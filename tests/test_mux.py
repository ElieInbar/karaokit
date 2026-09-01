from pathlib import Path

import pytest

from karaoke_he.mux import mux_video


def test_mux_video_raises_on_missing_background_video(tmp_path: Path):
    existing = tmp_path / "exists.wav"
    existing.write_bytes(b"")
    with pytest.raises(FileNotFoundError, match="background video"):
        mux_video(tmp_path / "missing.mp4", existing, existing, existing, tmp_path / "out.mkv")


def test_mux_video_raises_on_missing_instrumental(tmp_path: Path):
    existing = tmp_path / "exists.wav"
    existing.write_bytes(b"")
    with pytest.raises(FileNotFoundError, match="instrumental audio"):
        mux_video(existing, tmp_path / "missing.wav", existing, existing, tmp_path / "out.mkv")


def test_mux_video_raises_on_missing_subtitles(tmp_path: Path):
    existing = tmp_path / "exists.wav"
    existing.write_bytes(b"")
    with pytest.raises(FileNotFoundError, match="subtitles"):
        mux_video(existing, existing, existing, tmp_path / "missing.ass", tmp_path / "out.mkv")
