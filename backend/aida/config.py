"""Local configuration loaded before the agent starts."""
from pathlib import Path

from dotenv import load_dotenv


def load_local_env():
    """Load .env from the working directory without overriding shell variables."""
    load_dotenv(dotenv_path=Path.cwd() / '.env', override=False, interpolate=False)
