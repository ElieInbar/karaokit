"""The karaoke video's background: a "Vinyl" scene matching the web app - warm near-black, the
song's title and artist top-left, and a spinning record in the top-right corner, clear of the
lyric lines at the bottom (see `subtitle_render.MARGIN_BOTTOM`).

Only two still images are drawn (Pillow): the static scene and the record itself. ffmpeg then
renders exactly one turn of the record over the scene (a seamless loop, since the last frame
leads straight back into the first) and repeats that clip - without re-encoding - for the whole
song. Rotating a large image every frame for a full song took ~2 minutes; this takes seconds.
"""

import math
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from karaoke_he.languages import DEFAULT_LANGUAGE
from karaoke_he.subtitle_render import CANVAS_SIZE, FONT_PATH, _to_display_text, scaled

BG_COLOR = (29, 25, 21)  # web app's dark "paper"
TITLE_COLOR = (243, 236, 224)
ARTIST_COLOR = (185, 176, 163)
GROOVE_DARK = (11, 9, 7)
GROOVE_LIGHT = (46, 40, 34)
LABEL_RED = (226, 73, 47)
LABEL_RED_DARK = (201, 59, 35)

RECORD_DIAMETER = scaled(1190)
RECORD_X = CANVAS_SIZE[0] - RECORD_DIAMETER + scaled(270)  # partly off the right edge
RECORD_Y = -scaled(330)  # and off the top, clear of the lyric lines at the bottom
SECONDS_PER_TURN = 8.0
FPS = 25

TEXT_LEFT = scaled(115)
TITLE_TOP = scaled(150)
TITLE_MAX_WIDTH = scaled(820)  # stays clear of the record
TITLE_MAX_SIZE = scaled(120)
TITLE_MIN_SIZE = scaled(60)
ARTIST_SIZE = scaled(46)


class BackgroundError(RuntimeError):
    """ffmpeg failed while building the background video."""


def _fit_font(text: str, max_width: int) -> tuple[ImageFont.FreeTypeFont, str]:
    """Largest title font that fits `max_width`; below the minimum size, the text is truncated
    with an ellipsis instead of shrinking further."""
    for size in range(TITLE_MAX_SIZE, TITLE_MIN_SIZE - 1, -2):
        font = ImageFont.truetype(FONT_PATH, size)
        if font.getlength(text) <= max_width:
            return font, text
    font = ImageFont.truetype(FONT_PATH, TITLE_MIN_SIZE)
    while text and font.getlength(text + "…") > max_width:
        text = text[:-1]
    return font, text.rstrip() + "…"


def _draw_scene(title: str, artist: str | None, language: str) -> Image.Image:
    scene = Image.new("RGB", CANVAS_SIZE, BG_COLOR)
    draw = ImageDraw.Draw(scene)
    # Truncate in logical order (before bidi reordering), so an RTL title loses its *end*.
    title_font, title_text = _fit_font(title.strip(), TITLE_MAX_WIDTH)
    draw.text((TEXT_LEFT, TITLE_TOP), _to_display_text(title_text, language), font=title_font, fill=TITLE_COLOR)
    if artist:
        artist_font = ImageFont.truetype(FONT_PATH, ARTIST_SIZE)
        artist_text = artist.strip().upper()
        while artist_text and artist_font.getlength(artist_text) > TITLE_MAX_WIDTH:
            artist_text = artist_text[:-1]
        y = TITLE_TOP + title_font.size + scaled(40)
        draw.text((TEXT_LEFT, y), _to_display_text(artist_text, language), font=artist_font, fill=ARTIST_COLOR)
    return scene


def _draw_record() -> Image.Image:
    """The record, RGBA. Grooves alone are rotationally symmetric - spinning them would look
    static - so the label carries a couple of off-center marks that make the rotation visible."""
    d = RECORD_DIAMETER
    record = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    draw = ImageDraw.Draw(record)
    c = d / 2
    groove_step = max(4, scaled(7))
    for i, r in enumerate(range(int(c), int(d * 0.17), -groove_step)):
        color = GROOVE_LIGHT if i % 2 else GROOVE_DARK
        draw.ellipse((c - r, c - r, c + r, c + r), fill=color)

    label_r = d * 0.17
    draw.ellipse((c - label_r, c - label_r, c + label_r, c + label_r), fill=LABEL_RED)
    ring = label_r * 0.92
    draw.ellipse((c - ring, c - ring, c + ring, c + ring), outline=LABEL_RED_DARK, width=scaled(6))
    # Off-center "printed" marks on the label.
    for angle, length in ((-40, 0.55), (-20, 0.35)):
        a = math.radians(angle)
        x0, y0 = c + math.cos(a) * label_r * 0.35, c + math.sin(a) * label_r * 0.35
        x1, y1 = c + math.cos(a) * label_r * (0.35 + length), c + math.sin(a) * label_r * (0.35 + length)
        draw.line((x0, y0, x1, y1), fill=TITLE_COLOR, width=scaled(12))
    hole = d * 0.012
    draw.ellipse((c - hole, c - hole, c + hole, c + hole), fill=BG_COLOR)
    return record


def render_vinyl_background(
    duration: float,
    output_path: Path,
    *,
    title: str,
    artist: str | None = None,
    language: str = DEFAULT_LANGUAGE,
) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        scene_path = Path(tmp) / "scene.png"
        record_path = Path(tmp) / "record.png"
        _draw_scene(title, artist, language).save(scene_path)
        _draw_record().save(record_path)

        rotate = f"rotate=t*2*PI/{SECONDS_PER_TURN}:c=none:ow=iw:oh=ih"
        loop_path = Path(tmp) / "one_turn.mp4"
        one_turn = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-loop", "1", "-framerate", str(FPS), "-i", str(scene_path),
            "-loop", "1", "-framerate", str(FPS), "-i", str(record_path),
            "-filter_complex",
            f"[1:v]format=rgba,{rotate}[rec];[0:v][rec]overlay=x={RECORD_X}:y={RECORD_Y}:shortest=1,format=yuv420p",
            "-frames:v", str(round(SECONDS_PER_TURN * FPS)),
            "-c:v", "libx264", "-preset", "veryfast",
            str(loop_path),
        ]
        result = subprocess.run(one_turn)
        if result.returncode == 0:
            repeat = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-stream_loop", "-1", "-i", str(loop_path),
                "-t", f"{duration + 0.5:.2f}",
                "-c", "copy",
                str(output_path),
            ]
            result = subprocess.run(repeat)
    if result.returncode != 0:
        raise BackgroundError(f"ffmpeg failed building the background video (exit {result.returncode})")
    return output_path
