import unicodedata

NIKUD_START = 0x0591
NIKUD_END = 0x05C7


def strip_nikud(text: str) -> str:
    return "".join(ch for ch in text if not (NIKUD_START <= ord(ch) <= NIKUD_END))


def normalize_hebrew(text: str) -> str:
    return strip_nikud(unicodedata.normalize("NFC", text)).strip()
