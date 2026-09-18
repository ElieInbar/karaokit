from fastapi.testclient import TestClient

import karaoke_he.web.app as app_module
from karaoke_he.lyrics import LyricsResult, LyricsSource
from karaoke_he.web.app import _slugify, app


def test_slugify_lowercases_and_replaces_separators():
    assert _slugify("Idan Raichel") == "idan_raichel"


def test_slugify_preserves_hebrew():
    assert _slugify("עידן רייכל") == "עידן_רייכל"


def test_slugify_strips_leading_trailing_separators_and_punctuation():
    assert _slugify("  Or Kazeh!!  ") == "or_kazeh"


def test_slugify_falls_back_to_song_when_empty():
    assert _slugify("!!!") == "song"


client = TestClient(app)


def test_lyrics_check_endpoint_reports_found(monkeypatch):
    monkeypatch.setattr(
        app_module,
        "fetch_from_lrclib",
        lambda artist, title, album=None: LyricsResult(
            text="line one\nline two", source=LyricsSource.LRCLIB, synced=False, raw_response=None
        ),
    )
    res = client.get("/api/lyrics-check", params={"artist": "A", "title": "B"})
    assert res.status_code == 200
    body = res.json()
    assert body["found"] is True
    assert body["source"] == "lrclib"
    assert body["synced"] is False
    assert body["text"] == "line one\nline two"


def test_lyrics_check_endpoint_reports_not_found(monkeypatch):
    monkeypatch.setattr(app_module, "fetch_from_lrclib", lambda artist, title, album=None: None)
    res = client.get("/api/lyrics-check", params={"artist": "A", "title": "B"})
    assert res.status_code == 200
    assert res.json() == {"found": False}


def test_lyrics_check_endpoint_requires_artist_and_title():
    res = client.get("/api/lyrics-check", params={"artist": "A"})
    assert res.status_code == 422
