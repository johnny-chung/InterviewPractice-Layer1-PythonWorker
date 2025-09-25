"""Thin O*NET client providing optional requirement enrichment."""

import logging
import os
from typing import List, Optional, Dict, Tuple, Iterable

import requests

logger = logging.getLogger(__name__)

ONET_ENDPOINT = 'https://services.onetcenter.org/ws/online'


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
    legacy-like `skills` arrays if encountered.
    """
    results: List[Dict] = []

    # Preferred: element-based structures under various wrappers
    element_lists = _element_lists_from_payload(data)
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
    - 1-2 -> 100
    - 3-4 -> 90
    - 5-6 -> 80
    ... down by 10 every pair, minimum 10
    Returns normalized score in [0.1, 1.0].
    """
    try:
        bucket = max(1, (int(n) + 1) // 2)  # 1 for 1-2, 2 for 3-4, etc.
        raw = max(10, 110 - bucket * 10)
    except Exception:
        raw = 10
    return raw / 100.0


def _parse_technology_payload(data: Dict) -> List[Dict]:
    """Parse Technology Skills summary, returning 0-1 normalized importance.

    - Adds a skill for each technology category name with importance 1.0.
    - Adds a skill for each example marked hot_technology with tiered score
      based on the length of the example array (see _tiered_score_for_examples_length).
    """
    results: List[Dict] = []

    element_lists = _element_lists_from_payload(data)
    for elements in element_lists:
        for el in elements or []:
            # Category name itself as a skill at 1.0
            name = el.get('name') or el.get('element_name')
            if name:
                results.append({'skill': name, 'importance': 1.0, 'source': 'onet'})

            # Examples (e.g., software/tools), include only those flagged hot_technology
            examples = el.get('example') or el.get('examples') or []
            if isinstance(examples, list) and examples:
                n = len(examples)
                tiered = _tiered_score_for_examples_length(n)
                for ex in examples:
                    ex_name = ex.get('name') or ex.get('element_name')
                    hot = ex.get('hot_technology')
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


def fetch_onet_skills(code: str) -> List[Dict]:
    """Fetch skill/importance records for a SOC code, scaled to 0-1.

    Uses the documented O*NET OnLine endpoints:
    - Details: /occupations/{code}/details/skills (includes Importance scores)
    - Summary: /occupations/{code}/summary/skills (names/desc; may omit scores)
    - Technology: /occupations/{code}/summary/technology (technology skills and examples)

    All O*NET-derived importances are additionally weighted by 0.5 to be
    less important than job description extracted skills.
    """
    user, password = _credentials()
    if not (user and password and code):
        return []

    auth = (user, password)
    aggregated: List[Dict] = []

    # Prefer details endpoint (provides Importance scores). Request full list.
    details_url = f"{ONET_ENDPOINT}/occupations/{code}/details/skills?display=long"
    data = _get_json(details_url, auth)
    if data:
        skills = _parse_skills_payload(data)
        if skills:
            logger.info('O*NET skills fetched from %s for code=%s: %d items', details_url, code, len(skills))
            aggregated.extend(skills)

    # Fallback: summary endpoint (top descriptors; may not include scores)
    if not aggregated:
        summary_url = f"{ONET_ENDPOINT}/occupations/{code}/summary/skills?display=long"
        data = _get_json(summary_url, auth)
        if data:
            skills = _parse_skills_payload(data)
            if skills:
                logger.info('O*NET skills fetched from %s for code=%s: %d items', summary_url, code, len(skills))
                aggregated.extend(skills)
            else:
                logger.info('O*NET summary skills returned no items for code=%s', code)

    # Technology Skills (always attempt; additive)
    tech_url = f"{ONET_ENDPOINT}/occupations/{code}/summary/technology?display=long"
    data = _get_json(tech_url, auth)
    if data:
        tech_skills = _parse_technology_payload(data)
        if tech_skills:
            logger.info('O*NET technology skills fetched from %s for code=%s: %d items', tech_url, code, len(tech_skills))
            aggregated.extend(tech_skills)

    if not aggregated:
        logger.warning('O*NET skills fetch returned no data for code=%s (tried %s, %s, %s)', code, details_url, f"{ONET_ENDPOINT}/occupations/{code}/summary/skills?display=long", tech_url)
        return []

    # Dedupe and apply 0.5 weighting to all O*NET-derived importances
    deduped = _dedupe_max_importance(aggregated)
    for item in deduped:
        try:
            item['importance'] = float(item.get('importance', 0.0)) * 0.5
        except Exception:
            item['importance'] = 0.0
        item['source'] = 'onet'

    return deduped
