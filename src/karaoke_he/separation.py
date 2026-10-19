from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import soundfile as sf
from audio_separator.separator import Separator

DEFAULT_MODEL = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"

# Overlapping prediction windows for the remote (web app) separation. Compute time is roughly
# proportional to it; measured on a 4-minute song on an H100: 54s at the model's default vs 28s
# at 2, with the separated vocals within 28.5 dB SNR of the default's (a difference judged
# inaudible for a karaoke instrumental). The local `separate_audio` keeps the model default.
REMOTE_OVERLAP = 2

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
) -> SeparationResult:
    """
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
    function_name: str = "separate_remote",
    overlap: int | None = REMOTE_OVERLAP,
    with_alignment_emissions: bool = False,
    on_progress: Callable[[str], None] = lambda message: None,
) -> SeparationResult:
    """Same contract as `separate_audio`, but runs on the Modal-deployed GPU function
    (`deploy/modal_app.py`) instead of locally - ~30-40s on an H100 vs. ~30 minutes on this
    machine's MPS. Requires `modal deploy deploy/modal_app.py` to have been run at least once;
    `modal` itself is only needed for this path, so it's imported lazily here rather than being a
    hard dependency of the whole package.

    `separate_remote` is a Modal *generator* function: it yields `"NN%"` progress strings (parsed
    server-side from audio-separator's own tqdm output, since it has no first-class progress
    callback) as separation runs, then a final `{"vocals.wav": ..., "instrumental.wav": ...}`
    dict. `on_progress` is called with each progress string as it arrives.
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

    import modal

    separate_remote_fn = modal.Function.from_name(app_name, function_name)
    stems: dict[str, bytes] | None = None
    stale_emissions = output_dir / ALIGNMENT_EMISSIONS_NAME
    stale_emissions.unlink(missing_ok=True)  # about to be replaced (or not produced) along with the stems
    for item in separate_remote_fn.remote_gen(
        input_audio.read_bytes(),
        input_audio.name,
        model_name,
        overlap=overlap,
        with_alignment_emissions=with_alignment_emissions,
    ):
        if isinstance(item, dict):
            stems = item
        else:
            on_progress(str(item))

    if stems is None:
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
