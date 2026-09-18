import unicodedata

from karaoke_he.text_utils import normalize_hebrew, strip_display_punctuation, strip_nikud


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


def test_strip_display_punctuation_removes_wrapping_parentheses():
    assert strip_display_punctuation("(word)") == "word"


def test_strip_display_punctuation_removes_leading_and_trailing_marks():
    assert strip_display_punctuation("(word,") == "word"
    assert strip_display_punctuation("word)") == "word"
    assert strip_display_punctuation('"word"') == "word"


def test_strip_display_punctuation_keeps_internal_apostrophes():
    assert strip_display_punctuation("j'ai") == "j'ai"
    assert strip_display_punctuation("qu'on") == "qu'on"


def test_strip_display_punctuation_leaves_plain_word_unchanged():
    assert strip_display_punctuation("word") == "word"


def test_strip_display_punctuation_leaves_hebrew_word_unchanged():
    assert strip_display_punctuation("שלום") == "שלום"
