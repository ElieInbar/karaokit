"""Modal deployment of the GPU work: vocal separation (BS-Roformer) and the alignment's
acoustic model, in the user's own Modal account. A laptop takes ~20-30 minutes per song for the
separation; an H100 takes ~30 seconds.

Reuses `karaoke_he.separation.separate_audio` and `karaoke_he.alignment.compute_emissions`
unchanged; only the execution environment differs.

Setup and deploy (once, and again after updating Karaokit):
    modal token new                   # or MODAL_TOKEN_ID / MODAL_TOKEN_SECRET in .env
    modal deploy deploy/modal_app.py

Manual test on one file:
    modal run deploy/modal_app.py --input song.mp3 --song-slug my_song
"""

from pathlib import Path

import modal

from karaoke_he.separation import ALIGNMENT_EMISSIONS_NAME, DEFAULT_MODEL, separate_audio

app = modal.App("karaoke-he-separation")

MODEL_CACHE_DIR = "/cache/audio-separator-models"
model_cache_volume = modal.Volume.from_name("karaoke-he-model-cache", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg", "git")
    .pip_install(
        "audio-separator[gpu]",
        "audioread",
        # For the alignment's acoustic model, run right after separation (see separate_job).
        "ctc-forced-aligner @ git+https://github.com/MahmoudAshraf97/ctc-forced-aligner.git",
    )
    # Both model caches live on the same persistent volume, so neither is re-downloaded per run.
    .env({"AUDIO_SEPARATOR_MODEL_DIR": MODEL_CACHE_DIR, "HF_HOME": f"{MODEL_CACHE_DIR}/huggingface"})
    .add_local_python_source("karaoke_he")
)

@app.function(
    image=image,
    # H100: measured ~3x faster than the original A10G on BS-Roformer (54s vs 162s for a 4-minute
    # song at the model's default overlap), so roughly the same cost per song at Modal's
    # per-second billing despite the higher hourly rate.
    gpu="H100",
    volumes={MODEL_CACHE_DIR: model_cache_volume},
    timeout=600,
)
def separate_job(
    audio_bytes: bytes,
    filename: str,
    model_name: str = DEFAULT_MODEL,
    overlap: int | None = None,
    with_alignment_emissions: bool = False,
    progress_queue: modal.Queue | None = None,
) -> dict[str, bytes]:
    """Separates vocals/instrumental (and optionally computes the alignment's acoustic-model
    output) and returns the files' bytes. Called with `.spawn(...)` by
    `separation.separate_audio_remote`, so the caller can cancel it - which terminates this
    container - rather than via a streaming generator, which Modal can't cancel server-side.
    Progress ("NN%", read from audio-separator's tqdm output) goes into `progress_queue`."""
    import tempfile

    def report(message: str) -> None:
        if progress_queue is None:
            return
        try:
            progress_queue.put(message, block=False)
        except Exception:  # noqa: BLE001 - progress is best effort, never worth failing the job
            pass

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        input_path = tmp_dir / filename
        input_path.write_bytes(audio_bytes)

        result = separate_audio(input_path, tmp_dir / "stems", model_name=model_name, overlap=overlap, on_progress=report)
        model_cache_volume.commit()

        files = {
            "vocals.wav": result.vocals_path.read_bytes(),
            "instrumental.wav": result.instrumental_path.read_bytes(),
        }
        if with_alignment_emissions:
            # The alignment's only expensive step (its acoustic model over the vocals) takes
            # ~12s on a Mac's MPS but ~1-2s here, and the vocals are already on this GPU box -
            # so it runs here and only the small result (~1 MB) travels back.
            from karaoke_he.alignment import compute_emissions

            emissions_path = tmp_dir / ALIGNMENT_EMISSIONS_NAME
            compute_emissions(result.vocals_path).save(emissions_path)
            model_cache_volume.commit()
            files[ALIGNMENT_EMISSIONS_NAME] = emissions_path.read_bytes()
        return files


@app.local_entrypoint()
def main(input: str, song_slug: str, model_name: str = DEFAULT_MODEL) -> None:
    input_path = Path(input).expanduser()
    stems = separate_job.remote(input_path.read_bytes(), input_path.name, model_name)

    work_dir = Path(__file__).resolve().parents[1] / "work" / song_slug / "stems"
    work_dir.mkdir(parents=True, exist_ok=True)
    for name, data in stems.items():
        out_path = work_dir / name
        out_path.write_bytes(data)
        print(f"{name}: {out_path} ({len(data) / 1024 / 1024:.1f} MB)")
