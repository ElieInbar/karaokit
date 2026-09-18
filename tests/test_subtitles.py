import pytest

from karaoke_he.alignment import WordTimestamp
from karaoke_he.subtitles import (
    SubtitleError,
    SubtitleLine,
    format_ass_time,
    generate_ass,
    group_words_by_line,
)


def _w(word: str, start: float, end: float) -> WordTimestamp:
    return WordTimestamp(word=word, start=start, end=end, score=-0.1)


def test_format_ass_time_formats_hours_minutes_seconds_centiseconds():
    assert format_ass_time(3725.678) == "1:02:05.68"


def test_format_ass_time_clamps_negative_to_zero():
    assert format_ass_time(-1.0) == "0:00:00.00"


def test_group_words_by_line_splits_by_word_count():
    words = [_w("שלום", 0.0, 0.5), _w("עולם", 0.5, 1.0), _w("שוב", 1.0, 1.5)]
    lines = group_words_by_line(["שלום עולם", "שוב"], words)
    assert len(lines) == 2
    assert [w.word for w in lines[0].words] == ["שלום", "עולם"]
    assert [w.word for w in lines[1].words] == ["שוב"]


def test_group_words_by_line_skips_empty_lines():
    words = [_w("שלום", 0.0, 0.5)]
    lines = group_words_by_line(["שלום", ""], words)
    assert len(lines) == 1


def test_group_words_by_line_raises_on_count_mismatch():
    words = [_w("שלום", 0.0, 0.5)]
    with pytest.raises(SubtitleError, match="mismatch"):
        group_words_by_line(["שלום עולם"], words)


def test_generate_ass_includes_header_and_events():
    words = [_w("שלום", 5.0, 5.5), _w("עולם", 5.5, 6.0)]
    ass = generate_ass(["שלום עולם"], words)
    assert "[Script Info]" in ass
    assert "[Events]" in ass
    assert ass.count("Dialogue:") == 1


def test_generate_ass_one_event_per_line_with_full_text():
    words = [_w("שלום", 5.0, 5.5), _w("עולם", 5.5, 6.0)]
    ass = generate_ass(["שלום עולם"], words)
    dialogue = [line for line in ass.splitlines() if line.startswith("Dialogue:")][0]
    assert dialogue.endswith("שלום עולם")
    assert "\\c" not in dialogue


def test_generate_ass_event_spans_from_lead_in_to_last_word_end():
    words = [_w("שלום", 5.0, 5.5), _w("עולם", 5.5, 6.0)]
    ass = generate_ass(["שלום עולם"], words)
    dialogue = [line for line in ass.splitlines() if line.startswith("Dialogue:")][0]
    _, start, end = dialogue.split(",", 3)[:3]
    assert start == format_ass_time(4.0)  # 1s min lead-in before the first word at 5.0s
    assert end == format_ass_time(6.0)  # last word's end


def test_generate_ass_second_line_does_not_overlap_first():
    words = [_w("שלום", 0.0, 0.3), _w("עולם", 5.0, 5.3)]
    ass = generate_ass(["שלום", "עולם"], words)
    dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    first_line_end = dialogues[0].split(",")[2]
    second_line_start = dialogues[1].split(",")[1]
    assert second_line_start >= first_line_end


def test_subtitle_line_text_strips_display_punctuation():
    words = [_w("(ouh,", 0.0, 0.5), _w("nanana)", 0.5, 1.0)]
    line = SubtitleLine(words=words)
    assert line.text == "ouh nanana"


def test_generate_ass_strips_display_punctuation_from_dialogue_text():
    words = [_w("(ouh,", 5.0, 5.5), _w("nanana)", 5.5, 6.0)]
    ass = generate_ass(["(ouh, nanana)"], words)
    dialogue = [line for line in ass.splitlines() if line.startswith("Dialogue:")][0]
    assert dialogue.endswith("ouh nanana")


def test_generate_ass_extends_lead_in_for_fast_short_lines():
    words = [_w("שלום עולם שוב היום", 10.0, 10.1)]
    long_line = SubtitleLine(words=words)
    from karaoke_he.subtitles import _line_display_start

    start = _line_display_start(long_line, previous_line_end=0.0)
    assert start < 10.0 - 1.0  # more than the minimum 1s lead-in for a long, fast line
