"""Resume parsing utilities for Layer1 NLP worker.

Normalises resumes, identifies logical sections, extracts skills, and estimates
experience so downstream services can persist structured data.
"""

import io
import logging
import re
from typing import Dict, List

from docx import Document
from pdfminer.high_level import extract_text
from spacy.matcher import Matcher, PhraseMatcher

from utils.skill_dictionary import SECTION_PATTERNS, get_skill_terms
from utils.spacy_loader import get_nlp
from utils import gemini_client  # optional: technology extraction (explicit resume skills)

logger = logging.getLogger(__name__)

# Regex to pull explicit "X years of experience with Y" style statements.
YEARS_PATTERN = re.compile(r'(\d+)\s+(?:years?|yrs?)(?:\s+of)?\s+(?:experience|exp\.?)(?:\s+with)?\s+([\w\-.+]+)', re.IGNORECASE)
# Regex to estimate total experience from year ranges like "2018-2023" or "2016-present".
RANGE_PATTERN = re.compile(r'(\d{4})\s*[-\u2013\u2014]\s*(present|\d{4})', re.IGNORECASE)


class ResumeParser:
    """Wraps the resume parsing pipeline (text extraction, sections, skills)."""

    def __init__(self) -> None:
        """Initialise spaCy pipeline and reusable matchers."""
        self._nlp = get_nlp()
        self._section_matcher = self._build_section_matcher()
        self._skill_matcher = self._build_skill_matcher()

    def parse(self, data: bytes, filename: str | None, mime_type: str | None) -> Dict:
        """Convert raw resume bytes into structured sections, skills, and stats.

        Now also (optionally) calls Gemini to extract explicit technology / tool
        skills from the *entire* resume text (similar to job parsing flow).
        Gemini skills are merged (deduped) with dictionary / matcher extracted
        skills. When Gemini disabled or fails, behaviour is unchanged.

        Returns keys:
            raw_text, sections, skills (merged), statistics, profile, sources (per-skill provenance)
        """
        text = self._extract_text(data, filename, mime_type)
        doc = self._nlp(text)
        sections = self._identify_sections(doc)
        baseline_skills = self._extract_skills(doc)  # matcher-based (dictionary)

        # Optional Gemini extraction (treated as explicit stated skills; not inferred)
        gemini_skills: List[Dict] = []
        if gemini_client.is_enabled():  # pragma: no branch simple guard
            try:
                # Reuse job prompt style but adjust instruction phrase.
                # We call the same extract_technologies (instructions mention job description)
                # but empirically it's generic enough; we could adapt by prefixing text.
                gemini_skills = gemini_client.extract_technologies(f"Resume text:\n{text[:15000]}")
                if gemini_skills:
                    logger.info('resume_parser.parse: gemini extracted count=%d', len(gemini_skills))
            except Exception as exc:  # pragma: no cover
                logger.warning('resume_parser.parse: gemini extraction failed: %s', exc)
        else:
            logger.debug('resume_parser.parse: gemini disabled (is_enabled()=False)')

        # Merge baseline + gemini by skill name (case-insensitive); keep earliest experience_years if present
        merged: Dict[str, Dict] = {}
        for item in baseline_skills:
            key = item['skill'].lower()
            merged[key] = {**item, 'source': ['matcher']}
        for g in gemini_skills:
            g_skill = g.get('skill')
            if not g_skill:
                continue
            key = g_skill.lower()
            if key in merged:
                # Augment source + possibly elevate experience_years if Gemini implied none
                merged[key]['source'].append('gemini')
                # If Gemini importance differs and we don't already store one, record under gemini_importance
                if 'gemini_importance' not in merged[key] and g.get('importance') is not None:
                    merged[key]['gemini_importance'] = g.get('importance')
            else:
                merged[key] = {
                    'skill': key,
                    'experience_years': None,
                    'proficiency': None,
                    'mentions': 0,
                    'source': ['gemini'],
                    'gemini_importance': g.get('importance')
                }

        merged_list = list(merged.values())

        stats = {
            'characters': len(text),
            'tokens': len(doc),
            'skills_detected': len(merged_list),
            'skills_gemini': len(gemini_skills),
        }
        profile = {
            'summary': sections.get('SUMMARY', '')[:500],
            'total_experience_years': self._estimate_total_years(text),
        }
        return {
            'raw_text': text,
            'sections': sections,
            'skills': merged_list,
            'statistics': stats,
            'profile': profile,
        }

    def _extract_text(self, data: bytes, filename: str | None, mime_type: str | None) -> str:
        """Extract plain text from PDF, DOCX, or fall back to UTF-8 decoding.

        Args:
            data: Raw file bytes.
            filename: Original filename.
            mime_type: Client supplied MIME.
        Returns:
            Extracted UTF-8 text (best effort). Empty string when data missing.
        """
        # Guard against empty uploads to avoid downstream errors.
        if not data:
            return ''
        lower_name = (filename or '').lower()
        # Try PDF extraction first when the MIME type (or extension) suggests a PDF.
        if mime_type == 'application/pdf' or lower_name.endswith('.pdf'):
            try:
                with io.BytesIO(data) as fh:
                    return extract_text(fh)
            except Exception as exc:  # pragma: no cover
                logger.warning('PDF extraction failed: %s', exc)
                return data.decode('utf-8', errors='ignore')
        # Decode DOC/DOCX using python-docx when detected via extension.
        if lower_name.endswith(('.doc', '.docx')):
            try:
                document = Document(io.BytesIO(data))
                return '\n'.join(p.text for p in document.paragraphs)
            except Exception as exc:  # pragma: no cover
                logger.warning('DOCX extraction failed: %s', exc)
                return data.decode('utf-8', errors='ignore')
        # Fall back to UTF-8 for plaintext or unknown formats.
        return data.decode('utf-8', errors='ignore')

    def _build_section_matcher(self) -> Matcher:
        """Build spaCy matcher that recognises section headings (Experience, Skills, etc.)."""
        matcher = Matcher(self._nlp.vocab)
        # Register phrase patterns (per SECTION_PATTERNS) to map headings to labels.
        for section, phrases in SECTION_PATTERNS.items():
            patterns = []
            for phrase in phrases:
                tokens = [{'LOWER': token.lower()} for token in phrase.split()]
                patterns.append(tokens)
            matcher.add(section, patterns)
        return matcher

    def _build_skill_matcher(self) -> PhraseMatcher:
        """Build phrase matcher seeded with known skills/technologies."""
        matcher = PhraseMatcher(self._nlp.vocab, attr='LOWER')
        # Skills are loaded dynamically (O*NET or fallback list).
        docs = [self._nlp.make_doc(term) for term in get_skill_terms()]
        matcher.add('SKILL', docs)
        return matcher

    def _identify_sections(self, doc) -> Dict[str, str]:
        """Slice the resume into named sections based on heading matches.

        Args:
            doc: spaCy Doc.
        Returns:
            Mapping of SECTION_NAME -> text content (excludes heading of next section).
        """
        # Sort matches to walk the document from top to bottom.
        matches = sorted(self._section_matcher(doc), key=lambda match: match[1])
        sections: Dict[str, str] = {}
        if not matches:
            return sections
        for idx, (match_id, start, _end) in enumerate(matches):
            section_name = doc.vocab.strings[match_id]
            next_start = matches[idx + 1][1] if idx + 1 < len(matches) else len(doc)
            span = doc[start:next_start]
            sections[section_name] = span.text.strip()
        return sections

    def _extract_skills(self, doc) -> List[Dict]:
        """Return deduplicated skill hits with optional experience estimates.

        Args:
            doc: spaCy Doc.
        Returns:
            List of { skill, experience_years?, proficiency?, mentions }.
        """
        found: Dict[str, Dict] = {}
        for _match_id, start, end in self._skill_matcher(doc):
            term = doc[start:end].text.lower()
            # Grab nearby tokens to check for "X years" statements.
            snippet = doc[max(0, start - 5):min(len(doc), end + 5)].text
            years = self._extract_years_from_snippet(snippet, term)
            record = found.setdefault(
                term,
                {'skill': term, 'experience_years': None, 'proficiency': None, 'mentions': 0},
            )
            if years is not None:
                record['experience_years'] = max(record['experience_years'] or 0, years)
            record['mentions'] += 1  # Track how often the skill appears for weighting.
        return list(found.values())

    def _extract_years_from_snippet(self, snippet: str, term: str) -> int | None:
        """Look for explicit "X years" phrases near a detected skill mention.

        Args:
            snippet: Local window of text around skill.
            term: Normalised skill token.
        Returns:
            Integer years if detected else None.
        """
        for match in YEARS_PATTERN.finditer(snippet):
            years_str, skill_term = match.groups()
            # Only treat the hit as relevant if it references the current skill.
            if term.split()[0] in skill_term.lower():
                try:
                    return int(years_str)
                except ValueError:
                    continue
        return None

    def _estimate_total_years(self, text: str) -> int | None:
        """Coarsely estimate aggregate experience from year ranges in the resume.

        Args:
            text: Full resume text.
        Returns:
            Heuristic total years (sum but at least max span) or None if undetectable.
        """
        matches = RANGE_PATTERN.findall(text)
        years: List[int] = []
        for start, end in matches:
            try:
                start_year = int(start)
                end_year = int(end) if end.lower() != 'present' else 2024
                years.append(max(0, end_year - start_year))
            except Exception:  # pragma: no cover
                continue
        if years:
            aggregate = sum(years)
            return max(aggregate, max(years))
        return None


resume_parser = ResumeParser()
