"""Runtime settings, from environment variables or a `.env` file (see `.env.example`).

Everything that differs between installations lives here, so the same code runs on the
author's Mac, in Docker, or on someone else's machine with their own accounts:

- KARAOKIT_SEPARATION: "modal" (GPU on the user's own Modal account - fast), "local" (this
  machine, no account needed - ~20-30 min per song on a laptop), or "auto" (default: Modal when
  Modal credentials are configured, else local).
- KARAOKIT_MODAL_APP: name of the deployed Modal app (`modal deploy deploy/modal_app.py`).
- KARAOKIT_ENABLE_YOUTUBE: "1" to allow importing audio from YouTube links. Off by default:
  downloading from YouTube is against its terms of service - each user decides for themselves.
- KARAOKIT_DATA_DIR: where songs are processed and videos kept (`work/` and `songs/` inside).
- KARAOKIT_HOMEPAGE / KARAOKIT_FONT_PATH: see `lyrics.py` / `fonts.py`.
"""

import os
from pathlib import Path

_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_RUNNING_FROM_SOURCE = (_SOURCE_ROOT / "pyproject.toml").is_file()


def _load_dotenv(path: Path) -> None:
    """Minimal `.env` support (KEY=value lines, # comments); real environment variables win."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv(Path.cwd() / ".env")
if _RUNNING_FROM_SOURCE:
    _load_dotenv(_SOURCE_ROOT / ".env")


def _flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def modal_credentials_configured() -> bool:
    """Modal reads MODAL_TOKEN_ID/MODAL_TOKEN_SECRET or ~/.modal.toml (`modal token new`)."""
    return bool(os.environ.get("MODAL_TOKEN_ID")) or (Path.home() / ".modal.toml").is_file()


def separation_backend() -> str:
    choice = os.environ.get("KARAOKIT_SEPARATION", "auto").strip().lower()
    if choice in {"modal", "local"}:
        return choice
    return "modal" if modal_credentials_configured() else "local"


def youtube_enabled() -> bool:
    return _flag("KARAOKIT_ENABLE_YOUTUBE")


MODAL_APP_NAME = os.environ.get("KARAOKIT_MODAL_APP", "karaoke-he-separation")

DATA_DIR = Path(
    os.environ.get("KARAOKIT_DATA_DIR")
    or (_SOURCE_ROOT if _RUNNING_FROM_SOURCE else Path.home() / ".karaokit")
).expanduser()
WORK_DIR = DATA_DIR / "work"
SONGS_DIR = DATA_DIR / "songs"
