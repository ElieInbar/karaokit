import pytest
import yt_dlp

import karaoke_he.youtube as youtube_module
from karaoke_he.youtube import (
    YouTubeError,
    download_youtube_audio,
    extract_lyrics_from_description,
    fetch_youtube_metadata,
    is_youtube_url,
)


def test_is_youtube_url_accepts_watch_and_short_forms():
    assert is_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert is_youtube_url("https://youtube.com/watch?v=dQw4w9WgXcQ")
    assert is_youtube_url("https://youtu.be/dQw4w9WgXcQ")


def test_is_youtube_url_rejects_other_domains():
    assert not is_youtube_url("https://example.com/watch?v=dQw4w9WgXcQ")
    assert not is_youtube_url("not a url")
    assert not is_youtube_url("")


class _FakeYDLInfo:
    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, download=False):
        return self.info


def _fake_ydl_info(info: dict):
    def _factory(opts):
        ydl = _FakeYDLInfo(opts)
        ydl.info = info
        return ydl

    return _factory


def test_fetch_youtube_metadata_prefers_artist_and_track_fields(monkeypatch):
    monkeypatch.setattr(
        youtube_module.yt_dlp,
        "YoutubeDL",
        _fake_ydl_info(
            {
                "id": "abc123",
                "title": "Some Video Title",
                "artist": "Real Artist",
                "track": "Real Track",
                "uploader": "Some Channel",
                "duration": 210.0,
            }
        ),
    )
    metadata = fetch_youtube_metadata("https://www.youtube.com/watch?v=abc123")
    assert metadata.suggested_artist == "Real Artist"
    assert metadata.suggested_title == "Real Track"
    assert metadata.video_id == "abc123"
    assert metadata.duration_sec == 210.0


def test_fetch_youtube_metadata_splits_artist_dash_title_from_video_title(monkeypatch):
    monkeypatch.setattr(
        youtube_module.yt_dlp,
        "YoutubeDL",
        _fake_ydl_info({"id": "abc123", "title": "Some Artist - Some Song", "uploader": "Some Channel"}),
    )
    metadata = fetch_youtube_metadata("https://youtu.be/abc123")
    assert metadata.suggested_artist == "Some Artist"
    assert metadata.suggested_title == "Some Song"


def test_fetch_youtube_metadata_falls_back_to_uploader_and_full_title(monkeypatch):
    monkeypatch.setattr(
        youtube_module.yt_dlp,
        "YoutubeDL",
        _fake_ydl_info({"id": "abc123", "title": "A title with no dash", "uploader": "Some Channel"}),
    )
    metadata = fetch_youtube_metadata("https://youtu.be/abc123")
    assert metadata.suggested_artist == "Some Channel"
    assert metadata.suggested_title == "A title with no dash"


def test_fetch_youtube_metadata_rejects_non_youtube_url():
    with pytest.raises(YouTubeError):
        fetch_youtube_metadata("https://example.com/watch?v=abc123")


def test_fetch_youtube_metadata_wraps_download_error(monkeypatch):
    def _raise(opts):
        raise yt_dlp.utils.DownloadError("boom")

    monkeypatch.setattr(youtube_module.yt_dlp, "YoutubeDL", _raise)
    with pytest.raises(YouTubeError):
        fetch_youtube_metadata("https://youtu.be/abc123")


class _FakeYDLDownload:
    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def download(self, urls):
        hook = self.opts["progress_hooks"][0]
        hook({"status": "downloading", "_percent_str": " 50%"})
        # Simulates yt-dlp's own FFmpegExtractAudio postprocessor: the final file has the
        # postprocessor's target extension (.wav), not whatever the raw download's ext was.
        target_path = self.opts["outtmpl"].replace("%(ext)s", "wav")
        with open(target_path, "wb") as f:
            f.write(b"fake audio bytes")


def test_download_youtube_audio_returns_downloaded_path_and_reports_progress(monkeypatch, tmp_path):
    monkeypatch.setattr(youtube_module.yt_dlp, "YoutubeDL", _FakeYDLDownload)
    progress_messages = []

    result = download_youtube_audio(
        "https://youtu.be/abc123", tmp_path, on_progress=progress_messages.append
    )

    assert result == tmp_path / "input.wav"
    assert result.is_file()
    assert progress_messages == ["50%"]


def test_download_youtube_audio_rejects_non_youtube_url(tmp_path):
    with pytest.raises(YouTubeError):
        download_youtube_audio("https://example.com/video", tmp_path)


def test_download_youtube_audio_wraps_download_error(monkeypatch, tmp_path):
    def _raise(opts):
        raise yt_dlp.utils.DownloadError("boom")

    monkeypatch.setattr(youtube_module.yt_dlp, "YoutubeDL", _raise)
    with pytest.raises(YouTubeError):
        download_youtube_audio("https://youtu.be/abc123", tmp_path)


_PLACEHOLDER_LYRICS = "la la la, one two three\nfour five six, la la la\nseven eight nine, la la la"


def test_extract_lyrics_from_description_finds_text_after_header():
    description = f"Official video.\n\nLyrics:\n{_PLACEHOLDER_LYRICS}\n"
    assert extract_lyrics_from_description(description) == _PLACEHOLDER_LYRICS


def test_extract_lyrics_from_description_accepts_non_english_markers():
    description = f"Clip officiel.\n\nParoles :\n{_PLACEHOLDER_LYRICS}\n"
    assert extract_lyrics_from_description(description) == _PLACEHOLDER_LYRICS


def test_extract_lyrics_from_description_stops_at_next_heading():
    description = f"Lyrics:\n{_PLACEHOLDER_LYRICS}\n\nCredits:\nProduced by someone\nMixed by someone else"
    assert extract_lyrics_from_description(description) == _PLACEHOLDER_LYRICS


def test_extract_lyrics_from_description_trims_trailing_social_links():
    description = (
        f"Lyrics:\n{_PLACEHOLDER_LYRICS}\n\nFollow us on Instagram\nhttps://instagram.com/example\n#music"
    )
    assert extract_lyrics_from_description(description) == _PLACEHOLDER_LYRICS


def test_extract_lyrics_from_description_returns_none_without_a_header():
    assert extract_lyrics_from_description(f"Official video.\n\n{_PLACEHOLDER_LYRICS}") is None


def test_extract_lyrics_from_description_returns_none_when_too_short():
    assert extract_lyrics_from_description("Lyrics:\nsee pinned comment") is None


def test_extract_lyrics_from_description_returns_none_for_empty_description():
    assert extract_lyrics_from_description("") is None


def test_friendly_youtube_error_explains_bot_check_without_cli_advice():
    from karaoke_he.youtube import friendly_youtube_error

    message = friendly_youtube_error(
        YouTubeError("ERROR: [youtube] abc: Sign in to confirm you're not a bot. Use --cookies-from-browser")
    )
    assert "anti-bot" in message
    assert "--cookies" not in message
