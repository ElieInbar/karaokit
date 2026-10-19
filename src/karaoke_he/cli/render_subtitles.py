import json
import subprocess
from pathlib import Path

import typer

from karaoke_he.alignment import WordTimestamp
from karaoke_he.subtitle_render import OverlayRenderError, render_karaoke_video

PROJECT_ROOT = Path(__file__).resolve().parents[3]

app = typer.Typer(add_completion=False)


def _probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


@app.command()
def main(
    song_slug: str = typer.Option(..., "--song-slug", help="Working directory name under work/"),
    background_video: Path = typer.Option(..., "--background-video", exists=True),
    fps: int = typer.Option(25, "--fps"),
) -> None:
    work_dir = PROJECT_ROOT / "work" / song_slug
    lyrics_path = work_dir / "lyrics" / "final.txt"
    words_path = work_dir / "alignment" / "words.json"

    if not lyrics_path.is_file():
        typer.echo(f"Error: lyrics not found at {lyrics_path} (run the lyrics stage first).", err=True)
        raise typer.Exit(1)
    if not words_path.is_file():
        typer.echo(f"Error: alignment not found at {words_path} (run the alignment stage first).", err=True)
        raise typer.Exit(1)

    lyrics_lines = [line for line in lyrics_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    words = [WordTimestamp(**w) for w in json.loads(words_path.read_text(encoding="utf-8"))]
    duration = _probe_duration(background_video)

    subtitles_dir = work_dir / "subtitles"
    output_path = subtitles_dir / "background_with_subs.mp4"

    try:
        render_karaoke_video(lyrics_lines, words, background_video, output_path, duration=duration, fps=fps)
    except OverlayRenderError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    typer.echo(f"written: {output_path}")


if __name__ == "__main__":
    app()
