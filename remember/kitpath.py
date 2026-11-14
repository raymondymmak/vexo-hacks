"""Find the Vexo Open kit, which lives in vexo-open-kit/ next to this project.

The kit is cloned by scripts/setup.sh and is not part of this repository.
Nothing here edits it.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "vexo-open-kit"
_KEY_NAMES = (
    "DEEPGRAM_API_KEY", "OPENAI_API_KEY", "OPENAI_MODEL",
    "REMEMBER_AUTO_DELIVER", "ICLOUD_USERNAME", "ICLOUD_APP_PASSWORD",
    "ICLOUD_ADDRESSBOOK_URL", "NTFY_URL", "NTFY_TOKEN", "REMEMBER_PUBLIC_URL",
    "REMEMBER_SOCIAL_LOOKUP", "REMEMBER_SOCIAL_MODEL",
    "GEMINI_API_KEY", "REMEMBER_GEMINI_MODEL", "REMEMBER_PAIRING_TOKEN",
    "REMEMBER_ROOM_URL", "REMEMBER_ROOM_TOKEN", "REMEMBER_ROOM_MATCHER",
)


class KitMissing(RuntimeError):
    """The kit folder is not next to the project yet."""


def ensure_kit() -> Path:
    """Put the kit's library and example helpers on sys.path. Return the kit folder."""
    library = KIT / "python"
    helpers = KIT / "examples" / "python"
    if not (library / "vexo_open").is_dir() or not (helpers / "speech.py").is_file():
        raise KitMissing(
            "The Vexo Open kit is not in vexo-open-kit/. From this folder run:  sh scripts/setup.sh"
        )
    for folder in (str(helpers), str(library)):
        if folder not in sys.path:
            sys.path.insert(0, folder)
    return KIT


def load_dotenv() -> None:
    """Load supported speech and delivery settings from .env into the environment.

    An existing environment variable wins. Values are never printed.
    The kit's helpers also read .env from the folder you start the program in.
    """
    for folder in (ROOT, Path.cwd()):
        path = folder / ".env"
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            if text.startswith("export "):
                text = text[len("export "):]
            name, sep, value = text.partition("=")
            if not sep:
                continue
            name = name.strip()
            value = value.strip().strip("'\"")
            if name in _KEY_NAMES and value and not os.environ.get(name, "").strip():
                os.environ[name] = value


def plain(text) -> str:
    """Text safe to print. Band text can hold characters that steer a terminal."""
    if text is None:
        return ""
    return "".join(ch if ch.isprintable() else " " for ch in str(text))
