from dataclasses import dataclass
from pathlib import Path

import soundfile as sf
from audio_separator.separator import Separator

DEFAULT_MODEL = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
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
) -> SeparationResult:
    """
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

    separator = Separator(output_dir=str(output_dir), output_format="WAV")
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

    for label, path in (("vocals", vocals_path), ("instrumental", instrumental_path)):
        duration = sf.info(str(path)).duration
        if abs(duration - source_duration) > DURATION_TOLERANCE_SEC:
            raise SeparationError(
                f"{label} duration ({duration:.1f}s) does not match source "
                f"({source_duration:.1f}s) for model {model_name!r}"
            )

    return SeparationResult(vocals_path, instrumental_path, model_name, source_duration)


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
