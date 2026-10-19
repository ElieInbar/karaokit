import hashlib
import subprocess
from concurrent.futures import ThreadPoolExecutor

import soundfile as sf
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from karaoke_he.alignment import (
    AcousticEmissions,
    WordTimestamp,
    align_lyrics,
    compute_emissions,
    find_parenthetical_word_indices,
    load_diagnostic_model,
    summarize_low_confidence_regions,
    transcribe_diagnostic_region,
)
from karaoke_he.background import render_vinyl_background
from karaoke_he.languages import DEFAULT_LANGUAGE, LANGUAGES
from karaoke_he.lyrics import LyricsNotFoundError, fetch_lrclib_by_id, load_manual_fallback, resolve_lyrics
from karaoke_he.mux import encode_aac, mux_video
from karaoke_he import config
from karaoke_he.separation import ALIGNMENT_EMISSIONS_NAME, APP_OVERLAP, separate_audio, separate_audio_remote
from karaoke_he.subtitle_render import render_karaoke_video
from karaoke_he.text_utils import normalize_hebrew
from karaoke_he.web.library import write_job_record
from karaoke_he.youtube import download_youtube_audio


# Minimum region size (in low-confidence words) worth running diagnostic language-ID on. Tested
# on a real song: single isolated words are usually just ordinary alignment noise, and Whisper's
# language detection on a clip that short is unreliable (wildly different, implausible languages
# guessed for different 1-word blips in the same song) - not worth surfacing at all below this.
MIN_WORDS_FOR_DIAGNOSIS = 3


class PipelineError(RuntimeError):
    """A pipeline stage failed with a user-facing cause (not found on LRCLIB, ffmpeg failure,
    etc.), as opposed to an unexpected bug."""


@dataclass
class PipelineResult:
    output_path: Path
    warnings: list[str] = field(default_factory=list)
    # Same video + the instrumental re-encoded as AAC in an .mp4, for in-browser playback only:
    # browsers can't play the .mkv's uncompressed PCM audio (and Safari can't play .mkv at all).
    preview_path: Path | None = None


def _region_overlaps_parentheses(region, words: list[WordTimestamp], parenthetical_indices: set[int]) -> bool:
    """Whether any word in this low-confidence region came from a parenthesized span in the
    original lyrics text (see `find_parenthetical_word_indices`). Correlated by time rather than
    index, since `LowConfidenceRegion` only carries a time range, not indices."""
    return any(
        region.start <= w.start and w.end <= region.end and i in parenthetical_indices
        for i, w in enumerate(words)
    )


def _format_low_confidence_warnings(words: list[WordTimestamp], vocals_path: Path, lyrics_text: str) -> list[str]:
    """Turns `alignment.summarize_low_confidence_regions` into user-facing warning strings (in
    French, matching the current frontend locale - see CLAUDE.md for the pending English
    translation of the whole app, not done piecemeal here).

    Surfacing this matters because forced alignment must map every lyric word somewhere across
    the *entire* audio - if the audio actually contains something the lyrics text doesn't
    account for (a spoken passage, a foreign-language aside, an extra chorus repeat), nearby
    words get shoved to implausible timestamps instead of the pipeline just failing loudly.

    A region whose words were parenthesized in the source lyrics (`find_parenthetical_word_
    indices`) gets a different, more specific message: parentheses commonly mark backing-vocal
    ad-libs/harmonies, which are often sung *simultaneously* with the main line rather than
    sequentially in a gap - something forced alignment (which assumes one sequential vocal
    stream) has no way to represent, so timing there can be off even though the text genuinely
    was in the lyrics. The generic "content missing from the lyrics" message would be actively
    wrong in that case, since nothing was actually missing.

    Non-parenthetical, *substantial* regions (>= `MIN_WORDS_FOR_DIAGNOSIS` words - see that
    constant) also get their spoken language auto-detected (`transcribe_diagnostic_region`),
    since "somewhere around here is off" is much less actionable than "the audio here doesn't
    sound like the song's declared language". Isolated 1-2 word regions are skipped: tested on a
    real song, Whisper's language ID on a clip that short is unreliable (wildly different,
    implausible languages guessed for single-word blips that are more likely just ordinary
    alignment noise) - only worth trusting, even loosely, once there's a few seconds of real
    audio to work with. The transcribed text itself is never included in the warning (it may be
    part of the song's actual, possibly copyrighted content), only the detected language, and
    even that is presented as a hint rather than a fact, since language ID on sung (not spoken)
    audio is inherently less reliable than its usual speech use case.
    """
    regions = summarize_low_confidence_regions(words)
    if not regions:
        return []

    parenthetical_indices = find_parenthetical_word_indices(lyrics_text)
    model = None
    warnings = []
    for region in regions:
        word_label = f"{region.word_count} word{'s' if region.word_count != 1 else ''}"
        time_range = _format_time_range(region.start, region.end)

        if _region_overlaps_parentheses(region, words, parenthetical_indices):
            warnings.append(
                f"Timing may be approximate around {time_range} ({word_label}) - this part comes "
                "from parenthesized lyrics (usually backing vocals/ad-libs), probably sung at the "
                "same time as the main line. Alignment can only follow one voice at a time, so the "
                "timing here may be off even though the text itself is right."
            )
            continue

        detail = ""
        if region.word_count >= MIN_WORDS_FOR_DIAGNOSIS:
            model = model or load_diagnostic_model()
            language, text = transcribe_diagnostic_region(model, vocals_path, region.start, region.end)
            if text:
                detail = f" (possible language, just a hint - unreliable on singing: {language})"
        warnings.append(
            f"Uncertain timing around {time_range} ({word_label}) - the audio probably contains "
            f"spoken or unsung content that isn't in the fetched lyrics{detail}."
        )
    return warnings


def _format_time(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def _format_time_range(start: float, end: float) -> str:
    """`m:ss`, or `m:ss–m:ss` when the region spans more than a rounded second."""
    start_str, end_str = _format_time(start), _format_time(end)
    return start_str if start_str == end_str else f"{start_str}–{end_str}"


STEMS_SOURCE_STAMP = ".source-sha256"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stems_are_stale(stems_dir: Path, input_hash: str) -> bool:
    """Separation output is cached per song slug (artist + title), so a re-run with a *different*
    audio file under the same names - another upload, a live version, another YouTube video -
    would otherwise silently reuse the old stems. The stems dir records which input it came from;
    a missing record (stems from before this check existed) counts as stale too."""
    stamp = stems_dir / STEMS_SOURCE_STAMP
    return not stamp.is_file() or stamp.read_text().strip() != input_hash


def _load_or_compute_emissions(
    emissions_path: Path, vocals_path: Path, *, on_progress: Callable[[str], None]
) -> AcousticEmissions:
    """The alignment's acoustic-model output for these stems: normally computed on the Modal GPU
    right after separation and saved next to the stems (`separation.ALIGNMENT_EMISSIONS_NAME`);
    otherwise (stems cached from before that existed) computed locally once and saved there, so
    a re-run of the same song skips it either way."""
    if emissions_path.is_file():
        try:
            return AcousticEmissions.load(emissions_path)
        except (OSError, ValueError, KeyError):
            pass  # unreadable/partial file - recompute below
    emissions = compute_emissions(vocals_path, on_progress=on_progress)
    emissions.save(emissions_path)
    return emissions


def make_browser_preview(video_path: Path, instrumental_path: Path, output_path: Path) -> Path:
    """Muxes the (already h264) karaoke video with the instrumental into a fast-start .mp4 that
    browsers can play inline. The video stream is copied, not re-encoded, so this is quick; the
    audio is copied too when it's already AAC (the pipeline's .m4a), else encoded."""
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-i",
        str(instrumental_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        *(["-c:a", "copy"] if Path(instrumental_path).suffix == ".m4a" else ["-c:a", "aac", "-b:a", "192k"]),
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise PipelineError(f"ffmpeg failed building the browser preview (exit {result.returncode})")
    return output_path


def _resolve_job_lyrics(
    song_slug: str,
    artist: str,
    title: str,
    manual_lyrics: str | None,
    *,
    lrclib_id: int | None = None,
    album: str | None,
    duration_sec: float,
    songs_dir: Path | None = None,
):
    """Lyrics the user pasted or edited in the form win over LRCLIB: they're either the only
    lyrics available, or a deliberate correction - silently replacing them with LRCLIB's version
    (`resolve_lyrics`' normal order) would throw that correction away. They're also saved to
    `songs/<slug>/lyrics.txt`, so a later run without pasted lyrics still has them as a fallback."""
    songs_dir = songs_dir or config.SONGS_DIR
    if manual_lyrics and manual_lyrics.strip():
        song_dir = songs_dir / song_slug
        song_dir.mkdir(parents=True, exist_ok=True)
        (song_dir / "lyrics.txt").write_text(manual_lyrics, encoding="utf-8")
        result = load_manual_fallback(song_slug, songs_dir)
        if result is not None:
            return result
    if lrclib_id is not None:
        # The exact song picked in the search box - not a fresh artist/title search, which
        # could land on a different version than the lyrics the user saw.
        result = fetch_lrclib_by_id(lrclib_id)
        if result is not None:
            return result
    try:
        return resolve_lyrics(
            song_slug, artist, title, songs_dir=songs_dir, album=album, duration_sec=duration_sec
        )
    except LyricsNotFoundError as exc:
        raise PipelineError(str(exc)) from exc


def run_pipeline(
    song_slug: str,
    input_audio: Path | None,
    artist: str,
    title: str,
    *,
    youtube_url: str | None = None,
    lrclib_id: int | None = None,
    album: str | None = None,
    manual_lyrics: str | None = None,
    language: str = DEFAULT_LANGUAGE,
    on_progress: Callable[[str, str | None], None] = lambda stage, detail: None,
) -> PipelineResult:
    """Runs the full song -> karaoke .mkv pipeline, reusing each already-validated stage
    unchanged: separation (Modal GPU), lyrics resolution, forced alignment, the animated
    subtitle overlay, and muxing. `on_progress(stage, detail)` is called with a short stage name
    before each stage starts (`detail=None`), and again with a finer-grained `detail` string
    whenever a stage itself reports progress (downloading/separation's "NN%", alignment's named
    sub-steps, subtitle rendering's "NN%"), for a caller (the web job runner) to surface status.

    Exactly one of `input_audio` (a local file, e.g. an upload) or `youtube_url` must be given;
    when `youtube_url` is set, the audio is downloaded as the pipeline's first stage instead of
    copying a local file in.

    `manual_lyrics`, if given, is written to `songs/<song_slug>/lyrics.txt` and used as-is,
    *ahead of* LRCLIB - it's either the only lyrics available (a song LRCLIB doesn't have) or a
    deliberate correction of LRCLIB's version. Without it, `resolve_lyrics`' normal
    LRCLIB-then-manual-file order applies.

    `language` is one of `languages.LANGUAGES`'s keys (an ISO-639-3 code); the caller (`jobs.py`)
    is expected to have already validated it.
    """
    work_dir = config.WORK_DIR / song_slug
    work_dir.mkdir(parents=True, exist_ok=True)

    if youtube_url:
        on_progress("downloading", None)
        local_input = download_youtube_audio(
            youtube_url, work_dir, filename_stem="input", on_progress=lambda msg: on_progress("downloading", msg)
        )
    else:
        local_input = work_dir / f"input{Path(input_audio).suffix}"
        local_input.write_bytes(Path(input_audio).read_bytes())

    # Steps that don't depend on what comes before them run alongside it instead of after: the
    # background only needs the song's duration (known from the input file), so it's built while
    # the GPU separates; the AAC encodes only need the stems, so they run while the karaoke
    # video renders.
    pool = ThreadPoolExecutor(max_workers=3)
    background_future = pool.submit(
        render_vinyl_background,
        sf.info(str(local_input)).duration,
        work_dir / "mux" / "background.mp4",
        title=title,
        artist=artist,
        language=language,
    )

    on_progress("separating", None)
    stems_dir = work_dir / "stems"
    input_hash = _file_sha256(local_input)
    stale = _stems_are_stale(stems_dir, input_hash)
    if config.separation_backend() == "modal":
        separation = separate_audio_remote(
            local_input,
            stems_dir,
            overwrite=stale,
            app_name=config.MODAL_APP_NAME,
            with_alignment_emissions=True,
            on_progress=lambda msg: on_progress("separating", msg),
        )
    else:
        # No account needed, but slow (~10-15 min for a 4-minute song on a laptop, with the same
        # overlap as on Modal); progress and cancellation work through audio-separator's tqdm
        # output. The alignment's acoustic model then also runs locally (below).
        if stale:
            (stems_dir / ALIGNMENT_EMISSIONS_NAME).unlink(missing_ok=True)
        separation = separate_audio(
            local_input,
            stems_dir,
            overwrite=stale,
            overlap=APP_OVERLAP,
            on_progress=lambda msg: on_progress("separating", msg),
        )
    (stems_dir / STEMS_SOURCE_STAMP).write_text(input_hash)

    on_progress("lyrics", None)
    lyrics_result = _resolve_job_lyrics(
        song_slug, artist, title, manual_lyrics, lrclib_id=lrclib_id, album=album, duration_sec=separation.duration_sec
    )

    lang_config = LANGUAGES[language]
    lyrics_text = lyrics_result.text
    if lang_config.strip_nikud:
        # Must normalize before both the line-split and align_lyrics below, so the two sides of
        # subtitles.group_words_by_line's word-count check see the identical text - normalizing
        # only one side would break that invariant.
        lyrics_text = normalize_hebrew(lyrics_text)

    lyrics_dir = work_dir / "lyrics"
    lyrics_dir.mkdir(parents=True, exist_ok=True)
    (lyrics_dir / "final.txt").write_text(lyrics_text + "\n", encoding="utf-8")
    lyrics_lines = [line for line in lyrics_text.splitlines() if line.strip()]

    on_progress("aligning", None)
    emissions = _load_or_compute_emissions(
        stems_dir / ALIGNMENT_EMISSIONS_NAME,
        separation.vocals_path,
        on_progress=lambda msg: on_progress("aligning", msg),
    )
    words = align_lyrics(
        separation.vocals_path,
        lyrics_text,
        language=lang_config.iso639_3,
        emissions=emissions,
        on_progress=lambda msg: on_progress("aligning", msg),
    )
    # alignment.try_correct_alignment (splice out low-confidence regions and re-align) isn't run:
    # it never produced a better result in testing (its safety net always fell back to the
    # original) and cost a whole extra local alignment pass. Kept in alignment.py for experiments.
    warnings = _format_low_confidence_warnings(words, separation.vocals_path, lyrics_text)

    on_progress("rendering_subtitles", None)
    instrumental_aac_future = pool.submit(
        encode_aac, separation.instrumental_path, work_dir / "mux" / "instrumental.m4a", bitrate="192k"
    )
    vocals_aac_future = pool.submit(encode_aac, separation.vocals_path, work_dir / "mux" / "vocals.m4a", bitrate="128k")
    background_path = background_future.result()
    composited_path = work_dir / "subtitles" / "background_with_subs.mp4"
    render_karaoke_video(
        lyrics_lines,
        words,
        background_path,
        composited_path,
        duration=separation.duration_sec,
        language=language,
        on_progress=lambda msg: on_progress("rendering_subtitles", msg),
    )
    # Intermediates nothing reads afterwards: the background is cheap to regenerate (~2s), and
    # overlay.mov is a ~350 MB leftover of the old two-pass renderer in older work dirs.
    background_path.unlink(missing_ok=True)
    (work_dir / "subtitles" / "overlay.mov").unlink(missing_ok=True)

    on_progress("muxing", None)
    instrumental_aac, vocals_aac = instrumental_aac_future.result(), vocals_aac_future.result()
    pool.shutdown()
    output_path = mux_video(
        composited_path,
        instrumental_aac,
        vocals_aac,
        work_dir / "mux" / f"{song_slug}_karaoke.mkv",
        language=lang_config.bcp47,
    )
    preview_path = make_browser_preview(
        composited_path, instrumental_aac, work_dir / "mux" / f"{song_slug}_preview.mp4"
    )

    write_job_record(
        work_dir,
        artist=artist,
        title=title,
        language=language,
        source=youtube_url or (Path(input_audio).name if input_audio else None),
    )

    on_progress("done", None)
    return PipelineResult(output_path=output_path, warnings=warnings, preview_path=preview_path)
