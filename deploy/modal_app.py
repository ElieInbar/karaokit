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

import re
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from queue import Queue

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
        # For the alignment's acoustic model, run right after separation (see separate_remote).
        "ctc-forced-aligner @ git+https://github.com/MahmoudAshraf97/ctc-forced-aligner.git",
    )
    # Both model caches live on the same persistent volume, so neither is re-downloaded per run.
    .env({"AUDIO_SEPARATOR_MODEL_DIR": MODEL_CACHE_DIR, "HF_HOME": f"{MODEL_CACHE_DIR}/huggingface"})
    .add_local_python_source("karaoke_he")
)

# audio-separator's Separator has no first-class progress callback (checked its installed
# source: architectures/mdxc_separator.py only reports progress via a plain tqdm bar around its
# chunk loop). This regex picks the percentage back out of tqdm's rendered text instead.
_TQDM_PERCENT_RE = re.compile(r"(\d{1,3})%\|")


class _ProgressCapture:
    """A stderr-like stream that both passes writes through to the real stderr (so container
    logs are unaffected) and parses out tqdm's percentage for `separate_remote` to yield as
    progress. Deduplicates consecutive identical percentages so the caller doesn't get a flood
    of no-op updates."""

    def __init__(self, real_stream, queue: Queue) -> None:
        self._real = real_stream
        self._queue = queue
        self._last_pct: int | None = None

    def write(self, text: str) -> int:
        self._real.write(text)
        match = _TQDM_PERCENT_RE.search(text)
        if match:
            pct = int(match.group(1))
            if pct != self._last_pct:
                self._last_pct = pct
                self._queue.put(pct)
        return len(text)

    def flush(self) -> None:
        self._real.flush()


@app.function(
    image=image,
    # H100: measured ~3x faster than the original A10G on BS-Roformer (54s vs 162s for a 4-minute
    # song at the model's default overlap), so roughly the same cost per song at Modal's
    # per-second billing despite the higher hourly rate.
    gpu="H100",
    volumes={MODEL_CACHE_DIR: model_cache_volume},
    timeout=600,
)
def separate_remote(
    audio_bytes: bytes,
    filename: str,
    model_name: str = DEFAULT_MODEL,
    overlap: int | None = None,
    with_alignment_emissions: bool = False,
) -> Iterator[str | dict[str, bytes]]:
    """A generator function (called via `.remote_gen(...)`) so progress can stream back to the
    caller: yields `"NN%"` strings as separation progresses, then a final `{"vocals.wav": ...,
    "instrumental.wav": ...}` dict once done. The actual separation call is blocking, so it runs
    in a background thread while this (main) thread relays whatever that thread's redirected
    stderr picks up from tqdm via a queue - `sys.stderr` is process-global, not thread-local, but
    that's fine here since each container handles one job at a time (no concurrent invocations
    configured on this function).
    """
    import tempfile

    progress_queue: Queue = Queue()
    outcome: dict = {}

    def _run() -> None:
        real_stderr = sys.stderr
        sys.stderr = _ProgressCapture(real_stderr, progress_queue)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_dir = Path(tmp)
                input_path = tmp_dir / filename
                input_path.write_bytes(audio_bytes)

                result = separate_audio(input_path, tmp_dir / "stems", model_name=model_name, overlap=overlap)
                model_cache_volume.commit()

                outcome["stems"] = {
                    "vocals.wav": result.vocals_path.read_bytes(),
                    "instrumental.wav": result.instrumental_path.read_bytes(),
                }
                if with_alignment_emissions:
                    # The alignment's only expensive step (its acoustic model over the vocals)
                    # takes ~12s on the Mac's MPS but ~1-2s here, and the vocals are already on
                    # this GPU box - so it runs here and only the small result (~1 MB) travels.
                    from karaoke_he.alignment import compute_emissions

                    emissions_path = tmp_dir / ALIGNMENT_EMISSIONS_NAME
                    compute_emissions(result.vocals_path).save(emissions_path)
                    model_cache_volume.commit()
                    outcome["stems"][ALIGNMENT_EMISSIONS_NAME] = emissions_path.read_bytes()
        except Exception as exc:  # noqa: BLE001 - re-raised in the main thread below
            outcome["error"] = exc
        finally:
            sys.stderr = real_stderr
            progress_queue.put(None)  # sentinel: worker thread is done

    worker = threading.Thread(target=_run)
    worker.start()

    while True:
        item = progress_queue.get()
        if item is None:
            break
        yield f"{item}%"

    worker.join()
    if "error" in outcome:
        raise outcome["error"]
    yield outcome["stems"]


@app.local_entrypoint()
def main(input: str, song_slug: str, model_name: str = DEFAULT_MODEL) -> None:
    input_path = Path(input).expanduser()
    stems: dict[str, bytes] = {}
    for item in separate_remote.remote_gen(input_path.read_bytes(), input_path.name, model_name):
        if isinstance(item, dict):
            stems = item
        else:
            print(f"progress: {item}")

    work_dir = Path(__file__).resolve().parents[1] / "work" / song_slug / "stems"
    work_dir.mkdir(parents=True, exist_ok=True)
    for name, data in stems.items():
        out_path = work_dir / name
        out_path.write_bytes(data)
        print(f"{name}: {out_path} ({len(data) / 1024 / 1024:.1f} MB)")
