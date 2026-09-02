from pathlib import Path

import pytest

from karaoke_he.separation import separate_audio_remote


def test_separate_audio_remote_raises_on_missing_input(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        separate_audio_remote(tmp_path / "missing.mp3", tmp_path / "stems")


def test_separate_audio_remote_returns_cached_result_without_calling_modal(tmp_path: Path, monkeypatch):
    import soundfile as sf
    import numpy as np

    input_audio = tmp_path / "input.wav"
    sf.write(input_audio, np.zeros(44100), 44100)

    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    (stems_dir / "vocals.wav").write_bytes(b"")
    (stems_dir / "instrumental.wav").write_bytes(b"")
    sf.write(stems_dir / "vocals.wav", np.zeros(44100), 44100)
    sf.write(stems_dir / "instrumental.wav", np.zeros(44100), 44100)

    # No modal import/network call should happen on a cache hit; monkeypatch import to blow up
    # if the code path ever tries to reach it.
    import sys

    monkeypatch.setitem(sys.modules, "modal", None)

    result = separate_audio_remote(input_audio, stems_dir, overwrite=False)
    assert result.vocals_path == stems_dir / "vocals.wav"
    assert result.instrumental_path == stems_dir / "instrumental.wav"
