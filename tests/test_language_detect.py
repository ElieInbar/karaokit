import karaoke_he.language_detect as language_detect_module
from karaoke_he.language_detect import detect_language


def test_detect_language_maps_bcp47_to_languages_key(monkeypatch):
    monkeypatch.setattr(language_detect_module, "detect", lambda text, model=None: [{"lang": "fr", "score": 0.98}])
    assert detect_language("peu importe le texte ici") == "fra"


def test_detect_language_returns_none_below_confidence_threshold(monkeypatch):
    monkeypatch.setattr(language_detect_module, "detect", lambda text, model=None: [{"lang": "fr", "score": 0.2}])
    assert detect_language("some ambiguous text") is None


def test_detect_language_returns_none_for_unsupported_language(monkeypatch):
    monkeypatch.setattr(language_detect_module, "detect", lambda text, model=None: [{"lang": "de", "score": 0.99}])
    assert detect_language("ein deutscher text") is None


def test_detect_language_returns_none_for_empty_text():
    assert detect_language("   ") is None


def test_detect_language_returns_none_when_detector_raises(monkeypatch):
    def _raise(text, model=None):
        raise RuntimeError("model not loaded")

    monkeypatch.setattr(language_detect_module, "detect", _raise)
    assert detect_language("some text") is None
