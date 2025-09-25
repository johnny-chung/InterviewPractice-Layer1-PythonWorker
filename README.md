# Python Worker

FastAPI service that parses resumes/jobs and computes matches.

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

3. Create a .env (optional, only if you want O\*NET integration tests)

- Copy .env.example to .env if you have one, or create python-worker/.env with:
  - ONET_USER=your_user
  - ONET_PASSWORD=your_password
  - ONET_SKILL_CODES=15-1252.00

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

## Troubleshooting

- O\*NET tests are marked with the `integration` marker and are skipped if ONET_USER/ONET_PASSWORD are not configured.
- If you see spaCy model errors locally, run: `python -m spacy download en_core_web_sm`.
- You can run pytest from layer1 (root) or from python-worker; .env is located by tests/conftest.py regardless of the working directory.
