from pathlib import Path

import karaoke_he.lyrics as lyrics_module
from karaoke_he.lyrics import (
    LyricsSource,
    _lyrics_result_from_lrclib_payload,
    fetch_from_lrclib,
    load_manual_fallback,
    search_suggestions,
    strip_synced_timestamps,
)


class _FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_strip_synced_timestamps_removes_tags():
    synced = "[00:01.20]שלום עולם\n[00:04.50]שורה שנייה"
    assert strip_synced_timestamps(synced) == "שלום עולם\nשורה שנייה"


def test_strip_synced_timestamps_drops_empty_lines():
    synced = "[00:01.20]שלום\n[00:04.50]\n[00:06.00]עולם"
    assert strip_synced_timestamps(synced) == "שלום\nעולם"


def test_lyrics_result_prefers_plain_over_synced():
    payload = {"plainLyrics": "שלום עולם", "syncedLyrics": "[00:01.20]שלום עולם"}
    result = _lyrics_result_from_lrclib_payload(payload)
    assert result.text == "שלום עולם"
    assert result.source is LyricsSource.LRCLIB
    assert result.synced is False


def test_lyrics_result_falls_back_to_synced():
    payload = {"plainLyrics": None, "syncedLyrics": "[00:01.20]שלום עולם"}
    result = _lyrics_result_from_lrclib_payload(payload)
    assert result.text == "שלום עולם"
    assert result.synced is True


def test_lyrics_result_none_when_no_lyrics_field():
    assert _lyrics_result_from_lrclib_payload({"instrumental": True}) is None


def test_load_manual_fallback_reads_file_and_strips_comments(tmp_path: Path):
    song_dir = tmp_path / "or_caze"
    song_dir.mkdir()
    (song_dir / "lyrics.txt").write_text("# comment\nשלום עולם\n# another comment\nשורה שנייה\n", encoding="utf-8")

    result = load_manual_fallback("or_caze", tmp_path)

    assert result.source is LyricsSource.MANUAL
    assert result.text == "שלום עולם\nשורה שנייה"


def test_load_manual_fallback_returns_none_when_missing(tmp_path: Path):
    assert load_manual_fallback("no_such_song", tmp_path) is None


def test_load_manual_fallback_returns_none_when_only_comments(tmp_path: Path):
    song_dir = tmp_path / "or_caze"
    song_dir.mkdir()
    (song_dir / "lyrics.txt").write_text("# just a comment\n", encoding="utf-8")

    assert load_manual_fallback("or_caze", tmp_path) is None


def test_fetch_from_lrclib_retries_with_track_name_only_and_picks_closest_duration(monkeypatch):
    """Reproduces the עידן רייכל / Idan Raichel case: LRCLIB stores the artist name
    transliterated, so the combined artist+track search comes up empty even though the track
    exists; retrying with just the track title should find it, skip the instrumental version,
    and prefer the candidate whose duration matches the source audio."""

    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params))
        if url.endswith("/get"):
            return _FakeResponse(404, {})
        if url.endswith("/search"):
            if "artist_name" in params:
                return _FakeResponse(200, [])
            return _FakeResponse(
                200,
                [
                    {"artistName": "The Idan Raichel Project", "instrumental": True, "duration": 235.0},
                    {"artistName": "Idan Raichel", "instrumental": False, "duration": 500.0, "plainLyrics": "wrong duration"},
                    {"artistName": "Idan Raichel", "instrumental": False, "duration": 235.0, "plainLyrics": "אור כזה"},
                ],
            )
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(lyrics_module.requests, "get", fake_get)

    result = fetch_from_lrclib("עידן רייכל", "אור כזה", duration_sec=235.28)

    assert result is not None
    assert result.text == "אור כזה"
    assert calls[1][1] == {"artist_name": "עידן רייכל", "track_name": "אור כזה"}
    assert calls[2][1] == {"track_name": "אור כזה"}


def test_search_suggestions_returns_empty_list_for_blank_query():
    assert search_suggestions("   ") == []


def test_search_suggestions_uses_generic_q_param(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params))
        return _FakeResponse(200, [])

    monkeypatch.setattr(lyrics_module.requests, "get", fake_get)
    search_suggestions("idan rai")

    assert calls == [(f"{lyrics_module.LRCLIB_BASE_URL}/search", {"q": "idan rai"})]


def test_search_suggestions_extracts_only_non_lyrics_fields(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return _FakeResponse(
            200,
            [
                {
                    "artistName": "Artist One",
                    "trackName": "Song One",
                    "albumName": "Album One",
                    "duration": 200.0,
                    "plainLyrics": "should never appear in a suggestion",
                    "syncedLyrics": "[00:00.00]should never appear either",
                }
            ],
        )

    monkeypatch.setattr(lyrics_module.requests, "get", fake_get)
    results = search_suggestions("artist one")

    assert len(results) == 1
    assert results[0].artist == "Artist One"
    assert results[0].title == "Song One"
    assert results[0].album == "Album One"
    assert results[0].duration_sec == 200.0
    assert not hasattr(results[0], "plainLyrics")
    assert not hasattr(results[0], "syncedLyrics")


def test_search_suggestions_dedupes_by_artist_and_title(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return _FakeResponse(
            200,
            [
                {"artistName": "A", "trackName": "B", "albumName": "Album 1", "duration": 100.0},
                {"artistName": "A", "trackName": "B", "albumName": "Album 2 (Remaster)", "duration": 101.0},
                {"artistName": "A", "trackName": "C", "duration": 102.0},
            ],
        )

    monkeypatch.setattr(lyrics_module.requests, "get", fake_get)
    results = search_suggestions("a")

    assert len(results) == 2
    assert [(r.artist, r.title) for r in results] == [("A", "B"), ("A", "C")]


def test_search_suggestions_skips_entries_missing_artist_or_title(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return _FakeResponse(
            200,
            [
                {"artistName": None, "trackName": "B"},
                {"artistName": "A", "trackName": None},
                {"artistName": "A", "trackName": "B"},
            ],
        )

    monkeypatch.setattr(lyrics_module.requests, "get", fake_get)
    results = search_suggestions("a")

    assert len(results) == 1


def test_search_suggestions_respects_limit(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return _FakeResponse(
            200, [{"artistName": f"Artist {i}", "trackName": f"Song {i}"} for i in range(20)]
        )

    monkeypatch.setattr(lyrics_module.requests, "get", fake_get)
    results = search_suggestions("a", limit=3)

    assert len(results) == 3


def test_fetch_from_lrclib_retries_transient_server_errors(monkeypatch):
    """LRCLIB intermittently answers 503 "ServerOverloaded"; a blip at the lyrics stage
    shouldn't fail a job that already spent minutes on download/separation."""
    statuses = iter([503, 503, 200])

    def fake_get(url, params=None, timeout=None):
        return _FakeResponse(next(statuses), {"plainLyrics": "שלום"})

    monkeypatch.setattr(lyrics_module.requests, "get", fake_get)
    monkeypatch.setattr(lyrics_module, "LRCLIB_RETRY_DELAYS_SEC", (0, 0, 0))

    result = fetch_from_lrclib("artist", "title")

    assert result is not None
    assert result.text == "שלום"


def test_fetch_from_lrclib_gives_up_after_persistent_server_errors(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        return _FakeResponse(503, {})

    monkeypatch.setattr(lyrics_module.requests, "get", fake_get)
    monkeypatch.setattr(lyrics_module, "LRCLIB_RETRY_DELAYS_SEC", (0, 0))

    try:
        fetch_from_lrclib("artist", "title")
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the persistent 503 to propagate")
    assert len(calls) == 3


def test_fetch_from_lrclib_does_not_retry_not_found(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        return _FakeResponse(404 if url.endswith("/get") else 200, [] if url.endswith("/search") else {})

    monkeypatch.setattr(lyrics_module.requests, "get", fake_get)
    monkeypatch.setattr(lyrics_module, "LRCLIB_RETRY_DELAYS_SEC", (0, 0, 0))

    assert fetch_from_lrclib("artist", "title") is None
    assert calls.count(calls[0]) == 1
