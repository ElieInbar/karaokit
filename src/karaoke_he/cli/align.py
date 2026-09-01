import json
from pathlib import Path

import typer

from karaoke_he.alignment import DEFAULT_LANGUAGE, AlignmentError, align_lyrics, low_confidence_words

PROJECT_ROOT = Path(__file__).resolve().parents[3]

app = typer.Typer(add_completion=False)


@app.command()
def main(
    song_slug: str = typer.Option(..., "--song-slug", help="Working directory name under work/"),
    language: str = typer.Option(DEFAULT_LANGUAGE, "--language", help="ISO-639-3 language code"),
    force_cpu: bool = typer.Option(False, "--cpu", help="Force CPU inference instead of MPS/CUDA"),
) -> None:
    work_dir = PROJECT_ROOT / "work" / song_slug
    vocals_path = work_dir / "stems" / "vocals.wav"
    lyrics_path = work_dir / "lyrics" / "final.txt"

    if not vocals_path.is_file():
        typer.echo(f"Error: vocals not found at {vocals_path} (run the separation stage first).", err=True)
        raise typer.Exit(1)
    if not lyrics_path.is_file():
        typer.echo(f"Error: lyrics not found at {lyrics_path} (run the lyrics stage first).", err=True)
        raise typer.Exit(1)

    lyrics_text = lyrics_path.read_text(encoding="utf-8")

    try:
        words = align_lyrics(vocals_path, lyrics_text, language=language, force_cpu=force_cpu)
    except AlignmentError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    alignment_dir = work_dir / "alignment"
    alignment_dir.mkdir(parents=True, exist_ok=True)
    output_path = alignment_dir / "words.json"
    output_path.write_text(
        json.dumps(
            [{"word": w.word, "start": w.start, "end": w.end, "score": w.score} for w in words],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    typer.echo(f"aligned words: {len(words)}")
    typer.echo(f"first word: {words[0].word!r} at {words[0].start:.2f}s")
    typer.echo(f"last word: {words[-1].word!r} ending at {words[-1].end:.2f}s")
    typer.echo(f"written: {output_path}")

    weak = low_confidence_words(words)
    if weak:
        typer.echo(f"warning: {len(weak)} low-confidence word(s), timestamps may be unreliable:", err=True)
        for word in weak:
            typer.echo(f"  {word.word!r} at {word.start:.2f}-{word.end:.2f}s (score={word.score:.2f})", err=True)


if __name__ == "__main__":
    app()
