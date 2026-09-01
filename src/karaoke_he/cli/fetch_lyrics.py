import json
from pathlib import Path

import typer

from karaoke_he.lyrics import LyricsNotFoundError, LyricsSource, resolve_lyrics

PROJECT_ROOT = Path(__file__).resolve().parents[3]

app = typer.Typer(add_completion=False)


@app.command()
def main(
    song_slug: str = typer.Option(..., "--song-slug", help="Working directory name under work/"),
    artist: str = typer.Option(..., "--artist"),
    title: str = typer.Option(..., "--title"),
    album: str = typer.Option(None, "--album"),
    duration_sec: float = typer.Option(None, "--duration-sec"),
) -> None:
    songs_dir = PROJECT_ROOT / "songs"
    lyrics_dir = PROJECT_ROOT / "work" / song_slug / "lyrics"
    lyrics_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = resolve_lyrics(
            song_slug, artist, title, songs_dir=songs_dir, album=album, duration_sec=duration_sec
        )
    except LyricsNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    final_path = lyrics_dir / "final.txt"
    final_path.write_text(result.text + "\n", encoding="utf-8")

    if result.source is LyricsSource.LRCLIB and result.raw_response is not None:
        (lyrics_dir / "lrclib_response.json").write_text(
            json.dumps(result.raw_response, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    n_lines = len(result.text.splitlines())
    n_words = len(result.text.split())
    typer.echo(f"source: {result.source.value} (synced={result.synced})")
    typer.echo(f"lines: {n_lines}, words: {n_words}")
    typer.echo(f"written: {final_path}")


if __name__ == "__main__":
    app()
