import subprocess
from collections.abc import Callable
from pathlib import Path

import arabic_reshaper
from bidi.algorithm import get_display
from PIL import Image, ImageDraw, ImageFont

from karaoke_he.alignment import WordTimestamp
from karaoke_he.languages import DEFAULT_LANGUAGE, LANGUAGES
from karaoke_he.subtitles import SubtitleLine, _line_display_start, group_words_by_line
from karaoke_he.text_utils import strip_display_punctuation

# Covers Latin + Hebrew + Arabic + Cyrillic (and more) in one file, so every currently supported
# language renders correctly without a per-language font map.
FONT_PATH = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"
FONT_SIZE = 65  # matches the .ass Style's Fontsize on a 1920x1080 canvas
CANVAS_SIZE = (1920, 1080)
MARGIN_BOTTOM = 90

BASE_COLOR = (255, 255, 255, 255)
HIGHLIGHT_COLOR = (255, 210, 0, 255)
OUTLINE_COLOR = (0, 0, 0, 255)
OUTLINE_WIDTH = 3

# How long a word takes to glow from base to highlight color once it starts being sung. A hard
# instant color swap (the .ass version) read as cheap/jarring in motion even though each static
# frame was individually correct; a short crossfade is a real animation instead of a jump-cut.
TRANSITION_SEC = 0.15

_font = ImageFont.truetype(FONT_PATH, FONT_SIZE)


def _lerp_color(a: tuple[int, int, int, int], b: tuple[int, int, int, int], t: float) -> tuple[int, int, int, int]:
    t = max(0.0, min(1.0, t))
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(4))


def _draw_chunk(draw: ImageDraw.ImageDraw, x: float, y: float, text: str, color) -> float:
    """Draws one already-bidi-ordered chunk of text and returns its rendered width."""
    if not text:
        return 0.0
    draw.text(
        (x, y), text, font=_font, fill=color, stroke_width=OUTLINE_WIDTH, stroke_fill=OUTLINE_COLOR
    )
    return draw.textlength(text, font=_font)


def _to_display_text(raw_text: str, language: str) -> str:
    """Bidi-reorders one already-isolated chunk of text for rendering. Arabic additionally needs
    contextual letter-shaping (initial/medial/final joined forms) before bidi reordering - neither
    Pillow (no libraqm) nor python-bidi provides this, and without it Arabic text renders as
    disconnected isolated letters. Hebrew (and every other currently supported language) doesn't
    need this step; `get_display` itself is a no-op on plain LTR text, so it's always safe to
    call unconditionally."""
    if LANGUAGES[language].arabic_shaping:
        raw_text = arabic_reshaper.reshape(raw_text)
    return get_display(raw_text)


def render_line_frame(
    line: SubtitleLine, t: float, canvas_size: tuple[int, int] = CANVAS_SIZE, language: str = DEFAULT_LANGUAGE
) -> Image.Image:
    """One frame of a lyric line at time `t`: words fully sung so far in HIGHLIGHT_COLOR, the
    word currently being sung crossfading from BASE_COLOR to HIGHLIGHT_COLOR over its first
    TRANSITION_SEC, and the rest in BASE_COLOR.

    Chunks are laid out in the language's own reading order - sung-so-far nearest the reading
    start, unsung nearest the reading end - matching the natural reading-order placement
    validated for the .ass 2-run version. For RTL languages (Hebrew, Arabic) reading starts on
    the right, so the sung chunk is drawn rightmost; for LTR languages reading starts on the
    left, so it's drawn leftmost instead (drawing an LTR line in RTL chunk order would make the
    highlight visibly grow backwards, right-to-left, on a line that reads left-to-right). Each
    chunk's own text is bidi-reordered independently via `_to_display_text`, which is safe here
    because each chunk is a contiguous run of the original line and Pillow (no libraqm) draws
    whatever string it's given in plain left-to-right glyph order.
    """
    img = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    sung_count = sum(1 for w in line.words if w.end <= t)
    active = next((w for w in line.words if w.start <= t < w.end), None)
    unsung_start = sung_count + (1 if active else 0)

    sung_text = _to_display_text(
        " ".join(strip_display_punctuation(w.word) for w in line.words[:sung_count]), language
    )
    unsung_text = _to_display_text(
        " ".join(strip_display_punctuation(w.word) for w in line.words[unsung_start:]), language
    )
    active_text = _to_display_text(strip_display_punctuation(active.word), language) if active else ""
    active_color = (
        _lerp_color(BASE_COLOR, HIGHLIGHT_COLOR, (t - active.start) / TRANSITION_SEC) if active else BASE_COLOR
    )

    space_width = draw.textlength(" ", font=_font)
    # Chunks are drawn left-to-right in list order (the draw loop below accumulates x left to
    # right). RTL reading starts on the right, so the sung chunk - nearest the reading start -
    # must be drawn LAST (rightmost); LTR reading starts on the left, so sung must be drawn
    # FIRST (leftmost) instead.
    ordered = (
        [(unsung_text, BASE_COLOR), (active_text, active_color), (sung_text, HIGHLIGHT_COLOR)]
        if LANGUAGES[language].is_rtl
        else [(sung_text, HIGHLIGHT_COLOR), (active_text, active_color), (unsung_text, BASE_COLOR)]
    )
    chunks = [c for c in ordered if c[0]]

    widths = [draw.textlength(text, font=_font) for text, _ in chunks]
    total_width = sum(widths) + space_width * max(0, len(chunks) - 1)

    x = (canvas_size[0] - total_width) / 2
    y = canvas_size[1] - MARGIN_BOTTOM - FONT_SIZE
    for text, color in chunks:
        x += _draw_chunk(draw, x, y, text, color) + space_width

    return img


class OverlayRenderError(RuntimeError):
    """ffmpeg failed while rendering the karaoke video."""


# Lyrics only ever occupy the bottom of the frame (one line, MARGIN_BOTTOM above the bottom
# edge), so only that band is rendered and composited - about a fifth of the pixels of a full
# frame. Tall enough for the font's ascenders/descenders plus the outline; even, for yuv420p.
BAND_HEIGHT = 200

# Measured on a 4-minute song: libx264 "veryfast" at CRF 23 encoded as fast as Apple's hardware
# encoder (~27s either way - decoding the background and compositing dominate, not encoding)
# while producing a 2-3x smaller file (9.9 MB vs 21 MB at comparable quality).
_ENCODER_ARGS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]


def _line_windows(lyrics_lines: list[str], words: list[WordTimestamp]) -> list[tuple[float, float, SubtitleLine]]:
    windows: list[tuple[float, float, SubtitleLine]] = []
    previous_end = 0.0
    for line in group_words_by_line(lyrics_lines, words):
        start = _line_display_start(line, previous_end)
        end = line.words[-1].end
        windows.append((start, end, line))
        previous_end = end
    return windows


def _frame_state(line: SubtitleLine, t: float) -> tuple:
    """Everything `render_line_frame` depends on at time `t`, so frames with the same state can
    reuse one rendered image: most frames of a song are identical to the previous one (nothing
    changes between word boundaries), only the short crossfades actually animate."""
    sung_count = sum(1 for w in line.words if w.end <= t)
    active_idx = next((i for i, w in enumerate(line.words) if w.start <= t < w.end), None)
    fade = None
    if active_idx is not None:
        fade = round(min(1.0, max(0.0, (t - line.words[active_idx].start) / TRANSITION_SEC)), 3)
    return (sung_count, active_idx, fade)


def render_karaoke_video(
    lyrics_lines: list[str],
    words: list[WordTimestamp],
    background_video: Path,
    output_path: Path,
    *,
    duration: float,
    fps: int = 25,
    language: str = DEFAULT_LANGUAGE,
    on_progress: Callable[[str], None] = lambda message: None,
) -> Path:
    """Renders the karaoke highlight and burns it onto `background_video` in a single ffmpeg pass,
    producing the final H.264 video track.

    This replaced a two-pass version (every frame rendered at full size into an alpha ProRes
    file, then a second ffmpeg run to composite and re-encode it) that took ~3.5 minutes for a
    4-minute song. Now: only the bottom lyrics band is rendered, each distinct frame state is
    drawn once (`_frame_state`) and its bytes reused for identical frames, frames are piped
    straight into the compositing ffmpeg (no intermediate file), and the result is
    encoded with libx264 (see `_ENCODER_ARGS`).
    """
    background_video = Path(background_video)
    if not background_video.is_file():
        raise FileNotFoundError(f"Background video not found: {background_video}")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    return _render_karaoke_video(
        lyrics_lines, words, background_video, output_path, duration, fps, language, on_progress, _ENCODER_ARGS
    )


def _render_karaoke_video(
    lyrics_lines, words, background_video, output_path, duration, fps, language, on_progress, encoder_args
) -> Path:
    windows = _line_windows(lyrics_lines, words)
    band_size = (CANVAS_SIZE[0], BAND_HEIGHT)
    band_y = CANVAS_SIZE[1] - BAND_HEIGHT

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(background_video),
        "-f", "rawvideo", "-pixel_format", "rgba",
        "-video_size", f"{band_size[0]}x{band_size[1]}",
        "-framerate", str(fps),
        "-i", "-",
        # Converting the small RGBA band to yuva420p first and overlaying in yuv420 keeps the
        # (big) background in its native format; letting ffmpeg pick (format=auto, then
        # converting) measured ~4.5s slower on a 4-minute song for the same output.
        "-filter_complex",
        f"[1:v]format=yuva420p[band];[0:v][band]overlay=0:{band_y}:shortest=1:format=yuv420[v]",
        "-map", "[v]",
        *encoder_args,
        str(output_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    blank = Image.new("RGBA", band_size, (0, 0, 0, 0)).tobytes()
    cache: dict[tuple, bytes] = {}
    window_idx = 0
    n_frames = int(duration * fps) + 1
    progress_step = max(1, n_frames // 100)
    try:
        for i in range(n_frames):
            t = i / fps
            while window_idx < len(windows) - 1 and t >= windows[window_idx][1]:
                window_idx += 1
            start, end, line = windows[window_idx] if windows else (0.0, 0.0, None)
            if line is not None and start <= t < end:
                key = (window_idx, *_frame_state(line, t))
                frame = cache.get(key)
                if frame is None:
                    frame = render_line_frame(line, t, band_size, language).tobytes()
                    cache[key] = frame
                    if len(cache) > 64:  # states never repeat once a line is over
                        cache.pop(next(iter(cache)))
                proc.stdin.write(frame)
            else:
                proc.stdin.write(blank)
            if i % progress_step == 0 or i == n_frames - 1:
                on_progress(f"{round(100 * i / (n_frames - 1)) if n_frames > 1 else 100}%")
    except BrokenPipeError:
        pass  # ffmpeg exited early; its return code below says why
    finally:
        try:
            proc.stdin.close()
        except BrokenPipeError:
            pass
        proc.wait()

    if proc.returncode != 0:
        raise OverlayRenderError(f"ffmpeg failed rendering {output_path} (exit code {proc.returncode})")
    return output_path
