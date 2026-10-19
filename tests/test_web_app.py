import requests
from fastapi.testclient import TestClient

import karaoke_he.web.app as app_module
from karaoke_he.lyrics import LyricsResult, LyricsSource
from karaoke_he.web.app import _slugify, app
from karaoke_he.youtube import YouTubeError, YouTubeMetadata


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
    monkeypatch.setattr(app_module, "detect_language", lambda text: "fra")
    res = client.get("/api/lyrics-check", params={"artist": "A", "title": "B"})
    assert res.status_code == 200
    body = res.json()
    assert body["found"] is True
    assert body["source"] == "lrclib"
    assert body["synced"] is False
    assert body["text"] == "line one\nline two"
    assert body["detected_language"] == "fra"


def test_lyrics_check_endpoint_reports_not_found(monkeypatch):
    monkeypatch.setattr(app_module, "fetch_from_lrclib", lambda artist, title, album=None: None)
    res = client.get("/api/lyrics-check", params={"artist": "A", "title": "B"})
    assert res.status_code == 200
    assert res.json() == {"found": False}


def test_lyrics_check_endpoint_reports_transient_lrclib_failure_without_crashing(monkeypatch):
    def _raise(artist, title, album=None):
        raise requests.exceptions.HTTPError("503 Server Error")

    monkeypatch.setattr(app_module, "fetch_from_lrclib", _raise)
    res = client.get("/api/lyrics-check", params={"artist": "A", "title": "B"})
    assert res.status_code == 200
    body = res.json()
    assert body["found"] is False
    assert "error" in body


def test_lyrics_check_endpoint_prefers_youtube_description_over_lrclib(monkeypatch):
    description_lyrics = "la la la, one two three\nfour five six, la la la\nseven eight nine, la la la"

    monkeypatch.setattr(
        app_module,
        "fetch_youtube_metadata",
        lambda url: YouTubeMetadata(
            video_id="abc123",
            title="Some Video",
            suggested_artist="A",
            suggested_title="B",
            uploader="Channel",
            duration_sec=200.0,
            description=f"Lyrics:\n{description_lyrics}\n",
        ),
    )

    def _fail_lrclib(artist, title, album=None):
        raise AssertionError("should not fall back to LRCLIB when the description has lyrics")

    monkeypatch.setattr(app_module, "fetch_from_lrclib", _fail_lrclib)

    res = client.get(
        "/api/lyrics-check",
        params={"artist": "A", "title": "B", "youtube_url": "https://youtu.be/abc123"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["found"] is True
    assert body["source"] == "youtube_description"
    assert body["text"] == description_lyrics


def test_lyrics_check_endpoint_falls_back_to_lrclib_when_description_has_no_lyrics(monkeypatch):
    monkeypatch.setattr(
        app_module,
        "fetch_youtube_metadata",
        lambda url: YouTubeMetadata(
            video_id="abc123",
            title="Some Video",
            suggested_artist="A",
            suggested_title="B",
            uploader="Channel",
            duration_sec=200.0,
            description="Just a regular description with no lyrics section.",
        ),
    )
    monkeypatch.setattr(
        app_module,
        "fetch_from_lrclib",
        lambda artist, title, album=None: LyricsResult(
            text="line one\nline two", source=LyricsSource.LRCLIB, synced=False, raw_response=None
        ),
    )
    monkeypatch.setattr(app_module, "detect_language", lambda text: None)

    res = client.get(
        "/api/lyrics-check",
        params={"artist": "A", "title": "B", "youtube_url": "https://youtu.be/abc123"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["found"] is True
    assert body["source"] == "lrclib"


def test_lyrics_check_endpoint_falls_back_to_lrclib_when_youtube_metadata_fails(monkeypatch):
    def _raise(url):
        raise YouTubeError("video unavailable")

    monkeypatch.setattr(app_module, "fetch_youtube_metadata", _raise)
    monkeypatch.setattr(app_module, "fetch_from_lrclib", lambda artist, title, album=None: None)

    res = client.get(
        "/api/lyrics-check",
        params={"artist": "A", "title": "B", "youtube_url": "https://youtu.be/abc123"},
    )
    assert res.status_code == 200
    assert res.json() == {"found": False}


def test_suggestions_endpoint_returns_empty_list_on_transient_lrclib_failure(monkeypatch):
    def _raise(q):
        raise requests.exceptions.ConnectionError("boom")

    monkeypatch.setattr(app_module, "search_suggestions", _raise)
    res = client.get("/api/suggestions", params={"q": "A"})
    assert res.status_code == 200
    assert res.json() == []


def test_lyrics_check_endpoint_requires_artist_and_title():
    res = client.get("/api/lyrics-check", params={"artist": "A"})
    assert res.status_code == 422


def test_youtube_metadata_endpoint_returns_suggested_artist_and_title(monkeypatch):
    monkeypatch.setattr(
        app_module,
        "fetch_youtube_metadata",
        lambda url: YouTubeMetadata(
            video_id="abc123",
            title="Some Artist - Some Song",
            suggested_artist="Some Artist",
            suggested_title="Some Song",
            uploader="Some Channel",
            duration_sec=210.0,
        ),
    )
    res = client.get("/api/youtube-metadata", params={"url": "https://youtu.be/abc123"})
    assert res.status_code == 200
    body = res.json()
    assert body["suggested_artist"] == "Some Artist"
    assert body["suggested_title"] == "Some Song"
    assert body["duration_sec"] == 210.0


def test_youtube_metadata_endpoint_rejects_non_youtube_url():
    res = client.get("/api/youtube-metadata", params={"url": "https://example.com/video"})
    assert res.status_code == 400


def test_youtube_metadata_endpoint_reports_fetch_failure(monkeypatch):
    def _raise(url):
        raise YouTubeError("video unavailable")

    monkeypatch.setattr(app_module, "fetch_youtube_metadata", _raise)
    res = client.get("/api/youtube-metadata", params={"url": "https://youtu.be/abc123"})
    assert res.status_code == 400


def test_create_job_requires_exactly_one_of_file_or_youtube_url():
    res = client.post("/jobs", data={"artist": "A", "title": "B"})
    assert res.status_code == 400

    res = client.post(
        "/jobs",
        data={"artist": "A", "title": "B", "youtube_url": "https://youtu.be/abc123"},
        files={"file": ("song.mp3", b"fake audio", "audio/mpeg")},
    )
    assert res.status_code == 400


def test_create_job_rejects_non_youtube_url():
    res = client.post("/jobs", data={"artist": "A", "title": "B", "youtube_url": "https://example.com/video"})
    assert res.status_code == 400


def test_create_job_accepts_youtube_url_and_forwards_it(monkeypatch):
    captured = {}

    def _fake_create_job(song_slug, input_audio, artist, title, youtube_url=None, **kwargs):
        captured["input_audio"] = input_audio
        captured["youtube_url"] = youtube_url

        class _FakeJob:
            id = "job123"

        return _FakeJob()

    monkeypatch.setattr(app_module, "create_job", _fake_create_job)
    res = client.post("/jobs", data={"artist": "A", "title": "B", "youtube_url": "https://youtu.be/abc123"})
    assert res.status_code == 200
    assert res.json() == {"job_id": "job123"}
    assert captured["input_audio"] is None
    assert captured["youtube_url"] == "https://youtu.be/abc123"
