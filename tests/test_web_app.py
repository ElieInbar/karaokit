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


def test_create_job_returns_409_while_the_same_song_is_generating(monkeypatch):
    def _conflict(*args, **kwargs):
        raise app_module.JobConflictError("a_b")

    monkeypatch.setattr(app_module, "create_job", _conflict)
    res = client.post("/jobs", data={"artist": "A", "title": "B", "youtube_url": "https://youtu.be/abc123"})
    assert res.status_code == 409
    assert "already being generated" in res.json()["detail"]


def test_cancel_endpoint_maps_unknown_and_finished_jobs(monkeypatch):
    monkeypatch.setattr(app_module, "cancel_job", lambda job_id: None)
    assert client.post("/jobs/nope/cancel").status_code == 404
    monkeypatch.setattr(app_module, "cancel_job", lambda job_id: False)
    assert client.post("/jobs/done/cancel").status_code == 409
    monkeypatch.setattr(app_module, "cancel_job", lambda job_id: True)
    assert client.post("/jobs/running/cancel").json() == {"cancelling": True}



def test_search_endpoint_returns_songs_with_lyrics_and_language(monkeypatch):
    from karaoke_he.lyrics import SongMatch

    monkeypatch.setattr(app_module, "search_songs", lambda q: [
        SongMatch(lrclib_id=7, artist="Omer Adam", title="מביט מהצד", album=None, duration_sec=224.0, synced=True, lyrics="שורה"),
        SongMatch(lrclib_id=8, artist="X", title="Y", album=None, duration_sec=None, synced=False, lyrics=None),
    ])
    monkeypatch.setattr(app_module, "detect_language", lambda text: "heb")

    data = client.get("/api/search", params={"q": "omer"}).json()

    assert "error" not in data
    assert data["results"][0]["lrclib_id"] == 7
    assert data["results"][0]["detected_language"] == "heb"
    assert data["results"][1]["detected_language"] is None


def test_search_endpoint_reports_lrclib_outage_instead_of_failing(monkeypatch):
    def _raise(q):
        raise requests.exceptions.HTTPError("503")

    monkeypatch.setattr(app_module, "search_songs", _raise)
    data = client.get("/api/search", params={"q": "omer"}).json()
    assert data["results"] == [] and "busy" in data["error"]


def test_create_job_forwards_the_picked_lrclib_id(monkeypatch):
    captured = {}

    def _fake_create_job(song_slug, input_audio, artist, title, **kwargs):
        captured.update(kwargs)

        class _FakeJob:
            id = "job1"

        return _FakeJob()

    monkeypatch.setattr(app_module, "create_job", _fake_create_job)
    res = client.post("/jobs", data={"artist": "A", "title": "B", "youtube_url": "https://youtu.be/abc123", "lrclib_id": "42"})
    assert res.status_code == 200
    assert captured["lrclib_id"] == 42
