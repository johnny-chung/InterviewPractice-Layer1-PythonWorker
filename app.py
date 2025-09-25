"""FastAPI surface for resume/job parsing and match computation.

Endpoints:
  GET  /health          -> Liveness probe (always 200 when service up)
  POST /parse/resume    -> Extract sections + skills from a single resume file (base64 payload)
  POST /parse/job       -> Extract requirements from job description (raw text or file)
  POST /match           -> Compute weighted match between candidate skills & requirements

General error semantics:
  400 invalid_base64 / content_required when input payload is malformed or missing
  422 FastAPI / Pydantic validation errors for schema violations
"""
import base64
import logging
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv(Path(__file__).resolve().parent / '.env')

from parsers.resume_parser import resume_parser
from parsers.job_parser import job_parser
from match_engine import calculate_match

logger = logging.getLogger(__name__)  # Shared logger for request handlers.
logging.basicConfig(level=logging.INFO)

app = FastAPI(title='Layer1 NLP Service', version='0.1.0')  # Keep version here to surface in docs.


class ResumePayload(BaseModel):
    """Resume parsing request body.

    content_b64: base64 encoded file bytes (txt/pdf/docx). Optional metadata influences parser
    heuristics (e.g., selecting PDF extraction path).
    """
    filename: Optional[str] = None
    mime_type: Optional[str] = None
    content_b64: str


class JobPayload(BaseModel):
    """Job parsing request body.

    Provide either content_b64 (file) or text (raw description). If both provided, text is used
    only as fallback when file decode fails.
    """
    filename: Optional[str] = None
    mime_type: Optional[str] = None
    content_b64: Optional[str] = None
    text: Optional[str] = None
    title: Optional[str] = None


class SkillItem(BaseModel):
    """Candidate skill entry supplied to /match.

    experience_years / proficiency are optional and currently informational (not used directly
    in scoring yet) but retained for future weighting strategies.
    """
    skill: str
    experience_years: Optional[int] = Field(default=None, ge=0)
    proficiency: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class RequirementItem(BaseModel):
    """Job requirement entry supplied to /match.

    importance: fractional weight (0-1) used in overall match aggregation.
    inferred: distinguishes parser vs external (e.g. O*NET) sourced requirements.
    """
    skill: str
    importance: Optional[float] = Field(default=0.5, ge=0.0, le=1.0)
    inferred: Optional[bool] = False


class MatchPayload(BaseModel):
    """Match computation request: lists of candidate skills and job requirements."""
    candidate_skills: List[SkillItem]
    requirements: List[RequirementItem]


@app.get('/health')
def health() -> dict:
    """Return simple liveness signal.

    Returns: { 'ok': True }
    """
    return {'ok': True}


@app.post('/parse/resume')
def parse_resume(payload: ResumePayload) -> dict:
    """Parse a resume file.

    Request: ResumePayload
    Success 200: { skills, sections, profile, statistics }
    Errors: 400 invalid_base64 when content_b64 is not valid base64.
    """
    try:
        data = base64.b64decode(payload.content_b64)
    except Exception as exc:
        logger.warning('Failed to decode resume payload: %s', exc)
        raise HTTPException(status_code=400, detail='invalid_base64')
    result = resume_parser.parse(data, payload.filename, payload.mime_type)
    return {
        'skills': result['skills'],
        'sections': result['sections'],
        'profile': result['profile'],
        'statistics': result['statistics'],
    }


@app.post('/parse/job')
def parse_job(payload: JobPayload) -> dict:
    """Parse job description file or raw text and derive requirement list.

    Request: JobPayload (must include content_b64 OR text)
    Success 200: { requirements, summary, highlights, onet }
    Errors: 400 invalid_base64 | content_required
    """
    data = None
    if payload.content_b64:
        try:
            data = base64.b64decode(payload.content_b64)
        except Exception as exc:
            logger.warning('Failed to decode job payload: %s', exc)
            raise HTTPException(status_code=400, detail='invalid_base64')
    if not data and not payload.text:
        raise HTTPException(status_code=400, detail='content_required')
    result = job_parser.parse(data, payload.text, payload.filename, payload.mime_type, payload.title)
    return {
        'requirements': result['requirements'],
        'summary': result['summary'],
        'highlights': result['highlights'],
        'onet': result['onet'],
    }


@app.post('/match')
def compute_match(payload: MatchPayload) -> dict:
    """Compute weighted match score.

    Aggregates requirement importance * similarity (exact-match gated) over all requirements.
    Returns: {'score': float, 'summary': {...}}
    """
    candidate_skills = [item.dict() for item in payload.candidate_skills]
    requirements = [item.dict() for item in payload.requirements]
    result = calculate_match(candidate_skills, requirements)  # Weighted similarity computed in match_engine.
    return result

