<div align="center">

# Python Worker (NLP / Matching Microservice)

High‑throughput FastAPI service providing resume & job parsing, O\*NET enrichment, SBERT embeddings, optional Gemini augmentation, and match scoring utilities for the Layer1 backend.

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-ASGI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![spaCy](https://img.shields.io/badge/spaCy-NLP-09A3D5)](https://spacy.io/)
[![pdfminer.six](https://img.shields.io/badge/pdfminer.six-Extraction-FFB400)](https://github.com/pdfminer/pdfminer.six)
[![python-docx](https://img.shields.io/badge/python--docx-DOCX%20Parsing-295397)](https://python-docx.readthedocs.io/)
[![SentenceTransformers](https://img.shields.io/badge/SBERT-Embeddings-1B1F23)](https://www.sbert.net/)
[![O*NET](https://img.shields.io/badge/O*NET-Requirements-005A9C)](https://www.onetcenter.org/)
[![Gemini](https://img.shields.io/badge/Gemini-Optional%20LLM-4285F4?logo=googlecloud&logoColor=white)](https://ai.google.dev/)
[![Pytest](https://img.shields.io/badge/Pytest-Tests-0A9EDC?logo=pytest&logoColor=white)](https://pytest.org/)

</div>

## Role in the System

The Node.js backend (`../backend`) delegates computationally heavy steps here:

- PDF / DOCX extraction
- Section & skill detection (heuristics + pattern + optional Gemini)
- Job requirement extraction & O\*NET enrichment (technology + soft skills)
- Embedding generation (SBERT) with deterministic hash fallback
- Match scoring helpers consumed by queue workers / tests

Communication: direct HTTP (`PYTHON_SERVICE_URL`) from backend or invoked internally by queue processors.

Refer to `../layer1_backend_and_ai_design.md` for architecture details.

## Prerequisites

- Python 3.11+
- pip
- (Recommended) Virtual environment

## Setup (Windows)

1. Create and activate a virtual environment

- PowerShell:
  - python -m venv .venv
  - .\.venv\Scripts\Activate.ps1
- cmd:
  - python -m venv .venv
  - .\.venv\Scripts\activate.bat
- Bash (Git Bash on Windows):
  - python -m venv .venv
  - source .venv/Scripts/activate
- Bash (WSL/Linux shell):
  - python3 -m venv .venv
  - source .venv/bin/activate

2. Install dependencies

- python -m pip install -r requirements.txt
- Optional (needed for some NLP paths): python -m spacy download en_core_web_sm

3. Create a .env (optional, only if you want O\*NET enrichment / integration tests)

- Copy .env.example to .env if you have one, or create python-worker/.env with (minimum):
  - ONET_USER=your_user
  - ONET_PASSWORD=your_password
  - (Optional) ONET_USE_BRIGHT_OUTLOOK=true # default; set to false to disable enrichment
  - (Optional) ONET_BRIGHT_OUTLOOK_CATEGORY=grow # grow | rapid | new (defaults to grow)

O\*NET enrichment now always uses Bright Outlook occupation lists (paginated) to collect skills.
The legacy ONET_SKILL_CODES variable is deprecated and ignored.

### O\*NET Relevance Threshold

`ONET_MIN_RELEVANCE` (optional) controls filtering of O\*NET importance / relevance scores.

Allowed formats:

- Value 0–1: treated as already normalized (e.g. `0.7`).
- Value >1: interpreted as 0–100 scale and divided by 100 (e.g. `70` -> `0.70`).
- Missing / <=0 / invalid: no extra importance filtering (all returned by API kept).

Applied to:

- Technology skills (details/technology_skills) selection for explicit/inferred logic.
- Knowledge skills (details/knowledge or summary fallback) when technology pool is empty.
- Soft skills (details/skills) – separate soft skill threshold uses the same variable; default fallback is 0.50 when unset.

### Job Parsing Flow (Updated 2025-09)

1. Receive `title` + job description text (or uploaded file).
2. Title sanitization before O\*NET search:
   - Removes bracketed content: `(…)`, `[…]`, `{…}`.
   - Removes seniority / level tokens: junior, jr, senior, sr, intermediate, mid, lead, principal, staff, intern, internship, entry, entry-level, graduate.
   - Performs two O\*NET searches: full sanitized title (whitespace -> '+'), and (if multi-word) the last remaining token (e.g. “software engineer” => second query “engineer”).
3. Collect SOC codes from both queries (deduplicated, order preserved as discovered).
4. For each code fetch Technology Skills (`details/technology_skills`) and filter items by threshold.
5. If (and only if) every code yields zero technology skills above threshold, fetch Knowledge (`details/knowledge` -> `summary/knowledge` fallback) and filter by threshold; this becomes the candidate pool instead.
6. Build explicit requirement matcher from (candidate pool skills ∪ static dictionary terms), then scan job text for occurrences (frequency -> importance 0.5–1 scaled).
7. Invoke Gemini (if configured) to extract additional explicit technologies (importance 1.0 or 0.8 for “optional / nice to have”); merge without duplicates.
8. Inferred requirements: remaining candidate pool skills not matched explicitly are appended with their O\*NET-derived importance (or synthesized fallback) and `inferred: true`.
9. Soft skills are always fetched independently from `details/skills` for each code (filtered by threshold or default 0.50) and deduplicated (max importance retained).
10. Response returns ONLY plural keys: `requirements`, `soft_skills`. (Removed: `summary`, `highlights`, `onet` block.)

### /parse/job Response Schema (Current)

```
POST /parse/job -> {
  "requirements": [
    { "skill": "python", "importance": 0.93, "inferred": false },
    { "skill": "aws", "importance": 0.88, "inferred": false },
    { "skill": "distributed systems", "importance": 0.72, "inferred": true }
  ],
  "soft_skills": [
    { "skill": "communication", "value": 0.74 },
    { "skill": "teamwork", "value": 0.69 }
  ]
}
```

Notes:

- `importance` ∈ (0,1] for requirements (explicit or inferred). Explicit scores are frequency scaled; inferred use O\*NET (or synthesized) importance.
- `inferred` distinguishes items coming from O\*NET enrichment (not directly matched in the text) vs explicit textual matches / Gemini extraction.
- Soft skill objects use `value` to reflect normalized importance; no `inferred` flag.

### Backward Compatibility

If older clients expect `summary`, `highlights`, or an `onet` object, they will break. Add a shim layer or restore legacy fields at the API edge if required. At present the service intentionally omits them to simplify payloads.

Note: tests auto-load python-worker/.env via tests/conftest.py on every pytest run (session start), not just the first time. If you change .env, just re-run pytest. Existing OS env vars take precedence over .env because load_dotenv is called with override=False.

## Run tests

- All tests (unit + any integration tests that aren’t skipped):

  - python -m pytest

- Unit tests only (skip O\*NET integration tests):

  - python -m pytest -m "not integration"
  - Bash note: if quoting issues occur, use single quotes: python -m pytest -m 'not integration'

- Integration tests only (require ONET_USER and ONET_PASSWORD set in .env):
  - python -m pytest -m integration

## Re-using the environment next time

- Do NOT recreate the venv each time. Just activate it again:
  - PowerShell: .\.venv\Scripts\Activate.ps1
  - cmd: .\.venv\Scripts\activate.bat
  - Bash (Git Bash): source .venv/Scripts/activate
  - Bash (WSL): source .venv/bin/activate
- Re-install deps only if requirements.txt changed.

## How to check if the venv is active (Windows)

- Prompt usually shows (venv) prefix.
- Python path should point into .venv:
  - python -c "import sys; print(sys.executable)" -> should end with .venv\\Scripts\\python.exe (Git Bash) or .venv/bin/python (WSL)
- Pip should also point into .venv:
  - python -m pip -V -> look for .venv in the path
- PowerShell: $env:VIRTUAL_ENV should be set to the .venv path when active.
- Bash: echo "$VIRTUAL_ENV" should print the .venv path when active.

## Optional: Gemini Technology Extraction (Jobs + Resumes)

If you provide a Google Gemini API key, the job parser will invoke the model to extract explicit technology / tool names (languages, frameworks, databases, cloud platforms, ML / data / DevOps tools) from each job description. Extracted technologies are merged into the `requirements` list (marked as explicit, not `inferred`).

As of 2025-10 the resume parser also (optionally) calls Gemini with the full resume text. Returned items are merged with dictionary-matched resume skills. Each merged resume skill now includes a `source` array (e.g. `["matcher", "gemini"]`) and, when applicable, a `gemini_importance` field capturing the model-provided importance (1.0 or 0.8). Baseline matcher statistics still drive `experience_years`; Gemini does not infer years.

Environment variables:

```
GEMINI_API_KEY=your_key_here
# Optional override (defaults to gemini-1.5-flash):
GEMINI_MODEL=gemini-1.5-flash
# Control whether inferred (O*NET-only) requirements contribute to final match score.
# Default: false (only explicit textual / Gemini extracted requirements count toward score).
# When set truthy (1, true, yes, on) inferred requirements are blended with a 20% cap.
USE_INFERRED_REQUIREMENTS=false
```

Installation (already in requirements.txt as optional dependency):

```
python -m pip install -r requirements.txt
```

Behavior:

1. A single prompt is sent containing the job (or resume) text (truncated to 15k chars).
2. Model must return strict JSON array: `[ {"skill": "python", "importance": 1.0}, ... ]`.
3. Importance = 0.8 when the mention is clearly optional (phrases like "nice to have", "preferred", "a plus", "bonus", "optional"). Otherwise 1.0.
4. Soft skills or vague phrases (e.g. "team player", "fast learner") are excluded by the prompt instructions.
5. Any failure (auth, quota, JSON parse) is logged and silently ignored; the pipeline falls back to dictionary + O\*NET only.

Free tier note: Google has (historically) offered limited free usage for certain Gemini models (e.g. 1.5 Flash). Pricing / quotas can change; verify current terms before relying on free calls in production.

Disable by removing `GEMINI_API_KEY` (no code changes needed) or uninstalling the optional dependency.

## Changelog

- 2025-09: Simplified /parse/job response (removed summary/highlights/onet). Added technology-first O\*NET logic with knowledge fallback, title sanitization, unified relevance threshold, soft skills always included. Added job parsing flow docs.
- 2025-10: Added optional Gemini augmentation for resume parsing; resume skills now include provenance (`source`) and optional `gemini_importance`.
- 2025-10: Introduced `USE_INFERRED_REQUIREMENTS` flag (default false) to ignore inferred requirements in final match scoring unless explicitly enabled.

## Troubleshooting

- O\*NET tests are marked with the `integration` marker and are skipped if ONET_USER/ONET_PASSWORD are not configured.
- If you see spaCy model errors locally, run: `python -m spacy download en_core_web_sm`.
- You can run pytest from layer1 (root) or from python-worker; .env is located by tests/conftest.py regardless of the working directory.

---

## Docker / Compose Usage

The worker is referenced by `backend/docker-compose.yml` as service `python_worker`.

Standalone build & run:

```bash
docker build -t layer1-python-worker .
docker run --rm -p 8000:8000 --env-file .env layer1-python-worker
```

Via existing compose (from `backend` directory):

```bash
docker compose up -d python_worker
```

Check readiness at `http://localhost:8000/docs` (FastAPI Swagger UI) or a `/health` endpoint if implemented.

## Environment Highlights

| Variable                  | Purpose                                           | Notes                                     |
| ------------------------- | ------------------------------------------------- | ----------------------------------------- |
| ONET_USER / ONET_PASSWORD | O\*NET API credentials                            | Needed for enrichment & integration tests |
| ONET_MIN_RELEVANCE        | Filter importance / relevance                     | 0–1 or 0–100 accepted                     |
| GEMINI_API_KEY            | Enable Gemini technology extraction               | Omit to disable                           |
| GEMINI_MODEL              | Override default Gemini model                     | Optional                                  |
| USE_INFERRED_REQUIREMENTS | Include inferred requirements in scoring (capped) | Mirrors backend flag                      |

## Dev Tips

- Run `pytest -m 'not integration'` for fast feedback loops.
- First run downloads spaCy model / SBERT – subsequent starts are faster.
- Hash fallback activates automatically if transformer load fails (logged warning).

## Cross References

- Backend Orchestrator: `../backend/README.md`
- System Design: `../layer1_backend_and_ai_design.md`

---

_README enhanced 2025-10-02: added badges, role overview, docker usage, env highlights._
