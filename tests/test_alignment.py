from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import karaoke_he.alignment as alignment_module
from karaoke_he.alignment import (
    AlignmentError,
    AudioSplice,
    WordTimestamp,
    _build_trimmed_audio,
    _validate_alignment,
    low_confidence_words,
    summarize_low_confidence_regions,
    try_correct_alignment,
)


def test_validate_alignment_accepts_matching_monotonic_words():
    words = [
        WordTimestamp("שלום", 0.0, 0.5, score=0.9),
        WordTimestamp("עולם", 0.5, 1.0, score=0.9),
    ]
    _validate_alignment(words, expected_word_count=2)


def test_validate_alignment_rejects_empty_words():
    with pytest.raises(AlignmentError, match="no words"):
        _validate_alignment([], expected_word_count=2)


def test_validate_alignment_rejects_large_word_count_mismatch():
    words = [WordTimestamp("שלום", 0.0, 0.5, score=0.9)]
    with pytest.raises(AlignmentError, match="word count"):
        _validate_alignment(words, expected_word_count=20)


def test_validate_alignment_tolerates_small_word_count_mismatch():
    words = [WordTimestamp(f"word{i}", float(i), float(i) + 0.5, score=0.9) for i in range(18)]
    _validate_alignment(words, expected_word_count=20)


def test_validate_alignment_rejects_non_monotonic_timestamps():
    words = [
        WordTimestamp("שלום", 1.0, 1.5, score=0.9),
        WordTimestamp("עולם", 1.2, 2.0, score=0.9),
    ]
    with pytest.raises(AlignmentError, match="Non-monotonic"):
        _validate_alignment(words, expected_word_count=2)


def test_validate_alignment_rejects_end_before_start():
    words = [WordTimestamp("שלום", 1.0, 0.5, score=0.9)]
    with pytest.raises(AlignmentError, match="Non-monotonic"):
        _validate_alignment(words, expected_word_count=1)


def test_low_confidence_words_filters_below_threshold():
    words = [
        WordTimestamp("שלום", 0.0, 0.5, score=-0.2),
        WordTimestamp("עולם", 0.5, 1.0, score=-6.6),
    ]
    assert low_confidence_words(words) == [words[1]]


def test_low_confidence_words_empty_when_all_confident():
    words = [WordTimestamp("שלום", 0.0, 0.5, score=-0.05)]
    assert low_confidence_words(words) == []


def test_low_confidence_words_custom_threshold():
    words = [WordTimestamp("שלום", 0.0, 0.5, score=-1.5)]
    assert low_confidence_words(words, threshold=-1.0) == words
    assert low_confidence_words(words, threshold=-2.0) == []


def test_summarize_low_confidence_regions_empty_when_none_weak():
    words = [WordTimestamp("שלום", 0.0, 0.5, score=-0.1)]
    assert summarize_low_confidence_regions(words) == []


def test_summarize_low_confidence_regions_merges_close_words():
    words = [
        WordTimestamp("א", 10.0, 10.5, score=-6.0),
        WordTimestamp("ב", 11.0, 11.5, score=-6.0),
        WordTimestamp("ג", 12.0, 12.5, score=-6.0),
    ]
    regions = summarize_low_confidence_regions(words, gap_sec=5.0)
    assert len(regions) == 1
    assert regions[0].start == 10.0
    assert regions[0].end == 12.5
    assert regions[0].word_count == 3


def test_summarize_low_confidence_regions_splits_distant_words():
    words = [
        WordTimestamp("א", 10.0, 10.5, score=-6.0),
        WordTimestamp("ב", 100.0, 100.5, score=-6.0),
    ]
    regions = summarize_low_confidence_regions(words, gap_sec=5.0)
    assert len(regions) == 2
    assert [r.word_count for r in regions] == [1, 1]


def test_summarize_low_confidence_regions_ignores_confident_words_between_weak_ones():
    words = [
        WordTimestamp("א", 10.0, 10.5, score=-6.0),
        WordTimestamp("confident", 11.0, 11.5, score=-0.1),
        WordTimestamp("ב", 12.0, 12.5, score=-6.0),
    ]
    regions = summarize_low_confidence_regions(words, gap_sec=5.0)
    assert len(regions) == 1
    assert regions[0].word_count == 2


def test_audio_splice_maps_time_within_first_kept_range_unchanged():
    splice = AudioSplice(kept_ranges=[(0.0, 5.0), (8.0, 10.0)])
    assert splice.to_original_time(2.0) == 2.0


def test_audio_splice_maps_time_past_first_kept_range_into_second():
    splice = AudioSplice(kept_ranges=[(0.0, 5.0), (8.0, 10.0)])
    assert splice.to_original_time(6.0) == pytest.approx(9.0)


def test_build_trimmed_audio_shortens_duration_and_records_kept_ranges(tmp_path: Path):
    sample_rate = 8000
    duration = 10.0
    data = np.zeros(int(duration * sample_rate), dtype="float32")
    input_path = tmp_path / "input.wav"
    sf.write(input_path, data, sample_rate)

    output_path = tmp_path / "trimmed.wav"
    splice = _build_trimmed_audio(input_path, [(4.0, 6.0)], output_path)

    assert splice.kept_ranges == [(0.0, 4.0), (6.0, 10.0)]
    trimmed_duration = sf.info(str(output_path)).duration
    assert trimmed_duration == pytest.approx(8.0, abs=0.01)


def _fixture_words_with_substantial_low_confidence_region() -> list[WordTimestamp]:
    good = [WordTimestamp(f"word{i}", float(i), float(i) + 0.5, score=-0.1) for i in range(5)]
    weak = [WordTimestamp(f"weak{i}", 10.0 + i, 10.0 + i + 0.5, score=-6.0) for i in range(5)]
    return good + weak


def test_try_correct_alignment_skips_regions_below_min_words(tmp_path: Path, monkeypatch):
    words = [
        WordTimestamp("word0", 0.0, 0.5, score=-0.1),
        WordTimestamp("weak0", 5.0, 5.5, score=-6.0),
    ]

    def _fail_align_lyrics(*args, **kwargs):
        raise AssertionError("should not attempt re-alignment when no region meets min_words")

    monkeypatch.setattr(alignment_module, "align_lyrics", _fail_align_lyrics)

    result = try_correct_alignment(tmp_path / "vocals.wav", "text", words, min_words=5)
    assert result == words


def test_try_correct_alignment_rejects_on_word_count_mismatch(tmp_path: Path, monkeypatch):
    words = _fixture_words_with_substantial_low_confidence_region()
    sf.write(tmp_path / "vocals.wav", np.zeros(8000 * 15, dtype="float32"), 8000)

    monkeypatch.setattr(alignment_module, "align_lyrics", lambda *a, **k: words[:-1])

    result = try_correct_alignment(tmp_path / "vocals.wav", "text", words, min_words=5)
    assert result == words


def test_try_correct_alignment_rejects_when_not_actually_better(tmp_path: Path, monkeypatch):
    words = _fixture_words_with_substantial_low_confidence_region()
    sf.write(tmp_path / "vocals.wav", np.zeros(8000 * 15, dtype="float32"), 8000)

    # Same shape, same low scores - re-alignment on the trimmed audio "succeeded" but didn't help.
    monkeypatch.setattr(alignment_module, "align_lyrics", lambda *a, **k: list(words))

    result = try_correct_alignment(tmp_path / "vocals.wav", "text", words, min_words=5)
    assert result == words


def test_try_correct_alignment_accepts_when_it_actually_helps(tmp_path: Path, monkeypatch):
    words = _fixture_words_with_substantial_low_confidence_region()
    sf.write(tmp_path / "vocals.wav", np.zeros(8000 * 15, dtype="float32"), 8000)

    improved = [
        WordTimestamp(w.word, w.start, w.end, score=-0.1 if w.score < -1 else w.score) for w in words
    ]
    monkeypatch.setattr(alignment_module, "align_lyrics", lambda *a, **k: improved)

    result = try_correct_alignment(tmp_path / "vocals.wav", "text", words, min_words=5)
    assert result != words
    assert len(low_confidence_words(result)) == 0


def test_try_correct_alignment_rejects_on_alignment_error(tmp_path: Path, monkeypatch):
    words = _fixture_words_with_substantial_low_confidence_region()
    sf.write(tmp_path / "vocals.wav", np.zeros(8000 * 15, dtype="float32"), 8000)

    def _raise(*args, **kwargs):
        raise AlignmentError("boom")

    monkeypatch.setattr(alignment_module, "align_lyrics", _raise)

    result = try_correct_alignment(tmp_path / "vocals.wav", "text", words, min_words=5)
    assert result == words
