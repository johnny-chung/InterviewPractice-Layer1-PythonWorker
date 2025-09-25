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

logger = logging.getLogger(__name__)


class JobParser:
    """Normalises job description text and produces requirement lists."""

    def __init__(self) -> None:
        """Initialise spaCy pipeline and seed phrase matcher with known skills."""
        self._nlp = get_nlp()
        self._skill_matcher = PhraseMatcher(self._nlp.vocab, attr='LOWER')
        # Load dynamic skill terms (O*NET if available, otherwise fallback list).
        docs = [self._nlp.make_doc(term) for term in get_skill_terms()]
        self._skill_matcher.add('SKILL', docs)

    def parse(
        self,
        data: bytes | None,
        text: str | None,
        filename: str | None,
        mime_type: str | None,
        title: str | None = None,
    ) -> Dict:
        """Extract structured requirements plus optional O*NET enrichment.

        Args:
            data: Optional uploaded file bytes.
            text: Raw job description text (preferred if present).
            filename: Original filename (used for type inference).
            mime_type: Client supplied MIME type.
            title: Job title hint for O*NET occupation lookup.
        Returns:
            Dict with keys: raw_text, requirements, summary, highlights, onet.
        """
        # Use provided raw text when available; otherwise decode file bytes.
        raw_text = text or self._extract_text(data, filename, mime_type)
        doc = self._nlp(raw_text)
        requirements = self._extract_requirements(doc)
        onet_details = {}
        # Augment requirements with O*NET data when credentials are configured.
        if onet_client.is_enabled():  # External API call is optional and controlled via env.
            code = onet_client.search_onet_code(title or doc[:10].text if len(doc) else '')
            if code:
                inferred = onet_client.fetch_onet_skills(code)
                if inferred:
                    existing = {req['skill'] for req in requirements}  # Avoid duplicate requirements when O*NET returns overlaps.
                    for item in inferred:
                        if item['skill'] and item['skill'].lower() not in existing:
                            requirements.append({
                                'skill': item['skill'],
                                'importance': item.get('importance', 0.5),
                                'inferred': True,
                            })
                    onet_details = {'code': code, 'skills': inferred}
        summary = {
            'sentence_count': len(list(doc.sents)) if doc.has_annotation('SENT_START') else 0,
            'requirements_count': len(requirements),
        }
        highlights = requirements[:5]
        return {
            'raw_text': raw_text,
            'requirements': requirements,
            'summary': summary,
            'highlights': highlights,
            'onet': onet_details,
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

    def _extract_requirements(self, doc) -> List[Dict]:
        """Count skill mentions and derive naive importance scores.

        Args:
            doc: spaCy Doc representing job text.
        Returns:
            List of requirement dicts {skill, importance, inferred} sorted by frequency.
        """
        matches = self._skill_matcher(doc)
        counter = Counter()
        for _match_id, start, end in matches:
            term = doc[start:end].text.lower()
            counter[term] += 1
        requirements = []
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
