"""Modal deployment of the separation stage, for running BS-Roformer on a real CUDA GPU
instead of the local M1 (MPS lacks efficient flash-attention kernels for this model,
making a ~4 min song take 20-30 min locally vs. a target of well under a minute on GPU).

Reuses `karaoke_he.separation.separate_audio` unchanged; only the execution environment
changes (remote GPU container instead of the local venv).

One-time setup (not done yet):
    pip install modal
    modal setup                       # browser login, creates ~/.modal.toml

Deploy:
    modal deploy deploy/modal_app.py

Manual test on one file:
    modal run deploy/modal_app.py --input ~/Downloads/or_caze.mp3 --song-slug or_caze
"""

from pathlib import Path

import modal

from karaoke_he.separation import DEFAULT_MODEL, separate_audio

app = modal.App("karaoke-he-separation")

MODEL_CACHE_DIR = "/cache/audio-separator-models"
model_cache_volume = modal.Volume.from_name("karaoke-he-model-cache", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg")
    .pip_install("audio-separator[gpu]", "audioread")
    .env({"AUDIO_SEPARATOR_MODEL_DIR": MODEL_CACHE_DIR})
    .add_local_python_source("karaoke_he")
)


@app.function(
    image=image,
    gpu="A10G",
    volumes={MODEL_CACHE_DIR: model_cache_volume},
    timeout=600,
)
def separate_remote(
    audio_bytes: bytes, filename: str, model_name: str = DEFAULT_MODEL
) -> dict[str, bytes]:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        input_path = tmp_dir / filename
        input_path.write_bytes(audio_bytes)

        result = separate_audio(input_path, tmp_dir / "stems", model_name=model_name)
        model_cache_volume.commit()

        return {
            "vocals.wav": result.vocals_path.read_bytes(),
            "instrumental.wav": result.instrumental_path.read_bytes(),
        }


@app.local_entrypoint()
def main(input: str, song_slug: str, model_name: str = DEFAULT_MODEL) -> None:
    input_path = Path(input).expanduser()
    stems = separate_remote.remote(input_path.read_bytes(), input_path.name, model_name)

    work_dir = Path(__file__).resolve().parents[1] / "work" / song_slug / "stems"
    work_dir.mkdir(parents=True, exist_ok=True)
    for name, data in stems.items():
        out_path = work_dir / name
        out_path.write_bytes(data)
        print(f"{name}: {out_path} ({len(data) / 1024 / 1024:.1f} MB)")
