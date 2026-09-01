import json
from pathlib import Path

import typer

from karaoke_he.alignment import WordTimestamp
from karaoke_he.subtitles import SubtitleError, generate_ass

PROJECT_ROOT = Path(__file__).resolve().parents[3]

app = typer.Typer(add_completion=False)


@app.command()
def main(
    song_slug: str = typer.Option(..., "--song-slug", help="Working directory name under work/"),
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

    try:
        ass_content = generate_ass(lyrics_lines, words)
    except SubtitleError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    subtitles_dir = work_dir / "subtitles"
    subtitles_dir.mkdir(parents=True, exist_ok=True)
    output_path = subtitles_dir / "karaoke.ass"
    output_path.write_text(ass_content, encoding="utf-8")

    n_lines = ass_content.count("Dialogue:")
    typer.echo(f"events written: {n_lines}")
    typer.echo(f"written: {output_path}")


if __name__ == "__main__":
    app()
