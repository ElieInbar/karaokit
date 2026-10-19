"""Fonts for everything drawn into the video (lyrics, title, artist).

Bundled with the package (SIL Open Font License, see `assets/fonts/OFL-*.txt`) so rendering
looks the same on any machine - it used to load macOS's "Arial Unicode", which doesn't exist
on Linux/Docker and can't be redistributed:

- Rubik: Latin, Hebrew and Cyrillic in one family (designed by an Israeli foundry).
- Noto Sans Arabic: Arabic, plus Latin for mixed text. Rubik has no Arabic.

`KARAOKIT_FONT_PATH` overrides both with any TrueType/OpenType file.
"""

import os
import re
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

FONTS_DIR = Path(__file__).parent / "assets" / "fonts"
DEFAULT_FONT_PATH = FONTS_DIR / "Rubik-Variable.ttf"
ARABIC_FONT_PATH = FONTS_DIR / "NotoSansArabic-Variable.ttf"
DEFAULT_WEIGHT = "Medium"

_ARABIC_RE = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")


def _font_path(arabic: bool) -> Path:
    override = os.environ.get("KARAOKIT_FONT_PATH")
    if override:
        return Path(override)
    return ARABIC_FONT_PATH if arabic else DEFAULT_FONT_PATH


@lru_cache(maxsize=64)
def get_font(size: int, *, arabic: bool = False, weight: str = DEFAULT_WEIGHT) -> ImageFont.FreeTypeFont:
    # Always the BASIC layout engine: the text is already bidi-reordered (and Arabic-shaped) by
    # `subtitle_render._to_display_text`. Pillow builds that ship libraqm (Linux wheels, so
    # Docker) would otherwise apply bidi a second time and draw Hebrew backwards - which is
    # exactly what happened in the first Docker build.
    font = ImageFont.truetype(str(_font_path(arabic)), size, layout_engine=ImageFont.Layout.BASIC)
    try:
        font.set_variation_by_name(weight)
    except (OSError, ValueError):
        pass  # not a variable font (e.g. a KARAOKIT_FONT_PATH override) - use it as-is
    return font


def font_for_text(text: str, size: int, *, weight: str = DEFAULT_WEIGHT) -> ImageFont.FreeTypeFont:
    """The bundled font that can draw `text`: Arabic script needs Noto Sans Arabic."""
    return get_font(size, arabic=bool(_ARABIC_RE.search(text)), weight=weight)


def font_for_language(language: str, size: int, *, weight: str = DEFAULT_WEIGHT) -> ImageFont.FreeTypeFont:
    return get_font(size, arabic=language == "ara", weight=weight)
