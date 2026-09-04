from karaoke_he.web.app import _slugify


def test_slugify_lowercases_and_replaces_separators():
    assert _slugify("Idan Raichel") == "idan_raichel"


def test_slugify_preserves_hebrew():
    assert _slugify("עידן רייכל") == "עידן_רייכל"


def test_slugify_strips_leading_trailing_separators_and_punctuation():
    assert _slugify("  Or Kazeh!!  ") == "or_kazeh"


def test_slugify_falls_back_to_song_when_empty():
    assert _slugify("!!!") == "song"
