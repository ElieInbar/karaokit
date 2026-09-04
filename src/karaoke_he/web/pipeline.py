import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from karaoke_he.alignment import align_lyrics, summarize_low_confidence_regions
from karaoke_he.lyrics import LyricsNotFoundError, resolve_lyrics
from karaoke_he.mux import mux_video
from karaoke_he.separation import separate_audio_remote
from karaoke_he.subtitle_render import burn_overlay, render_overlay_video

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Placeholder background: point 3 of the roadmap (a real background video) is deliberately
# deferred - see CLAUDE.md. Generated fresh per job since each song has a different duration.
PLACEHOLDER_BACKGROUND_COLOR = "0x1a1a2e"


class PipelineError(RuntimeError):
    """A pipeline stage failed with a user-facing cause (not found on LRCLIB, ffmpeg failure,
    etc.), as opposed to an unexpected bug."""


@dataclass
class PipelineResult:
    output_path: Path
    warnings: list[str] = field(default_factory=list)


def _format_low_confidence_warnings(words) -> list[str]:
    """Turns `alignment.summarize_low_confidence_regions` into user-facing warning strings (in
    French, matching the current frontend locale - see CLAUDE.md for the pending English
    translation of the whole app, not done piecemeal here).

    Surfacing this matters because forced alignment must map every lyric word somewhere across
    the *entire* audio - if the audio actually contains something the lyrics text doesn't
    account for (a spoken passage, a foreign-language aside, an extra chorus repeat), nearby
    words get shoved to implausible timestamps instead of the pipeline just failing loudly.
    """
    return [
        f"Timing incertain vers {region.start:.0f}s-{region.end:.0f}s ({region.word_count} mot"
        f"{'s' if region.word_count != 1 else ''}) - l'audio contient probablement du contenu "
        "parlé ou non chanté absent des paroles récupérées."
        for region in summarize_low_confidence_regions(words)
    ]


def _generate_placeholder_background(duration: float, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c={PLACEHOLDER_BACKGROUND_COLOR}:s=1920x1080:r=25",
        "-t",
        f"{duration + 0.5:.2f}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise PipelineError(f"ffmpeg failed generating the placeholder background (exit {result.returncode})")
    return output_path


def run_pipeline(
    song_slug: str,
    input_audio: Path,
    artist: str,
    title: str,
    *,
    album: str | None = None,
    manual_lyrics: str | None = None,
    on_progress: Callable[[str], None] = lambda stage: None,
) -> PipelineResult:
    """Runs the full song -> karaoke .mkv pipeline, reusing each already-validated stage
    unchanged: separation (Modal GPU), lyrics resolution, forced alignment, the animated
    subtitle overlay, and muxing. `on_progress` is called with a short stage name before each
    stage starts, for a caller (the web job runner) to surface status.

    `manual_lyrics`, if given, is written to `songs/<song_slug>/lyrics.txt` before resolving
    lyrics, so it's picked up by `resolve_lyrics`'s existing LRCLIB-then-manual-fallback logic
    unchanged - this is how a user unblocks a song LRCLIB doesn't have without touching the
    filesystem directly, since the "fully automatic" pipeline otherwise has no way to ask for
    lyrics mid-run.
    """
    work_dir = PROJECT_ROOT / "work" / song_slug
    work_dir.mkdir(parents=True, exist_ok=True)

    on_progress("separating")
    local_input = work_dir / f"input{Path(input_audio).suffix}"
    local_input.write_bytes(Path(input_audio).read_bytes())
    separation = separate_audio_remote(local_input, work_dir / "stems")

    on_progress("lyrics")
    if manual_lyrics:
        song_dir = PROJECT_ROOT / "songs" / song_slug
        song_dir.mkdir(parents=True, exist_ok=True)
        (song_dir / "lyrics.txt").write_text(manual_lyrics, encoding="utf-8")
    try:
        lyrics_result = resolve_lyrics(
            song_slug,
            artist,
            title,
            songs_dir=PROJECT_ROOT / "songs",
            album=album,
            duration_sec=separation.duration_sec,
        )
    except LyricsNotFoundError as exc:
        raise PipelineError(str(exc)) from exc

    lyrics_dir = work_dir / "lyrics"
    lyrics_dir.mkdir(parents=True, exist_ok=True)
    (lyrics_dir / "final.txt").write_text(lyrics_result.text + "\n", encoding="utf-8")
    lyrics_lines = [line for line in lyrics_result.text.splitlines() if line.strip()]

    on_progress("aligning")
    words = align_lyrics(separation.vocals_path, lyrics_result.text)
    warnings = _format_low_confidence_warnings(words)

    on_progress("rendering_subtitles")
    background_path = _generate_placeholder_background(
        separation.duration_sec, work_dir / "mux" / "background.mp4"
    )
    overlay_path = work_dir / "subtitles" / "overlay.mov"
    composited_path = work_dir / "subtitles" / "background_with_subs.mp4"
    render_overlay_video(lyrics_lines, words, overlay_path, duration=separation.duration_sec)
    burn_overlay(background_path, overlay_path, composited_path)

    on_progress("muxing")
    output_path = mux_video(
        composited_path,
        separation.instrumental_path,
        separation.vocals_path,
        work_dir / "mux" / f"{song_slug}_karaoke.mkv",
    )

    on_progress("done")
    return PipelineResult(output_path=output_path, warnings=warnings)
