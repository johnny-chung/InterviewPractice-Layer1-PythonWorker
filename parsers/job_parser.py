"""Job parsing utilities: extracts explicit requirements and augments with O*NET."""

import io
import logging
from collections import Counter
from typing import Dict, List

from docx import Document
from pdfminer.high_level import extract_text
from spacy.matcher import PhraseMatcher

from utils.skill_dictionary import get_skill_terms
from utils.spacy_loader import get_nlp
from utils import onet_client
from utils import gemini_client  # optional technology extraction via Gemini

logger = logging.getLogger(__name__)


class JobParser:
    """Normalises job description text and produces requirement lists."""

    def __init__(self) -> None:
        """Initialise spaCy pipeline (skill matcher now built per-parse)."""
        self._nlp = get_nlp()

    def _build_matcher(self, terms: List[str]) -> PhraseMatcher:
        matcher = PhraseMatcher(self._nlp.vocab, attr='LOWER')
        docs = [self._nlp.make_doc(t) for t in sorted(set(terms)) if t]
        if docs:
            matcher.add('SKILL', docs)
        return matcher

    def parse(
        self,
        data: bytes | None,
        text: str | None,
        filename: str | None,
        mime_type: str | None,
        title: str | None = None,
    ) -> Dict:
        """Extract explicit + inferred requirements and soft skills.

          Flow (2025-09 clarified spec):
             1. Use title to search O*NET codes.
             2. For those codes attempt technology_skills (filtered by threshold). If NONE of the
                 codes yield technology skills above threshold, fallback to knowledge skills (filtered).
             3. Build explicit requirement matcher from (selected O*NET tech-or-knowledge list) UNION
                 fallback hardcoded dictionary terms. Extract matched terms from text.
             4. Ask Gemini for additional explicit requirements; merge (dedupe).
             5. Inferred requirements: remaining O*NET candidate skills (tech-or-knowledge set) that
                 were NOT explicitly matched.
             6. Soft skills: always fetched from O*NET details/skills (above threshold) for all codes.
             7. Output only: raw_text, requirements (explicit + inferred flagged), soft_skills.
          """
        # Use provided raw text when available; otherwise decode file bytes.
        raw_text = text or self._extract_text(data, filename, mime_type)
        doc = self._nlp(raw_text)
        soft_skills: List[Dict] = []
        requirements: List[Dict] = []  # explicit + inferred

        multi_codes: List[str] = []
        technology_candidates: List[Dict] = []
        knowledge_candidates: List[Dict] = []
        candidate_terms: List[str] = []
        soft_by_code: List[Dict] = []
        threshold = onet_client.importance_threshold() or 0.0
        if onet_client.is_enabled():
            title_seed = title or (doc[:10].text if len(doc) else '')
            multi_codes = onet_client.search_onet_codes_multi(title_seed)
            logger.info('job_parser.parse: title=%r codes=%s threshold=%.2f', title, multi_codes, threshold)
            for code in multi_codes:
                tech_items = [t for t in onet_client.fetch_onet_technology_skills(code) if (t.get('importance') or 0) >= threshold]
                if tech_items:
                    technology_candidates.extend(tech_items)
                else:
                    knowledge_items = [k for k in onet_client.fetch_onet_knowledge_skills(code) if (k.get('importance') or 0) >= threshold]
                    knowledge_candidates.extend(knowledge_items)
                soft = onet_client.fetch_onet_soft_skills(code)
                if soft:
                    soft_by_code.append({'code': code, 'items': soft})
            if technology_candidates:
                candidate_terms = [c.get('skill') for c in technology_candidates if c.get('skill')]
                logger.info('job_parser.parse: using technology skill candidate pool size=%d', len(candidate_terms))
            else:
                candidate_terms = [c.get('skill') for c in knowledge_candidates if c.get('skill')]
                if candidate_terms:
                    logger.info('job_parser.parse: technology empty; using knowledge candidate pool size=%d', len(candidate_terms))
            # Deduplicate candidate terms early
            candidate_terms = sorted({t.strip().lower() for t in candidate_terms if t})

        # Step 2: (Optional) Gemini technology extraction (model-driven explicit tech terms)
        gemini_requirements: List[Dict] = []
        if gemini_client.is_enabled():  # pragma: no branch - simple guard
            try:
                gemini_requirements = gemini_client.extract_technologies(raw_text)
                if gemini_requirements:
                    logger.info('job_parser.parse: gemini extracted tech_count=%d', len(gemini_requirements))
                    # Log full structured list (skills + importance) for traceability
                    logger.info('job_parser.parse: gemini items=%s', [f"{g.get('skill')}({g.get('importance')})" for g in gemini_requirements])
            except Exception as exc:  # pragma: no cover - safety net
                logger.warning('job_parser.parse: gemini extraction failed: %s', exc)
        else:
            logger.info('job_parser.parse: gemini disabled (is_enabled()=False)')

        # Step 3: Build matcher for dictionary / O*NET derived explicit terms
        global_terms = get_skill_terms()
        if candidate_terms:
            union_terms = sorted(set(candidate_terms) | set(global_terms))
            matcher = self._build_matcher(union_terms)
            requirements = self._extract_requirements(doc, matcher)
            if not requirements:
                matcher = self._build_matcher(global_terms)
                requirements = self._extract_requirements(doc, matcher)
        else:
            matcher = self._build_matcher(global_terms)
            requirements = self._extract_requirements(doc, matcher)

        # Merge Gemini extracted requirements (treat them as explicit, not inferred) without duplicates
        if gemini_requirements:
            existing_lower = {r['skill'].lower() for r in requirements}
            added_gemini = 0
            for item in gemini_requirements:
                skill = item.get('skill')
                if not skill:
                    continue
                if skill.lower() in existing_lower:
                    continue
                # Normalise structure to match requirements list shape
                requirements.append({
                    'skill': skill,
                    'importance': item.get('importance', 1.0),
                    'inferred': False,
                })
                existing_lower.add(skill.lower())
                added_gemini += 1
            if added_gemini:
                logger.info('job_parser.parse: merged gemini added=%d total_requirements=%d', added_gemini, len(requirements))

        # Step 4: Inferred requirements = remaining candidate skills not explicitly matched
        if candidate_terms:
            explicit_lower = {r['skill'].lower() for r in requirements}
            inferred_added = 0
            # Use the same candidate pool (technology if available else knowledge)
            source_pool = technology_candidates if technology_candidates else knowledge_candidates
            for item in source_pool:
                nm = (item.get('skill') or '').strip()
                if not nm:
                    continue
                if nm.lower() in explicit_lower:
                    continue
                requirements.append({
                    'skill': nm.lower(),
                    'importance': item.get('importance', 0.5),
                    'inferred': True,
                })
                explicit_lower.add(nm.lower())
                inferred_added += 1
            logger.info('job_parser.parse: inferred_added=%d total_requirements=%d', inferred_added, len(requirements))

        # Build flattened soft skills list (dedupe by name, keep max value)
        if soft_by_code:
            soft_accum = {}
            for entry in soft_by_code:
                for item in entry['items']:
                    nm = (item.get('skill') or '').strip()
                    if not nm:
                        continue
                    val = item.get('value')
                    key = nm.lower()
                    existing_val = soft_accum.get(key, {}).get('value')
                    if key not in soft_accum or (val is not None and (existing_val is None or val > existing_val)):
                        soft_accum[key] = {'skill': nm.lower(), 'value': val}
            soft_skills = list(soft_accum.values())
            logger.info('job_parser.parse: soft_skills_count=%d', len(soft_skills))
        return {
            'raw_text': raw_text,
            'requirements': requirements,
            'soft_skills': soft_skills,
        }

    def _extract_text(self, data: bytes | None, filename: str | None, mime_type: str | None) -> str:
        """Decode various file types into plain text for downstream NLP.

        Args:
            data: File bytes (may be None).
            filename: Original filename.
            mime_type: MIME type string.
        Returns:
            Best-effort decoded UTF-8 text ('' when no data provided).
        """
        if not data:
            return ''
        if mime_type == 'application/pdf' or (filename and filename.lower().endswith('.pdf')):
            try:
                with io.BytesIO(data) as fh:
                    return extract_text(fh)
            except Exception as exc:
                logger.warning('PDF extraction failed: %s', exc)
                return data.decode('utf-8', errors='ignore')
        if filename and filename.lower().endswith(('.doc', '.docx')):
            try:
                document = Document(io.BytesIO(data))
                return '\n'.join(p.text for p in document.paragraphs)
            except Exception as exc:
                logger.warning('DOCX extraction failed: %s', exc)
                return data.decode('utf-8', errors='ignore')
        return data.decode('utf-8', errors='ignore') if data else ''

    def _extract_requirements(self, doc, matcher: PhraseMatcher) -> List[Dict]:
        """Count skill mentions and derive naive importance scores.

        Args:
            doc: spaCy Doc representing job text.
        Returns:
            List of requirement dicts {skill, importance, inferred} sorted by frequency.
        """
        matches = matcher(doc)
        counter = Counter()
        for _match_id, start, end in matches:
            term = doc[start:end].text.lower()
            counter[term] += 1
        requirements: List[Dict] = []
        if not counter:
            return requirements
        max_freq = max(counter.values()) or 1  # Scale weights by most frequent skill.
        for term, freq in counter.most_common():
            score = 0.5 + 0.5 * (freq / max_freq)
            requirements.append({
                'skill': term,
                'importance': round(min(score, 1.0), 2),
                'inferred': False,
            })
        return requirements


job_parser = JobParser()
