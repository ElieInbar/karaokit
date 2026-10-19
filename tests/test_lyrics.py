from pathlib import Path

import karaoke_he.lyrics as lyrics_module
from karaoke_he.lyrics import (
    LyricsSource,
    _lyrics_result_from_lrclib_payload,
    fetch_from_lrclib,
    load_manual_fallback,
    fetch_lrclib_by_id,
    search_songs,
    strip_synced_timestamps,
)


class _FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload
        self.headers = {}

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

    def fake_get(url, params=None, timeout=None, **kwargs):
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


def test_fetch_from_lrclib_retries_transient_server_errors(monkeypatch):
    """LRCLIB intermittently answers 503 "ServerOverloaded"; a blip at the lyrics stage
    shouldn't fail a job that already spent minutes on download/separation."""
    statuses = iter([503, 503, 200])

    def fake_get(url, params=None, timeout=None, **kwargs):
        return _FakeResponse(next(statuses), {"plainLyrics": "שלום"})

    monkeypatch.setattr(lyrics_module.requests, "get", fake_get)
    monkeypatch.setattr(lyrics_module, "LRCLIB_RETRY_DELAYS_SEC", (0, 0, 0))

    result = fetch_from_lrclib("artist", "title")

    assert result is not None
    assert result.text == "שלום"


def test_fetch_from_lrclib_gives_up_after_persistent_server_errors(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None, **kwargs):
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

    def fake_get(url, params=None, timeout=None, **kwargs):
        calls.append(url)
        return _FakeResponse(404 if url.endswith("/get") else 200, [] if url.endswith("/search") else {})

    monkeypatch.setattr(lyrics_module.requests, "get", fake_get)
    monkeypatch.setattr(lyrics_module, "LRCLIB_RETRY_DELAYS_SEC", (0, 0, 0))

    assert fetch_from_lrclib("artist", "title") is None
    assert calls.count(calls[0]) == 1



def _search_payload():
    return [
        {"id": 1, "artistName": "OMER BALIK", "trackName": "OMER BALIK - Chicago Downtown [ZbIABUsnxFg]", "plainLyrics": "plain"},
        {"id": 2, "artistName": "Ömer Balık", "trackName": "Chicago Downtown", "syncedLyrics": "[00:01.00]synced line"},
        {"id": 3, "artistName": "Omer Adam", "trackName": "מביט מהצד", "plainLyrics": None, "syncedLyrics": None},
        {"id": 4, "artistName": "Someone", "trackName": "Instrumental cut", "instrumental": True},
    ]


def test_search_songs_groups_duplicates_and_keeps_the_best_version(monkeypatch):
    lyrics_module._search_cache.clear()
    monkeypatch.setattr(lyrics_module.requests, "get", lambda url, params=None, timeout=None, **kwargs: _FakeResponse(200, _search_payload()))

    results = search_songs("chicago downtown")

    assert [(r.lrclib_id, r.title) for r in results] == [(2, "Chicago Downtown"), (3, "מביט מהצד")]
    assert results[0].synced and results[0].lyrics == "synced line"  # synced wins over plain
    assert results[1].lyrics is None  # listed, but marked as having no lyrics


def test_search_songs_caches_and_ignores_too_short_queries(monkeypatch):
    lyrics_module._search_cache.clear()
    calls = []

    def fake_get(url, params=None, timeout=None, **kwargs):
        calls.append(params)
        return _FakeResponse(200, _search_payload())

    monkeypatch.setattr(lyrics_module.requests, "get", fake_get)
    assert search_songs("a") == []
    search_songs("Omer  Adam")
    search_songs("omer adam")
    assert calls == [{"q": "Omer Adam"}]


def test_search_songs_retries_rate_limits(monkeypatch):
    lyrics_module._search_cache.clear()
    statuses = iter([429, 503, 200])
    monkeypatch.setattr(lyrics_module.requests, "get", lambda url, params=None, timeout=None, **kwargs: _FakeResponse(next(statuses), _search_payload()))
    monkeypatch.setattr(lyrics_module, "LRCLIB_INTERACTIVE_RETRY_DELAYS_SEC", (0, 0, 0))
    assert len(search_songs("chicago")) == 2


def test_fetch_lrclib_by_id_uses_the_exact_record(monkeypatch):
    seen = []

    def fake_get(url, params=None, timeout=None, **kwargs):
        seen.append(url)
        return _FakeResponse(200, {"id": 42, "plainLyrics": "exact lyrics"}) if url.endswith("/get/42") else _FakeResponse(404, {})

    monkeypatch.setattr(lyrics_module.requests, "get", fake_get)
    assert fetch_lrclib_by_id(42).text == "exact lyrics"
    assert fetch_lrclib_by_id(7) is None
    assert seen[0].endswith("/api/get/42")


def test_hebrew_skeleton_ignores_spelling_variants():
    from karaoke_he.lyrics import _hebrew_skeleton

    assert _hebrew_skeleton("שירד") == _hebrew_skeleton("שיירד")
    assert _hebrew_skeleton("שָׁלוֹם") == _hebrew_skeleton("שלם")
    assert _hebrew_skeleton("לך") == _hebrew_skeleton("לכ")


def test_hebrew_search_falls_back_to_single_words_when_spelling_differs(monkeypatch):
    lyrics_module._search_cache.clear()
    song = {"id": 9, "artistName": "Osher Cohen", "trackName": "עד שיירד הסהר", "plainLyrics": "x"}
    other = {"id": 10, "artistName": "Someone", "trackName": "הסהר הכחול", "plainLyrics": "y"}
    queries = []

    def fake_get(url, params=None, timeout=None, **kwargs):
        queries.append(params["q"])
        if params["q"] == "הסהר":
            return _FakeResponse(200, [other, song])
        if params["q"] == "שירד":
            return _FakeResponse(503, {})  # a busy probe is skipped, not fatal
        return _FakeResponse(200, [])  # the full query, with the "wrong" spelling

    monkeypatch.setattr(lyrics_module.requests, "get", fake_get)
    monkeypatch.setattr(lyrics_module, "LRCLIB_INTERACTIVE_RETRY_DELAYS_SEC", ())

    results = search_songs("אושר כהן עד שירד הסהר")

    assert [r.lrclib_id for r in results] == [9]  # "הסהר הכחול" shares too few words
    assert queries[0] == "אושר כהן עד שירד הסהר"
