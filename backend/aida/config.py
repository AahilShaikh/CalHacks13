"""Local configuration loaded before the agent starts."""
from pathlib import Path

from dotenv import load_dotenv

# backend/aida/config.py -> repository root
REPO_ROOT = Path(__file__).resolve().parents[2]


def load_local_env():
    """Load .env without overriding shell variables: the working directory's first,
    then the repository root's, so commands work from any folder (e.g. frontend/)."""
    load_dotenv(dotenv_path=Path.cwd() / '.env', override=False, interpolate=False)
    load_dotenv(dotenv_path=REPO_ROOT / '.env', override=False, interpolate=False)
