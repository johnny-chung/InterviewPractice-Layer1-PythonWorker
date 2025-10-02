"""Lightweight wrapper for Google Gemini extraction (technology skill list).

Features:
    * Optional activation (only when ``GEMINI_API_KEY`` present)
    * Strict JSON array parsing (silent fallback on malformed output)
    * Importance heuristic delegated to prompt (1.0 normal, 0.8 optional wording)
    * Uses the modern ``google-genai`` SDK only (legacy sdk removed).
    * Optional "thinking" (reasoning) budget for Gemini 2.5 models (disabled by default).

Environment variables:
    GEMINI_API_KEY           Required to enable calls.
    GEMINI_MODEL             Optional model name (default: gemini-1.5-flash).
    GEMINI_THINKING_BUDGET   Optional integer. If set and using *new* SDK + 2.5 model:
                                                     -1 => dynamic thinking, 0 => disabled, >0 => token budget.
                                                     For this simple extraction task a small or zero budget is usually fine.
    GEMINI_INCLUDE_THOUGHTS  If 'true', request thought summaries (only new SDK + 2.5 models).

Returned format from :func:`extract_technologies`:
    [ {"skill": "python", "importance": 1.0, "inferred": False}, ... ]

Design notes:
    * For a deterministic structured task (keyword-like extraction) enabling extensive
        thinking usually adds latency & token cost without major quality gain. Therefore
        thinking is *opt-in*.
    * Failures NEVER raise; they log & return an empty list so upstream parsing continues.
"""
from __future__ import annotations

import json
import logging
import os
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

###############################################################################
# SDK import (google-genai only)
###############################################################################
try:  # Modern SDK (2025+)
    from google import genai as genai_new  # type: ignore
    try:  # Types (may be absent on some versions)
        from google.genai import types as genai_types  # type: ignore
    except Exception:  # pragma: no cover
        genai_types = None  # type: ignore
except Exception:  # pragma: no cover
    genai_new = None  # type: ignore
    genai_types = None  # type: ignore

DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
THINKING_BUDGET: Optional[int] = None
if os.getenv("GEMINI_THINKING_BUDGET") not in (None, ""):
    try:
        THINKING_BUDGET = int(os.getenv("GEMINI_THINKING_BUDGET", "0").strip())
    except ValueError:  # pragma: no cover - invalid user input
        THINKING_BUDGET = None
INCLUDE_THOUGHTS = os.getenv("GEMINI_INCLUDE_THOUGHTS", "false").lower() == "true"

INSTRUCTIONS = (
    "You will be given a job description or resume. Extract every explicit technology, programming language, framework, library, database, cloud platform, devops tool, machine learning tool, or similar technical skill mentioned. "
    "Return ONLY valid JSON array of objects. Each object must have: skill (lowercase single term or phrase), importance (1.0 or 0.8). "
    "Rules: If the mention is clearly optional, phrased with adjectives like 'nice to have', 'preferred', 'a plus', 'bonus', 'optional', assign 0.8. Otherwise 1.0. Do not infer unstated technologies. Do not include soft skills or generic terms like 'team player'. Do not include versions."
)

JSON_REMINDER = (
    "Output strictly as JSON array, no markdown, no commentary. Example: [ {\"skill\": \"python\", \"importance\": 1.0} ]"
)


def _resolve_api_key() -> Optional[str]:
        """Return GEMINI_API_KEY only (single supported variable)."""
        return os.getenv("GEMINI_API_KEY")


def is_enabled() -> bool:
    """Return True if GEMINI_API_KEY and SDK are present."""
    return bool(_resolve_api_key()) and genai_new is not None


def extract_technologies(text: str) -> List[Dict]:
    """Return list of technology skill dicts extracted by Gemini.

    Thinking budget is applied only when an integer budget env var is provided
    (and a suitable model is used). Legacy SDK support removed.
    """
    if not text or not is_enabled():
        return []
    prompt = f"{INSTRUCTIONS}\n\n{JSON_REMINDER}\n\nTarget Text:\n{text[:15000]}"  # cap to avoid oversized payload

    try:
        if not is_enabled():
            return []
        # Resolve API key (single var).
        api_key = _resolve_api_key()
        if not api_key:
            logger.warning('gemini_client: no GEMINI_API_KEY set at call time')
            return []
        logger.debug('gemini_client: preparing request model=%s key_len=%d thinking=%s include_thoughts=%s',
                     DEFAULT_MODEL, len(api_key), THINKING_BUDGET, INCLUDE_THOUGHTS)
        client = genai_new.Client(api_key=api_key)  # type: ignore
        config = None
        if genai_types is not None and (THINKING_BUDGET is not None or INCLUDE_THOUGHTS):
            try:
                thinking_kwargs = {}
                if THINKING_BUDGET is not None:
                    thinking_kwargs['thinking_budget'] = THINKING_BUDGET
                if INCLUDE_THOUGHTS:
                    thinking_kwargs['include_thoughts'] = True
                config = genai_types.GenerateContentConfig(
                    thinking_config=genai_types.ThinkingConfig(**thinking_kwargs)
                )
            except Exception as exc:  # pragma: no cover
                logger.debug('gemini_client: unable to build thinking config: %s', exc)
                config = None
        if config is not None:
            response = client.models.generate_content(model=DEFAULT_MODEL, contents=prompt, config=config)
        else:
            response = client.models.generate_content(model=DEFAULT_MODEL, contents=prompt)
        raw = getattr(response, 'text', None)
        if not raw and getattr(response, 'candidates', None):  # type: ignore
            parts = []
            for c in response.candidates:  # type: ignore
                for p in getattr(c, 'content', {}).get('parts', []):
                    val = getattr(p, 'text', None)
                    if val:
                        parts.append(val)
            raw = '\n'.join(parts)

        if not raw:
            logger.info('gemini_client: empty model response')
            return []
        raw = raw.strip()
        if raw.startswith('```'):
            raw = raw.strip('`')
            if raw.lower().startswith('json\n'):
                raw = raw[5:]
        # Log a short preview of the raw model output for diagnostics (truncate to avoid log spam)
        logger.debug('gemini_client: raw response preview=%r len=%d', raw[:250], len(raw))
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning('gemini_client: JSON decode failed; raw length=%d', len(raw))
            return []
        if not isinstance(data, list):
            return []
        cleaned: List[Dict] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            skill = str(item.get('skill', '')).strip().lower()
            if not skill:
                continue
            imp = item.get('importance', 1.0)
            try:
                imp_f = float(imp)
            except Exception:
                imp_f = 1.0
            if imp_f > 1.0:
                imp_f = 1.0
            if imp_f < 0:
                imp_f = 0.0
            cleaned.append({'skill': skill, 'importance': round(imp_f, 2), 'inferred': False})
        if cleaned:
            # Log concise list of skills + importance values
            logger.info('gemini_client: extracted %d skills: %s', len(cleaned), [f"{c['skill']}({c['importance']})" for c in cleaned])
        else:
            logger.info('gemini_client: no skills extracted (empty list)')
        return cleaned
    except Exception as exc:  # pragma: no cover - network/runtime issues
        logger.warning('gemini_client: extraction failure: %s', exc)
        return []

# ---------------------------------------------------------------------------
# Module import side-effect logging: indicate enablement state early.
# ---------------------------------------------------------------------------
try:
    if _resolve_api_key():
        logger.info(
            "gemini_client: init: GEMINI_API_KEY present model=%s sdk_loaded=%s enabled=%s",
            DEFAULT_MODEL,
            genai_new is not None,
            is_enabled(),
        )
    else:
        logger.info("gemini_client: init: no GEMINI_API_KEY; disabled")
except Exception:  # pragma: no cover - defensive
    pass

if __name__ == "__main__":  # pragma: no cover - manual harness
    sample = "We use Python, Django, PostgreSQL and AWS. Nice to have: Redis."
    print(extract_technologies(sample))
