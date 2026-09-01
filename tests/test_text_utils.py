import unicodedata

from karaoke_he.text_utils import normalize_hebrew, strip_nikud


def test_strip_nikud_removes_points():
    assert strip_nikud("שָׁלוֹם") == "שלום"


def test_strip_nikud_leaves_plain_text_unchanged():
    assert strip_nikud("שלום עולם") == "שלום עולם"


def test_strip_nikud_leaves_latin_and_digits_unchanged():
    assert strip_nikud("Hello 123") == "Hello 123"


def test_normalize_hebrew_strips_nikud_and_trims():
    assert normalize_hebrew("  שָׁלוֹם עוֹלָם  ") == "שלום עולם"


def test_normalize_hebrew_applies_nfc():
    decomposed = "שָׁלום"  # sin-dot as separate combining mark
    composed = unicodedata.normalize("NFC", decomposed)
    assert normalize_hebrew(decomposed) == strip_nikud(composed)


def test_normalize_hebrew_idempotent():
    text = "כמה כוכבים שנפלו עכשיו"
    assert normalize_hebrew(text) == normalize_hebrew(normalize_hebrew(text))
