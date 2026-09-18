import re
import unicodedata

NIKUD_START = 0x0591
NIKUD_END = 0x05C7

_LEADING_TRAILING_PUNCT_RE = re.compile(r"^\W+|\W+$", re.UNICODE)


def strip_nikud(text: str) -> str:
    return "".join(ch for ch in text if not (NIKUD_START <= ord(ch) <= NIKUD_END))


def normalize_hebrew(text: str) -> str:
    return strip_nikud(unicodedata.normalize("NFC", text)).strip()


def strip_display_punctuation(word: str) -> str:
    """Strips leading/trailing punctuation (parentheses, commas, quotes, ...) from a single word
    for *display* purposes only - e.g. a raw lyric token like "(nanana," (backing-vocal ad-libs
    are often wrapped in parentheses in fetched lyrics) renders as "nanana" instead of showing
    the punctuation marks. Only touches the ends of the word, not interior characters, so e.g.
    French "j'ai" or "qu'on" keep their apostrophe. Never used on the text fed into the forced
    aligner or on WordTimestamp.word itself - alignment/validation must see the original token;
    this only affects what's drawn on screen."""
    return _LEADING_TRAILING_PUNCT_RE.sub("", word)
