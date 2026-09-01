from karaoke_he.alignment import WordTimestamp
from karaoke_he.subtitles import SubtitleLine
from karaoke_he.subtitle_render import (
    BASE_COLOR,
    CANVAS_SIZE,
    HIGHLIGHT_COLOR,
    _lerp_color,
    render_line_frame,
)


def test_lerp_color_at_zero_returns_first_color():
    assert _lerp_color(BASE_COLOR, HIGHLIGHT_COLOR, 0.0) == BASE_COLOR


def test_lerp_color_at_one_returns_second_color():
    assert _lerp_color(BASE_COLOR, HIGHLIGHT_COLOR, 1.0) == HIGHLIGHT_COLOR


def test_lerp_color_clamps_outside_unit_range():
    assert _lerp_color(BASE_COLOR, HIGHLIGHT_COLOR, -5.0) == BASE_COLOR
    assert _lerp_color(BASE_COLOR, HIGHLIGHT_COLOR, 5.0) == HIGHLIGHT_COLOR


def test_lerp_color_midpoint_is_between_endpoints():
    mid = _lerp_color((0, 0, 0, 255), (100, 200, 50, 255), 0.5)
    assert mid == (50, 100, 25, 255)


def _line():
    return SubtitleLine(
        words=[
            WordTimestamp("שלום", 1.0, 1.5, score=0.0),
            WordTimestamp("עולם", 1.5, 2.0, score=0.0),
        ]
    )


def test_render_line_frame_returns_full_canvas_rgba_image():
    frame = render_line_frame(_line(), 1.2)
    assert frame.mode == "RGBA"
    assert frame.size == CANVAS_SIZE


def test_render_line_frame_has_visible_pixels_when_line_is_active():
    frame = render_line_frame(_line(), 1.2)
    alpha_channel = frame.split()[-1]
    assert alpha_channel.getextrema()[1] > 0  # at least one non-transparent pixel


def test_render_line_frame_draws_text_even_before_the_first_word_starts():
    # render_line_frame draws whatever state applies at t with no notion of a display window -
    # windowing (skipping frames outside a line's display span) is render_overlay_video's job.
    frame = render_line_frame(_line(), 0.0)
    alpha_channel = frame.split()[-1]
    assert alpha_channel.getextrema()[1] > 0
