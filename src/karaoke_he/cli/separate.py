import shutil
from pathlib import Path

import typer

from karaoke_he.separation import DEFAULT_MODEL, separate_audio, separate_audio_remote

PROJECT_ROOT = Path(__file__).resolve().parents[3]

app = typer.Typer(add_completion=False)


@app.command()
def main(
    input: Path = typer.Option(..., "--input", exists=True, help="Source audio file"),
    song_slug: str = typer.Option(..., "--song-slug", help="Working directory name under work/"),
    model_name: str = typer.Option(DEFAULT_MODEL, "--model-name"),
    overwrite: bool = typer.Option(False, "--overwrite"),
    cpu: bool = typer.Option(False, "--cpu", help="Force CPU inference instead of MPS (local only)"),
    remote: bool = typer.Option(
        False, "--remote", help="Run on the Modal-deployed GPU function instead of locally"
    ),
) -> None:
    work_dir = PROJECT_ROOT / "work" / song_slug
    work_dir.mkdir(parents=True, exist_ok=True)

    local_input = work_dir / f"input{input.suffix}"
    if overwrite or not local_input.exists():
        shutil.copy2(input, local_input)

    if remote:
        result = separate_audio_remote(
            local_input,
            work_dir / "stems",
            model_name=model_name,
            overwrite=overwrite,
            on_progress=lambda msg: typer.echo(f"progress: {msg}"),
        )
    else:
        result = separate_audio(
            local_input,
            work_dir / "stems",
            model_name=model_name,
            overwrite=overwrite,
            force_cpu=cpu,
        )

    typer.echo(f"model: {result.model_name}")
    typer.echo(f"source duration: {result.duration_sec:.1f}s")
    for label, path in (("vocals", result.vocals_path), ("instrumental", result.instrumental_path)):
        size_mb = path.stat().st_size / (1024 * 1024)
        typer.echo(f"{label}: {path} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    app()
