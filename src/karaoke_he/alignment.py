from dataclasses import dataclass
from pathlib import Path

import torch
from ctc_forced_aligner import (
    generate_emissions,
    get_alignments,
    get_spans,
    load_alignment_model,
    load_audio,
    postprocess_results,
    preprocess_text,
)

DEFAULT_LANGUAGE = "heb"


## `score` is a log-probability (0 = perfectly confident, more negative = less so), not a 0-1
# ratio. On or_caze, well-aligned words scored between -0.02 and -0.6; a word left stranded by
# trailing non-lyrical audio (instrumental bleed, wordless vocalizing) scored -6.6. -2.0 sits
# comfortably between those two clusters, but this is calibrated on one song and may need
# revisiting once more songs have been run through alignment.
LOW_CONFIDENCE_SCORE = -2.0


@dataclass
class WordTimestamp:
    word: str
    start: float
    end: float
    score: float


class AlignmentError(RuntimeError):
    """Forced alignment failed, or produced an implausible result (word count mismatch,
    non-monotonic timestamps)."""


def low_confidence_words(words: list[WordTimestamp], threshold: float = LOW_CONFIDENCE_SCORE) -> list[WordTimestamp]:
    """Words the acoustic model itself is unsure about - typically the tail of a forced
    alignment run, when audio continues (instrumental bleed, wordless vocalizing) past the last
    real lyric: the model still has to place the remaining tokens somewhere, so their position
    can be far from where they're actually sung. A low score is the signal to distrust it."""
    return [word for word in words if word.score < threshold]


def _validate_alignment(words: list[WordTimestamp], expected_word_count: int) -> None:
    if not words:
        raise AlignmentError("Alignment produced no words.")

    tolerance = max(3, round(expected_word_count * 0.1))
    if abs(len(words) - expected_word_count) > tolerance:
        raise AlignmentError(
            f"Aligned word count ({len(words)}) differs too much from the source text "
            f"({expected_word_count} words) - check that the vocals audio matches the lyrics."
        )

    prev_end = -1.0
    for word in words:
        if word.end < word.start or word.start < prev_end:
            raise AlignmentError(
                f"Non-monotonic or invalid timestamp around word {word.word!r} "
                f"({word.start:.2f}-{word.end:.2f}s)."
            )
        prev_end = word.end


def align_lyrics(
    vocals_path: Path,
    lyrics_text: str,
    *,
    language: str = DEFAULT_LANGUAGE,
    force_cpu: bool = False,
    batch_size: int = 4,
) -> list[WordTimestamp]:
    vocals_path = Path(vocals_path)
    if not vocals_path.is_file():
        raise FileNotFoundError(f"Vocals audio not found: {vocals_path}")

    text = " ".join(lyrics_text.split())
    if not text:
        raise AlignmentError("Lyrics text is empty, nothing to align.")

    if force_cpu:
        device = "cpu"
    elif torch.cuda.is_available():
        device = "cuda"
    elif torch.backends.mps.is_available():
        device = "mps"
    else:
        device = "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32

    model, tokenizer = load_alignment_model(device, dtype=dtype)
    audio_waveform = load_audio(str(vocals_path), model.dtype, model.device)
    emissions, stride = generate_emissions(model, audio_waveform, batch_size=batch_size)

    tokens_starred, text_starred = preprocess_text(text, romanize=True, language=language)
    segments, scores, blank_token = get_alignments(emissions, tokens_starred, tokenizer)
    spans = get_spans(tokens_starred, segments, blank_token)
    raw_words = postprocess_results(text_starred, spans, stride, scores)

    words = [
        WordTimestamp(word=w["text"], start=w["start"], end=w["end"], score=w["score"]) for w in raw_words
    ]
    _validate_alignment(words, expected_word_count=len(text.split()))

    return words
