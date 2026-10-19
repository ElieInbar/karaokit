import subprocess

from karaoke_he import background
from karaoke_he.background import TITLE_MAX_WIDTH, _draw_scene, _fit_font, render_vinyl_background
from karaoke_he.subtitle_render import CANVAS_SIZE


def test_fit_font_shrinks_then_truncates_long_titles():
    font, text = _fit_font("Short", TITLE_MAX_WIDTH)
    assert text == "Short" and font.size == background.TITLE_MAX_SIZE

    long_title = "A really very extremely long song title that cannot possibly fit on one line"
    font, text = _fit_font(long_title, TITLE_MAX_WIDTH)
    assert font.size == background.TITLE_MIN_SIZE
    assert text.endswith("…") and font.getlength(text) <= TITLE_MAX_WIDTH


def test_title_stays_clear_of_the_record():
    assert background.TEXT_LEFT + TITLE_MAX_WIDTH < background.RECORD_X


def test_draw_scene_handles_rtl_and_arabic_titles():
    for title, artist, language in [("מביט מהצד", "Omer Adam", "heb"), ("أغنية", "فنان", "ara"), ("Song", None, "eng")]:
        scene = _draw_scene(title, artist, language)
        assert scene.size == CANVAS_SIZE


def test_render_vinyl_background_matches_requested_duration(tmp_path):
    out = render_vinyl_background(3.0, tmp_path / "bg.mp4", title="Test", artist="Artist", language="eng")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=width,height", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    )
    lines = probe.stdout.split()
    assert lines[0] == f"{CANVAS_SIZE[0]},{CANVAS_SIZE[1]}"
    assert abs(float(lines[1]) - 3.5) < 0.2
