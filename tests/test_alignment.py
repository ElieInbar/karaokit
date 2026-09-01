import pytest

from karaoke_he.alignment import AlignmentError, WordTimestamp, _validate_alignment, low_confidence_words


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
