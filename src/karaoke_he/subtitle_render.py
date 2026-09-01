import subprocess
from pathlib import Path

from bidi.algorithm import get_display
from PIL import Image, ImageDraw, ImageFont

from karaoke_he.alignment import WordTimestamp
from karaoke_he.subtitles import SubtitleLine, _line_display_start, group_words_by_line

FONT_PATH = "/System/Library/Fonts/ArialHB.ttc"
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


def render_line_frame(line: SubtitleLine, t: float, canvas_size: tuple[int, int] = CANVAS_SIZE) -> Image.Image:
    """One frame of a lyric line at time `t`: words fully sung so far in HIGHLIGHT_COLOR, the
    word currently being sung crossfading from BASE_COLOR to HIGHLIGHT_COLOR over its first
    TRANSITION_SEC, and the rest in BASE_COLOR.

    Chunks are assembled right-to-left (sung chunk rightmost, i.e. drawn last/last-in-storage)
    to match the same natural reading-order placement validated for the .ass 2-run version -
    each chunk's own text is bidi-reordered independently via `get_display`, which is safe here
    because each chunk is a contiguous run of the original line and Pillow (no libraqm) draws
    whatever string it's given in plain left-to-right glyph order.
    """
    img = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    sung_count = sum(1 for w in line.words if w.end <= t)
    active = next((w for w in line.words if w.start <= t < w.end), None)
    unsung_start = sung_count + (1 if active else 0)

    sung_text = get_display(" ".join(w.word for w in line.words[:sung_count]))
    unsung_text = get_display(" ".join(w.word for w in line.words[unsung_start:]))
    active_text = get_display(active.word) if active else ""
    active_color = (
        _lerp_color(BASE_COLOR, HIGHLIGHT_COLOR, (t - active.start) / TRANSITION_SEC) if active else BASE_COLOR
    )

    space_width = draw.textlength(" ", font=_font)
    chunks = [(unsung_text, BASE_COLOR), (active_text, active_color), (sung_text, HIGHLIGHT_COLOR)]
    chunks = [c for c in chunks if c[0]]

    widths = [draw.textlength(text, font=_font) for text, _ in chunks]
    total_width = sum(widths) + space_width * max(0, len(chunks) - 1)

    x = (canvas_size[0] - total_width) / 2
    y = canvas_size[1] - MARGIN_BOTTOM - FONT_SIZE
    for text, color in chunks:
        x += _draw_chunk(draw, x, y, text, color) + space_width

    return img


class OverlayRenderError(RuntimeError):
    """ffmpeg failed to encode the rendered overlay frames."""


def render_overlay_video(
    lyrics_lines: list[str],
    words: list[WordTimestamp],
    output_path: Path,
    *,
    duration: float,
    fps: int = 25,
    canvas_size: tuple[int, int] = CANVAS_SIZE,
) -> Path:
    """Renders the whole song's karaoke highlight as a standalone alpha-channel video (ProRes
    4444), one frame at a time, piped straight into ffmpeg rather than written to disk as a PNG
    sequence. Meant to be composited onto the background video with ffmpeg's `overlay` filter
    before muxing - hard-burning it in, since this replaces the .ass soft-subtitle track rather
    than complementing it.
    """
    windows: list[tuple[float, float, SubtitleLine]] = []
    previous_end = 0.0
    for line in group_words_by_line(lyrics_lines, words):
        start = _line_display_start(line, previous_end)
        end = line.words[-1].end
        windows.append((start, end, line))
        previous_end = end

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pixel_format",
        "rgba",
        "-video_size",
        f"{canvas_size[0]}x{canvas_size[1]}",
        "-framerate",
        str(fps),
        "-i",
        "-",
        "-c:v",
        "prores_ks",
        "-profile:v",
        "4444",
        "-pix_fmt",
        "yuva444p10le",
        str(output_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    blank = Image.new("RGBA", canvas_size, (0, 0, 0, 0)).tobytes()
    window_idx = 0
    n_frames = int(duration * fps) + 1
    try:
        for i in range(n_frames):
            t = i / fps
            while window_idx < len(windows) - 1 and t >= windows[window_idx][1]:
                window_idx += 1
            start, end, line = windows[window_idx] if windows else (0.0, 0.0, None)
            if line is not None and start <= t < end:
                proc.stdin.write(render_line_frame(line, t, canvas_size).tobytes())
            else:
                proc.stdin.write(blank)
    finally:
        proc.stdin.close()
        proc.wait()

    if proc.returncode != 0:
        raise OverlayRenderError(f"ffmpeg failed rendering {output_path} (exit code {proc.returncode})")

    return output_path


def burn_overlay(background_video: Path, overlay_video: Path, output_path: Path) -> Path:
    """Composites the rendered overlay (with alpha) onto the background video, hard-burning the
    karaoke highlight into the video track."""
    background_video = Path(background_video)
    overlay_video = Path(overlay_video)
    if not background_video.is_file():
        raise FileNotFoundError(f"Background video not found: {background_video}")
    if not overlay_video.is_file():
        raise FileNotFoundError(f"Overlay video not found: {overlay_video}")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(background_video),
        "-i",
        str(overlay_video),
        "-filter_complex",
        "[0:v][1:v]overlay=0:0:format=auto[v]",
        "-map",
        "[v]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "18",
        str(output_path),
    ]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise OverlayRenderError(f"ffmpeg failed compositing {output_path} (exit code {result.returncode})")

    return output_path
