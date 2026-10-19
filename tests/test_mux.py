from pathlib import Path

import pytest

from karaoke_he.mux import mux_video


def test_mux_video_raises_on_missing_background_video(tmp_path: Path):
    existing = tmp_path / "exists.wav"
    existing.write_bytes(b"")
    with pytest.raises(FileNotFoundError, match="background video"):
        mux_video(tmp_path / "missing.mp4", existing, existing, tmp_path / "out.mkv")


def test_mux_video_raises_on_missing_instrumental(tmp_path: Path):
    existing = tmp_path / "exists.wav"
    existing.write_bytes(b"")
    with pytest.raises(FileNotFoundError, match="instrumental audio"):
        mux_video(existing, tmp_path / "missing.wav", existing, tmp_path / "out.mkv")


def test_mux_video_raises_on_missing_vocals(tmp_path: Path):
    existing = tmp_path / "exists.wav"
    existing.write_bytes(b"")
    with pytest.raises(FileNotFoundError, match="vocals audio"):
        mux_video(existing, existing, tmp_path / "missing.wav", tmp_path / "out.mkv")


def test_mux_video_does_not_require_subtitles(tmp_path: Path):
    existing = tmp_path / "exists.wav"
    existing.write_bytes(b"")
    # subtitles_path is optional (the karaoke highlight is burned into the video instead), so
    # this must get past file-existence validation without a FileNotFoundError; mkvmerge itself
    # will legitimately fail afterwards on these dummy (not real video/audio) file contents, and
    # that failure is not what this test is about.
    try:
        mux_video(existing, existing, existing, tmp_path / "out.mkv")
    except FileNotFoundError as exc:
        pytest.fail(f"subtitles should be optional, but got: {exc}")
    except Exception:
        pass


def test_mux_video_raises_on_missing_subtitles_when_provided(tmp_path: Path):
    existing = tmp_path / "exists.wav"
    existing.write_bytes(b"")
    with pytest.raises(FileNotFoundError, match="subtitles"):
        mux_video(existing, existing, existing, tmp_path / "out.mkv", subtitles_path=tmp_path / "missing.ass")


def test_encode_aac_compresses_a_wav(tmp_path: Path):
    import subprocess

    from karaoke_he.mux import encode_aac

    wav = tmp_path / "stem.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=5", "-ac", "2", str(wav)], check=True)
    out = encode_aac(wav, tmp_path / "stem.m4a")
    assert out.stat().st_size < wav.stat().st_size / 5
