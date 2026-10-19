import re
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import soundfile as sf
from audio_separator.separator import Separator

DEFAULT_MODEL = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"

# Overlapping prediction windows used by the web app (remote and local). Compute time is roughly
# proportional to it; measured on a 4-minute song on an H100: 54s at the model's default vs 28s
# at 2, with the separated vocals within 28.5 dB SNR of the default's (a difference judged
# inaudible for a karaoke instrumental). `separate_audio` itself still defaults to the model's.
APP_OVERLAP = 2
REMOTE_OVERLAP = APP_OVERLAP  # kept for older imports

# audio-separator has no progress callback (only a plain tqdm bar around its chunk loop), so
# progress is read back out of tqdm's rendered text.
_TQDM_PERCENT_RE = re.compile(r"(\d{1,3})%\|")


class _TqdmProgressCapture:
    """A stderr stand-in that passes everything through to the real stderr (logs unaffected)
    and calls `on_percent` with each new tqdm percentage. An exception raised by `on_percent`
    (e.g. a job's cancellation) propagates out of tqdm's write - i.e. out of the separation
    loop itself, which is how a running separation gets stopped."""

    def __init__(self, real_stream, on_percent: Callable[[int], None]) -> None:
        self._real = real_stream
        self._on_percent = on_percent
        self._last_pct: int | None = None

    def write(self, text: str) -> int:
        self._real.write(text)
        match = _TQDM_PERCENT_RE.search(text)
        if match:
            pct = int(match.group(1))
            if pct != self._last_pct:
                self._last_pct = pct
                self._on_percent(pct)
        return len(text)

    def flush(self) -> None:
        self._real.flush()

    def __getattr__(self, name):
        return getattr(self._real, name)


@contextmanager
def capture_tqdm_progress(on_percent: Callable[[int], None]) -> Iterator[None]:
    """Redirects `sys.stderr` (process-wide, not per thread) to report tqdm percentages."""
    real = sys.stderr
    sys.stderr = _TqdmProgressCapture(real, on_percent)
    try:
        yield
    finally:
        sys.stderr = real

# Written next to the stems when the remote separation also computed the alignment's acoustic
# model output (`alignment.AcousticEmissions`); it belongs to these exact stems.
ALIGNMENT_EMISSIONS_NAME = "alignment_emissions.npz"
DURATION_TOLERANCE_SEC = 1.0


@dataclass
class SeparationResult:
    vocals_path: Path
    instrumental_path: Path
    model_name: str
    duration_sec: float


class SeparationError(RuntimeError):
    """Separation failed: missing stem output, or output duration inconsistent with the source."""


def separate_audio(
    input_audio: Path,
    output_dir: Path,
    *,
    model_name: str = DEFAULT_MODEL,
    overwrite: bool = False,
    force_cpu: bool = False,
    overlap: int | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> SeparationResult:
    """
    on_progress: called with "NN%" as separation advances (read from audio-separator's tqdm
    output); an exception it raises aborts the separation.

    overlap: how many overlapping prediction windows cover each point of the audio (Roformer
    models); compute time is roughly proportional to it. None keeps the model's own default.

    force_cpu: skip the MPS (Apple Silicon GPU) backend and run on CPU instead. MPS was
    observed to silently die mid-inference on this machine (progressively slower chunks,
    then the process vanished with no traceback) - likely a memory-pressure issue with this
    model on an 16GB Mac. CPU is slower but doesn't exhibit that failure.
    """
    input_audio = Path(input_audio)
    output_dir = Path(output_dir)
    if not input_audio.is_file():
        raise FileNotFoundError(f"Input audio not found: {input_audio}")

    output_dir.mkdir(parents=True, exist_ok=True)
    vocals_path = output_dir / "vocals.wav"
    instrumental_path = output_dir / "instrumental.wav"
    source_duration = sf.info(str(input_audio)).duration

    if not overwrite and vocals_path.exists() and instrumental_path.exists():
        return SeparationResult(vocals_path, instrumental_path, model_name, source_duration)

    if force_cpu:
        import torch

        torch.backends.mps.is_available = lambda: False

    separator = Separator(
        output_dir=str(output_dir),
        output_format="WAV",
        mdxc_params={
            "segment_size": 256,
            "override_model_segment_size": False,
            "batch_size": None,
            "overlap": overlap,
            "pitch_shift": 0,
        },
    )
    separator.load_model(model_filename=model_name)
    if on_progress is None:
        output_files = separator.separate(str(input_audio))
    else:
        with capture_tqdm_progress(lambda pct: on_progress(f"{pct}%")):
            output_files = separator.separate(str(input_audio))

    vocals_out = _find_stem(output_files, "vocals")
    instrumental_out = _find_stem(output_files, "instrumental")
    if vocals_out is None or instrumental_out is None:
        raise SeparationError(
            f"Could not find 'Vocals'/'Instrumental' stems among separator outputs: {output_files}"
        )

    _resolve(output_dir, vocals_out).replace(vocals_path)
    _resolve(output_dir, instrumental_out).replace(instrumental_path)

    _validate_stem_durations(vocals_path, instrumental_path, source_duration, model_name)
    return SeparationResult(vocals_path, instrumental_path, model_name, source_duration)


def separate_audio_remote(
    input_audio: Path,
    output_dir: Path,
    *,
    model_name: str = DEFAULT_MODEL,
    overwrite: bool = False,
    app_name: str = "karaoke-he-separation",
    function_name: str = "separate_job",
    overlap: int | None = APP_OVERLAP,
    with_alignment_emissions: bool = False,
    on_progress: Callable[[str], None] = lambda message: None,
) -> SeparationResult:
    """Same contract as `separate_audio`, but runs on the Modal-deployed GPU function
    (`deploy/modal_app.py`) - ~30-40s on an H100 vs. ~20-30 minutes on a laptop. Requires
    `modal deploy deploy/modal_app.py` to have been run once in the user's Modal account.

    The function is *spawned* (not streamed) so it can be cancelled: if `on_progress` raises
    (a job's cancellation) or anything else interrupts the wait, the remote call is cancelled
    and its container terminated, instead of the GPU running on to completion for nothing.
    Progress ("NN%") comes back through a temporary Modal queue the function writes to.
    """
    input_audio = Path(input_audio)
    output_dir = Path(output_dir)
    if not input_audio.is_file():
        raise FileNotFoundError(f"Input audio not found: {input_audio}")

    output_dir.mkdir(parents=True, exist_ok=True)
    vocals_path = output_dir / "vocals.wav"
    instrumental_path = output_dir / "instrumental.wav"
    source_duration = sf.info(str(input_audio)).duration

    if not overwrite and vocals_path.exists() and instrumental_path.exists():
        return SeparationResult(vocals_path, instrumental_path, model_name, source_duration)

    import queue

    import modal
    import modal.exception

    separate_fn = modal.Function.from_name(app_name, function_name)
    (output_dir / ALIGNMENT_EMISSIONS_NAME).unlink(missing_ok=True)  # replaced (or not) with the stems

    with modal.Queue.ephemeral() as progress_queue:
        call = separate_fn.spawn(
            input_audio.read_bytes(),
            input_audio.name,
            model_name,
            overlap=overlap,
            with_alignment_emissions=with_alignment_emissions,
            progress_queue=progress_queue,
        )
        try:
            while True:
                try:
                    stems = call.get(timeout=0)
                    break
                except (TimeoutError, modal.exception.TimeoutError):
                    pass
                try:
                    for message in progress_queue.get_many(100, block=True, timeout=1.0):
                        on_progress(str(message))
                except queue.Empty:
                    pass
        except BaseException:
            try:
                call.cancel(terminate_containers=True)
            except Exception:  # noqa: BLE001 - best effort; the original error matters more
                pass
            raise

    if not stems or "vocals.wav" not in stems:
        raise SeparationError("Modal separation function ended without producing stems.")

    vocals_path.write_bytes(stems["vocals.wav"])
    instrumental_path.write_bytes(stems["instrumental.wav"])
    if ALIGNMENT_EMISSIONS_NAME in stems:
        (output_dir / ALIGNMENT_EMISSIONS_NAME).write_bytes(stems[ALIGNMENT_EMISSIONS_NAME])

    _validate_stem_durations(vocals_path, instrumental_path, source_duration, model_name)
    return SeparationResult(vocals_path, instrumental_path, model_name, source_duration)


def _validate_stem_durations(
    vocals_path: Path, instrumental_path: Path, source_duration: float, model_name: str
) -> None:
    for label, path in (("vocals", vocals_path), ("instrumental", instrumental_path)):
        duration = sf.info(str(path)).duration
        if abs(duration - source_duration) > DURATION_TOLERANCE_SEC:
            raise SeparationError(
                f"{label} duration ({duration:.1f}s) does not match source "
                f"({source_duration:.1f}s) for model {model_name!r}"
            )


def _find_stem(output_files: list[str], keyword: str) -> str | None:
    for path in output_files:
        if keyword in Path(path).stem.lower():
            return path
    return None


def _resolve(output_dir: Path, raw_path: str) -> Path:
    """`Separator.separate()` sometimes returns filenames relative to output_dir instead of
    fully-qualified paths; join them explicitly rather than relying on the caller's cwd."""
    path = Path(raw_path)
    return path if path.is_absolute() else output_dir / path
