from collections.abc import Callable
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
    can be far from where they're actually sung. A low score is the signal to distrust it. The
    same pattern can also show up in the *middle* of a song - e.g. a spoken or foreign-language
    passage that isn't in the fetched lyrics text at all - not just at the tail end."""
    return [word for word in words if word.score < threshold]


@dataclass
class LowConfidenceRegion:
    start: float
    end: float
    word_count: int


def transcribe_diagnostic_region(
    model, vocals_path: Path, start: float, end: float, padding: float = 1.0
) -> tuple[str, str]:
    """Auto-transcribes one time range of the vocals track and returns (detected_language,
    text) - a diagnostic aid for a `LowConfidenceRegion`, not part of the main alignment path.

    Unlike `ctc-forced-aligner`, which is only ever given the song's one declared language,
    Whisper detects the spoken language itself, which is exactly what's useful when a
    low-confidence region turns out to be a spoken aside or a passage in another language that
    the forced aligner had no way to represent (see CLAUDE.md's `ממעמקים` example) - forced
    alignment can't tell you *what* is actually being said in a region it's unsure about, only
    *that* it's unsure. Pass in an already-loaded model (`load_diagnostic_model`) so multiple
    regions in the same song don't each pay the model-load cost again.
    """
    clip_start = max(0.0, start - padding)
    result = model.transcribe(str(vocals_path), clip_timestamps=[clip_start, end + padding], verbose=False)
    return result.language, result.text.strip()


def load_diagnostic_model(model_name: str = "base"):
    """Loads the `stable_whisper` model used by `transcribe_diagnostic_region`. Lazy import:
    this diagnostic path is the only thing in the package that needs `stable_whisper`, so it
    shouldn't be a hard dependency of ordinary forced alignment."""
    import stable_whisper

    return stable_whisper.load_model(model_name)


def summarize_low_confidence_regions(
    words: list[WordTimestamp], threshold: float = LOW_CONFIDENCE_SCORE, gap_sec: float = 5.0
) -> list[LowConfidenceRegion]:
    """Groups `low_confidence_words` into contiguous time regions (merging words less than
    `gap_sec` apart) instead of a flat list - a song can have dozens of individually
    low-confidence words clustered around just one or two real trouble spots (e.g. one spoken
    passage in another language), and a per-word listing obscures that shape."""
    weak = low_confidence_words(words, threshold)
    if not weak:
        return []

    regions = [LowConfidenceRegion(weak[0].start, weak[0].end, 1)]
    for word in weak[1:]:
        current = regions[-1]
        if word.start - current.end <= gap_sec:
            current.end = word.end
            current.word_count += 1
        else:
            regions.append(LowConfidenceRegion(word.start, word.end, 1))
    return regions


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
    on_progress: Callable[[str], None] = lambda message: None,
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

    on_progress("loading alignment model")
    model, tokenizer = load_alignment_model(device, dtype=dtype)

    on_progress("loading vocals audio")
    audio_waveform = load_audio(str(vocals_path), model.dtype, model.device)

    on_progress("running acoustic model")
    emissions, stride = generate_emissions(model, audio_waveform, batch_size=batch_size)

    on_progress("preparing text")
    tokens_starred, text_starred = preprocess_text(text, romanize=True, language=language)

    on_progress("computing alignment")
    segments, scores, blank_token = get_alignments(emissions, tokens_starred, tokenizer)
    spans = get_spans(tokens_starred, segments, blank_token)
    raw_words = postprocess_results(text_starred, spans, stride, scores)

    words = [
        WordTimestamp(word=w["text"], start=w["start"], end=w["end"], score=w["score"]) for w in raw_words
    ]
    _validate_alignment(words, expected_word_count=len(text.split()))

    return words
