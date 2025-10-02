"""Thin O*NET client providing optional requirement enrichment."""

import logging
import os
import re
from functools import lru_cache
from typing import List, Optional, Dict, Tuple, Iterable

import requests

logger = logging.getLogger(__name__)

ONET_ENDPOINT = 'https://services.onetcenter.org/ws/online'


def _importance_threshold() -> Optional[float]:
    """Return normalized (0-1) importance threshold from ONET_MIN_RELEVANCE.

    Env semantics:
      - If unset / invalid => None (no additional filtering beyond legacy logic)
      - If value > 1 (assumed 0-100 scale) it's divided by 100
      - If 0 < value <= 1 interpreted already normalized
      - If value <= 0 ignored
    """
    raw = os.getenv('ONET_MIN_RELEVANCE', '').strip()
    if not raw:
        return None
    try:
        val = float(raw)
    except Exception:
        return None
    if val <= 0:
        return None
    if val > 1:
        val = val / 100.0
    # Clamp
    val = max(0.0, min(1.0, val))
    return val


def _credentials() -> Tuple[Optional[str], Optional[str]]:
    """Return configured credentials or (None, None) when unset."""
    user = os.getenv('ONET_USER')
    password = os.getenv('ONET_PASSWORD')
    if user and password:
        return user, password
    return None, None


def _headers() -> Dict[str, str]:
    """Default headers for O*NET requests (JSON, descriptive UA)."""
    return {
        'Accept': 'application/json',
        'User-Agent': 'skill_search'
    }


def is_enabled() -> bool:
    """Signal whether downstream callers should attempt O*NET lookups."""
    user, password = _credentials()
    return bool(user and password)


def search_onet_code(job_title: str) -> Optional[str]:
    """Search O*NET for the closest occupation code to the provided title."""
    user, password = _credentials()
    if not (user and password and job_title):
        return None
    try:
        resp = requests.get(
            f'{ONET_ENDPOINT}/search',
            params={'keyword': job_title, 'start': 1, 'end': 1},
            auth=(user, password),
            headers=_headers(),
            timeout=5
        )
        # Handle 200 OK and 422 Unprocessable Entity with JSON error body
        if resp.status_code not in (200, 422):
            logger.warning('O*NET search failed: HTTP %s for title=%r', resp.status_code, job_title)
            return None
        data = resp.json()
        if resp.status_code == 422:
            logger.warning('O*NET search validation error for title=%r: %s', job_title, data.get('error'))
            return None
        occupations = data.get('occupation') or []
        if occupations:
            code = occupations[0].get('code')
            if code:
                logger.info('O*NET search resolved title=%r to code=%s', job_title, code)
                return code
        logger.info('O*NET search returned no matches for title=%r', job_title)
    except Exception as exc:
        logger.warning('O*NET search failed: %s', exc)
    return None


def search_onet_codes_multi(job_title: str, min_score: int = 75, max_pages: int = 1) -> List[str]:
    """Search O*NET for occupation codes using ONLY the sanitized full title.

    Revision (2025-10 user request): Previous behaviour also executed a fallback
    search on the final remaining token (e.g., just "engineer"). This produced
    overly broad matches. We now restrict queries to the single sanitized full
    title string after removing bracketed content and level / seniority tokens.

    Sanitization steps:
      * Remove bracketed content: (), [], {}
      * Remove level / seniority / contract or seasonal tokens (junior, senior, lead, intern, etc.)
      * Remove year-like tokens 2000-2099
      * Tokenize on whitespace and common punctuation delimiters [\s/,+-]+

    Args:
        job_title: Raw job title string.
        min_score: Default minimum relevance (0-100) for relevance_score (env override allowed).
        max_pages: Reserved (unused) for future pagination (kept for API stability).
    Returns:
        Ordered list of distinct SOC codes matched from the sanitized title.
    """
    # Allow environment override of min relevance threshold
    try:
        env_min = int(os.getenv('ONET_MIN_RELEVANCE', '').strip())
        if env_min > 0:
            min_score = env_min
    except Exception:
        pass

    user, password = _credentials()
    if not (user and password and job_title):
        return []
    auth = (user, password)

    level_tokens = {
        'junior', 'jr', 'jr.', 'junior-level', 'senior', 'sr', 'sr.', 'senior-level', 'intermediate', 'mid', 'associate', 'new', 'grad', 'grads',
        'mid-level', 'lead', 'principal', 'staff', 'intern', 'intern,', 'internship', 'entry', 'entry-level', 'graduate', 'i', 'ii', 'iii', 'iv', 'v',
        'co-op', 'trainee', 'apprentice', 'summer', 'winter', 'fall', 'month', 'months', '1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12',
        'seeking', 'contract', 'full-time', 'part-time', 'temporary', 'permanent', 'toronto',
        "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
        "january", "february", "march", "april", "june", "july", "august", "september", "october", "november", "december"
    }

    # Remove bracketed content
    cleaned = re.sub(r'\([^)]*\)|\[[^\]]*\]|\{[^}]*\}', ' ', job_title or ' ')
    # Tokenize simple split; remove punctuation adornments
    # Split on whitespace and punctuation delimiters (space, '/', ',', '+', '-')
    raw_tokens = [t.strip().lower() for t in re.split(r'[\s/,+-]+', cleaned) if t.strip()]
    year_pattern = re.compile(r'^20\d{2}$')  # Matches 2000-2099
    filtered_tokens = [t for t in raw_tokens if t not in level_tokens and not year_pattern.match(t)]
    sanitized_full = ' '.join(filtered_tokens).strip()
    if not sanitized_full:
        sanitized_full = (job_title or '').strip()

    # Only one query now: the sanitized full title tokens joined by '+' for API
    queries: List[str] = []
    if sanitized_full:
        queries.append('+'.join(sanitized_full.split()))

    seen: set[str] = set()
    ordered: List[str] = []

    def run_query(q: str, label: str):
        try:
            resp = requests.get(
                f"{ONET_ENDPOINT}/search",
                params={'keyword': q, 'start': 1, 'end': 20},
                auth=auth,
                headers=_headers(),
                timeout=5,
            )
            if resp.status_code not in (200, 422):
                logger.debug('O*NET multi-search(%s) query=%r HTTP %s', label, q, resp.status_code)
                return
            data = resp.json()
            if resp.status_code == 422:
                logger.debug('O*NET multi-search(%s) validation error query=%r: %s', label, q, data.get('error'))
                return
            for occ in data.get('occupation') or []:
                code = occ.get('code')
                if not code:
                    continue
                score = occ.get('relevance_score')
                if score is None or score >= min_score:
                    if code not in seen:
                        seen.add(code)
                        ordered.append(code)
        except Exception as exc:
            logger.debug('O*NET multi-search(%s) failed query=%r: %s', label, q, exc)

    for q in queries:
        run_query(q, 'full')

    if ordered:
        logger.info('O*NET title search resolved sanitized=%r to %d codes (threshold=%d): %s', sanitized_full, len(ordered), min_score, ', '.join(ordered))
    else:
        logger.info('O*NET title search found no codes for sanitized=%r (threshold=%d)', sanitized_full, min_score)
    return ordered


def _element_lists_from_payload(data: Dict) -> List[List[Dict]]:
    """Collect candidate element arrays from a variety of response shapes.

    Responses may be shaped as:
    - { element: [...] }
    - { report: { element: [...] } }
    - { report: { category: [{ element: [...] }, ...] } }
    - { summary: { skills: { element: [...] } } }
    - { details: { skills: { element: [...] } } }
    - { skills: [...] }  # legacy
    """
    candidates: List[List[Dict]] = []

    def add_candidate(obj: Optional[Dict]):
        if isinstance(obj, dict):
            elems = obj.get('element')
            if isinstance(elems, list):
                candidates.append(elems)

    # direct
    add_candidate(data)

    # common wrappers
    for key in ('report', 'summary', 'details'):
        wrapper = data.get(key)
        if isinstance(wrapper, dict):
            add_candidate(wrapper)
            # nested skills object
            skills = wrapper.get('skills')
            if isinstance(skills, dict):
                add_candidate(skills)
            # nested categories/groups that hold element arrays
            for cat_key in ('category', 'categories', 'groups'):
                cats = wrapper.get(cat_key)
                if isinstance(cats, list):
                    for cat in cats:
                        add_candidate(cat)

    return candidates


def _parse_skills_payload(data: Dict) -> List[Dict]:
    """Parse skills from summary/details payloads, normalizing importance 0-1.

    Supports both modern summary/details format under `element` and
    legacy-like `skills` arrays if encountered. Applies optional importance
    threshold (env `ONET_MIN_RELEVANCE`) interpreted as 0-1 or 0-100.
    """
    results: List[Dict] = []

    # Preferred: element-based structures under various wrappers
    element_lists = _element_lists_from_payload(data)
    threshold = _importance_threshold()
    for elements in element_lists:
        for el in elements or []:
            name = el.get('name') or el.get('element_name')
            importance_val = None

            # 1) Details endpoints provide a score object: { "value": 75, ... }
            score_obj = el.get('score')
            if isinstance(score_obj, dict):
                importance_val = score_obj.get('value') if score_obj.get('value') is not None else score_obj.get('score')

            # 2) Some responses provide a data array with labeled items (e.g., Importance)
            if importance_val is None:
                for d in (el.get('data') or []):
                    label = (d.get('name') or d.get('label') or d.get('id') or '').lower()
                    if 'importance' in label or d.get('id') in ('IM', 'IMP'):
                        importance_val = d.get('value') if d.get('value') is not None else d.get('score')
                        break

            if name and importance_val is not None:
                try:
                    importance = float(importance_val) / 100.0
                except Exception:
                    continue
                if threshold is None or importance > threshold:
                    results.append({'skill': name, 'importance': importance, 'source': 'onet'})

    if results:
        return results

    # Fallback: legacy structure
    for s in data.get('skills', []) or []:
        name = s.get('element_name') or s.get('name')
        val = s.get('importance') if s.get('importance') is not None else s.get('score')
        if name and val is not None:
            try:
                importance = float(val) / 100.0
            except Exception:
                continue
            if threshold is None or importance > threshold:
                results.append({'skill': name, 'importance': importance, 'source': 'onet'})

    return results


def _get_json(url: str, auth: Tuple[str, str]) -> Optional[Dict]:
    """Helper to GET JSON with common headers and basic status handling."""
    try:
        resp = requests.get(url, auth=auth, headers=_headers(), timeout=5)
        if resp.status_code not in (200, 422):
            logger.info('O*NET request %s -> HTTP %s', url, resp.status_code)
            return None
        data = resp.json()
        if resp.status_code == 422:
            logger.warning('O*NET validation error for %s: %s', url, data.get('error'))
            return None
        return data
    except Exception as exc:
        logger.warning('O*NET request failed for %s: %s', url, exc)
        return None


def _tiered_score_for_examples_length(n: int) -> float:
    """Map example list length to a tiered 0-1 score, min 0.1.

    Tiers (pre-normalization):
    - 1-3 -> 100
    - 4-6 -> 90
    - 7-9 -> 80
    ... down by 10 every pair, minimum 50
    Returns normalized score in [0.1, 1.0].
    """
    try:
        bucket = max(1, (int(n) + 1) // 3)  # 1 for 1-3, 2 for 4-6, etc.
        raw = max(50, 110 - bucket * 10)
    except Exception:
        raw = 50
    return raw / 100.0


def _parse_technology_payload(data: Dict) -> List[Dict]:
    """Parse Technology Skills details payload.

    The details/technology_skills endpoint returns structure:
      { code, report:'details', category: [ { title:{name}, example:[ { name, hot_technology? }, ... ] }, ... ] }

    We treat each category title as importance 1.0 and each hot technology example as tiered.
    """
    results: List[Dict] = []
    categories = []
    # Direct category list on root or nested under 'report'
    if isinstance(data.get('category'), list):
        categories = data['category']
    elif isinstance(data.get('report'), dict) and isinstance(data['report'].get('category'), list):
        categories = data['report']['category']

    for cat in categories:
        # Category title object may be under 'title' with 'name'
        title_obj = cat.get('title') if isinstance(cat, dict) else None
        cat_name = None
        if isinstance(title_obj, dict):
            cat_name = title_obj.get('name') or title_obj.get('element_name')
        if not cat_name:
            cat_name = cat.get('name') or cat.get('element_name')
        if cat_name:
            results.append({'skill': cat_name, 'importance': 0.8, 'source': 'onet'})
        examples = cat.get('example') or cat.get('examples') or []
        if isinstance(examples, list) and examples:
            n = len(examples)
            tiered = _tiered_score_for_examples_length(n)
            for ex in examples:
                if not isinstance(ex, dict):
                    continue
                ex_name = ex.get('name') or ex.get('element_name')
                hot = ex.get('hot_technology')
                # Even if not hot, we could consider including; stick to hot for signal density.
                if ex_name and bool(hot):
                    results.append({'skill': ex_name, 'importance': tiered, 'source': 'onet'})
    return results


def _dedupe_max_importance(items: List[Dict]) -> List[Dict]:
    """Dedupe by skill name (case-insensitive), keeping max importance."""
    by_name: Dict[str, Dict] = {}
    for it in items:
        key = (it.get('skill') or '').strip().lower()
        if not key:
            continue
        if key not in by_name or (it.get('importance') or 0) > (by_name[key].get('importance') or 0):
            by_name[key] = it
    return list(by_name.values())


def _rank_based_fallback(elements: List[Dict]) -> List[Dict]:
    """Generate synthetic importance scores when API omits numeric fields.

    Applies a linear descending scale from 1.0 down to 0.5 across the ordered
    elements (minimum 0.5). This is only used when the knowledge endpoint
    returns descriptors without importance/score data (e.g., summary endpoint).
    """
    if not elements:
        return []
    n = len(elements)
    results: List[Dict] = []
    for idx, el in enumerate(elements):
        name = el.get('name') or el.get('element_name')
        if not name:
            continue
        # Linear interpolation: first element 1.0, last element 0.5
        if n == 1:
            importance = 1.0
        else:
            importance = 1.0 - (0.5 * (idx / (n - 1)))
        importance = max(0.5, min(1.0, importance))
        results.append({'skill': name, 'importance': importance, 'source': 'onet', 'synthetic': True})
    return results


@lru_cache(maxsize=512)
def fetch_onet_knowledge_and_technology(code: str) -> List[Dict]:
    """Fetch knowledge + technology items for requirements enrichment.

    Strategy:
    1. Attempt details/knowledge (richer, includes importance/level) with display=long.
    2. Fallback to summary/knowledge if details empty/unavailable.
    3. If either returns element descriptors lacking numeric importance, synthesize
       rank-based importance so descriptors still contribute signal.
    4. Merge with technology skills (details/technology_skills).
    """
    user, password = _credentials()
    if not (user and password and code):
        return []
    auth = (user, password)
    aggregated: List[Dict] = []

    # --- Knowledge (details preferred) ---
    knowledge_details_url = f"{ONET_ENDPOINT}/occupations/{code}/details/knowledge?display=long"
    data = _get_json(knowledge_details_url, auth)
    knowledge: List[Dict] = []
    raw_elements: List[Dict] = []
    if data:
        knowledge = _parse_skills_payload(data)
        raw_elements = data.get('element') or []
        if knowledge:
            logger.info('O*NET knowledge (details) fetched code=%s: %d (cacheable)', code, len(knowledge))
        elif raw_elements:
            # No numeric scores; synthesize
            knowledge = _rank_based_fallback(raw_elements)
            logger.info('O*NET knowledge (details) lacked numeric scores; synthesized %d items code=%s', len(knowledge), code)

    # Fallback to summary removed: previously fetched summary/knowledge when details empty.

    if knowledge:
        aggregated.extend(knowledge)

    # --- Technology skills (details endpoint) ---
    tech_url = f"{ONET_ENDPOINT}/occupations/{code}/details/technology_skills"
    data = _get_json(tech_url, auth)
    if data:
        tech = _parse_technology_payload(data)
        if tech:
            logger.info('O*NET technology (details) fetched code=%s: %d (cacheable)', code, len(tech))
            aggregated.extend(tech)
        else:
            logger.debug('Technology details returned no parsable items for code=%s', code)

    if not aggregated:
        return []

    deduped = _dedupe_max_importance(aggregated)
    for item in deduped:
        try:
            item['importance'] = float(item.get('importance', 0.0))
        except Exception:
            item['importance'] = 0.0
        item['source'] = 'onet'
    return deduped


def importance_threshold() -> Optional[float]:  # Public accessor for downstream logic
    """Expose current numeric importance threshold (0-1) or None if unset."""
    return _importance_threshold()


@lru_cache(maxsize=512)
def fetch_onet_technology_skills(code: str) -> List[Dict]:
    """Fetch ONLY technology skills for a SOC code.

    Returns list[{ skill, importance, source }] deduped. Importance values are
    those assigned during parsing (category ~0.8, examples tiered). Threshold is
    NOT applied here; callers decide how to filter vs fallback to knowledge.
    """
    user, password = _credentials()
    if not (user and password and code):
        return []
    auth = (user, password)
    tech_url = f"{ONET_ENDPOINT}/occupations/{code}/details/technology_skills"
    data = _get_json(tech_url, auth)
    if not data:
        return []
    tech = _parse_technology_payload(data)
    if not tech:
        return []
    deduped = _dedupe_max_importance(tech)
    for item in deduped:
        try:
            item['importance'] = float(item.get('importance', 0.0))
        except Exception:
            item['importance'] = 0.0
        item['source'] = 'onet'
    return deduped


@lru_cache(maxsize=512)
def fetch_onet_knowledge_skills(code: str) -> List[Dict]:
    """Fetch ONLY knowledge descriptors (details -> summary fallback) for a SOC code.

    Applies same synthesis logic as combined fetch, but does NOT include technology categories.
    """
    user, password = _credentials()
    if not (user and password and code):
        return []
    auth = (user, password)
    aggregated: List[Dict] = []
    # Details first
    details_url = f"{ONET_ENDPOINT}/occupations/{code}/details/knowledge?display=long"
    data = _get_json(details_url, auth)
    knowledge: List[Dict] = []
    raw_elements: List[Dict] = []
    if data:
        knowledge = _parse_skills_payload(data)
        raw_elements = data.get('element') or []
        if not knowledge and raw_elements:
            knowledge = _rank_based_fallback(raw_elements)
    if not knowledge:
        summary_url = f"{ONET_ENDPOINT}/occupations/{code}/summary/knowledge?display=long"
        data_sum = _get_json(summary_url, auth)
        if data_sum:
            parsed = _parse_skills_payload(data_sum)
            if parsed:
                knowledge = parsed
            else:
                elems = data_sum.get('element') or []
                if elems:
                    knowledge = _rank_based_fallback(elems)
    if not knowledge:
        return []
    for item in knowledge:
        try:
            item['importance'] = float(item.get('importance', 0.0))
        except Exception:
            item['importance'] = 0.0
        item['source'] = 'onet'
    return _dedupe_max_importance(knowledge)


@lru_cache(maxsize=512)
def fetch_onet_soft_skills(code: str) -> List[Dict]:
    """Fetch soft skills from details/skills endpoint applying threshold filter.

    Business rule: Include skills with normalized importance greater than:
      - ONET_MIN_RELEVANCE (interpreted as 0-1 or 0-100) when set and > 0, else
      - Default 0.50

    Returned objects: { skill, value }
    """
    user, password = _credentials()
    if not (user and password and code):
        return []
    auth = (user, password)

    details_url = f"{ONET_ENDPOINT}/occupations/{code}/details/skills?display=long"
    data = _get_json(details_url, auth)
    if not data:
        return []
    skills = _parse_skills_payload(data)
    soft: List[Dict] = []
    soft_threshold = _importance_threshold()
    if soft_threshold is None:
        soft_threshold = 0.5
    for item in skills:
        try:
            val = float(item.get('importance', 0) or 0.0)
        except Exception:
            continue
        if val > soft_threshold:
            soft.append({'skill': item.get('skill'), 'value': round(val, 2)})
    # Deduplicate by skill keeping max value
    deduped = _dedupe_max_importance([{'skill': s['skill'], 'importance': s['value']} for s in soft])
    return [{'skill': d['skill'], 'value': round(d.get('importance') or 0, 2)} for d in deduped]


def fetch_onet_skills(code: str) -> List[Dict]:
    """Alias for fetch_onet_knowledge_and_technology.

    The application treats knowledge + technology items as general 'skills'.
    Returns list[dict]: { skill, importance, source }
    """
    return fetch_onet_knowledge_and_technology(code)  # type: ignore  # noqa: F821


def fetch_bright_outlook_codes(category: str = 'grow', page_size: int = 20) -> List[str]:
    """Fetch all Bright Outlook occupation codes for a given category (e.g. 'grow').

    Iterates using start/end pagination until an empty page is returned.
    Returns list of SOC codes.
    """
    user, password = _credentials()
    if not (user and password):
        return []
    auth = (user, password)
    codes: List[str] = []
    start = 1
    while True:
        end = start + page_size - 1
        url = f"{ONET_ENDPOINT}/bright_outlook/{category}?start={start}&end={end}"
        data = _get_json(url, auth)
        if not data:
            break
        occs = data.get('occupation') or []
        if not occs:
            break
        for occ in occs:
            code = occ.get('code') or occ.get('occupation_code')
            if code:
                codes.append(code)
        if len(occs) < page_size:
            break  # Last page
        start = end + 1
        if start > 500:  # Safety guard against infinite loop
            break
    unique = sorted(set(codes))
    logger.info('Fetched %d Bright Outlook codes for category=%s', len(unique), category)
    return unique
