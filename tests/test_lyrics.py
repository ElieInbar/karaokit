from pathlib import Path

import karaoke_he.lyrics as lyrics_module
from karaoke_he.lyrics import (
    LyricsSource,
    _lyrics_result_from_lrclib_payload,
    fetch_from_lrclib,
    load_manual_fallback,
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
