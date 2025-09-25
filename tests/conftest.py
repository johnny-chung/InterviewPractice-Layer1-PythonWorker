# Load environment variables for tests from the project .env
from pathlib import Path
from dotenv import load_dotenv
import sys

# Resolve the repository root for the python-worker package
ROOT = Path(__file__).resolve().parents[1]

# Ensure local package modules are importable in tests
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DOTENV_PATH = ROOT / ".env"

# Load once on test session import
load_dotenv(dotenv_path=DOTENV_PATH, override=False)
