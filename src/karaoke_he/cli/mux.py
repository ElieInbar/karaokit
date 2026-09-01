from pathlib import Path

import typer

from karaoke_he.mux import DEFAULT_LANGUAGE, MuxError, mux_video

PROJECT_ROOT = Path(__file__).resolve().parents[3]

app = typer.Typer(add_completion=False)


@app.command()
def main(
    song_slug: str = typer.Option(..., "--song-slug", help="Working directory name under work/"),
    video: Path = typer.Option(
        ..., "--video", exists=True, help="Background video, with the karaoke overlay already burned in"
    ),
    language: str = typer.Option(DEFAULT_LANGUAGE, "--language", help="BCP 47 language tag"),
) -> None:
    work_dir = PROJECT_ROOT / "work" / song_slug

    try:
        output_path = mux_video(
            video,
            work_dir / "stems" / "instrumental.wav",
            work_dir / "stems" / "vocals.wav",
            work_dir / "mux" / f"{song_slug}_karaoke.mkv",
            language=language,
        )
    except (FileNotFoundError, MuxError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    typer.echo(f"written: {output_path} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    app()
