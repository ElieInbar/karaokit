import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf
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


def find_parenthetical_word_indices(lyrics_text: str) -> set[int]:
    """Indices, into the same flat word list `align_lyrics` produces (i.e. `lyrics_text.split()`
    in order), of tokens that were wrapped in parentheses in the original lyrics text - a common
    convention for backing-vocal ad-libs/harmonies. Used only to make low-confidence-region
    warnings more specific (see `web/pipeline.py`'s low-confidence warning formatting), not to
    change alignment itself: forced alignment assumes one sequential vocal stream, so content
    that's genuinely sung *simultaneously* with the main line (as opposed to sequentially, in a
    gap - parentheses don't distinguish the two) has no way to be represented and can end up
    mistimed. Knowing *which* low-confidence words were parenthetical in the source at least lets
    the warning say "likely overlapping backing vocals" instead of the generic, and in this case
    actively misleading, "content missing from the lyrics" (the text wasn't missing - it's a
    timing/overlap issue, not a missing-content one).
    """
    tokens = lyrics_text.split()
    indices: set[int] = set()
    depth = 0
    for i, token in enumerate(tokens):
        if depth > 0:
            indices.add(i)
        depth += token.count("(") - token.count(")")
        depth = max(depth, 0)
        if "(" in token or ")" in token:
            indices.add(i)
    return indices


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


@dataclass
class AudioSplice:
    """Maps a timestamp in a trimmed audio file (with some ranges cut out) back to where that
    same instant was in the original, untrimmed audio."""

    kept_ranges: list[tuple[float, float]] = field(default_factory=list)  # (orig_start, orig_end)

    def to_original_time(self, trimmed_time: float) -> float:
        cursor = 0.0
        for orig_start, orig_end in self.kept_ranges:
            seg_len = orig_end - orig_start
            if trimmed_time <= cursor + seg_len:
                return orig_start + (trimmed_time - cursor)
            cursor += seg_len
        return self.kept_ranges[-1][1] if self.kept_ranges else trimmed_time


def _build_trimmed_audio(
    input_path: Path, exclude_ranges: list[tuple[float, float]], output_path: Path
) -> AudioSplice:
    """Writes `output_path` as `input_path` with each (start, end) range in `exclude_ranges`
    spliced out (silently concatenating what's before and after), and returns the mapping needed
    to translate timestamps computed on that trimmed file back to original-audio time."""
    data, sample_rate = sf.read(str(input_path))
    duration = len(data) / sample_rate

    kept_ranges: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in sorted(exclude_ranges):
        start = max(cursor, min(start, duration))
        end = max(start, min(end, duration))
        if start > cursor:
            kept_ranges.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration:
        kept_ranges.append((cursor, duration))

    chunks = [data[int(round(s * sample_rate)) : int(round(e * sample_rate))] for s, e in kept_ranges]
    trimmed = np.concatenate(chunks) if chunks else np.empty(0, dtype=data.dtype)
    sf.write(str(output_path), trimmed, sample_rate)
    return AudioSplice(kept_ranges=kept_ranges)


def try_correct_alignment(
    vocals_path: Path,
    lyrics_text: str,
    words: list[WordTimestamp],
    *,
    language: str = DEFAULT_LANGUAGE,
    force_cpu: bool = False,
    min_words: int = 5,
    padding_sec: float = 0.5,
) -> list[WordTimestamp]:
    """Attempts to fix substantial low-confidence regions (>= `min_words`) by splicing them out
    of the vocals audio and re-running forced alignment with the *same, unchanged* lyrics text
    against that trimmed audio, then mapping the resulting timestamps back onto the original
    timeline.

    The premise: forced alignment must place every word somewhere across the *entire* audio, so
    an untranscribed stretch (spoken aside, foreign-language passage, unwritten extra chorus)
    makes it smear nearby real words across too much time; removing that stretch lets the
    remaining audio duration match the text again, so those words should snap back to a tighter,
    more accurate placement. This assumes the flagged words themselves *are* genuine lyrics, just
    externally mistimed - their text is never touched or dropped, only the audio is cut.

    Never applied blindly: only accepted if the corrected alignment (a) still has the same word
    count, (b) is still monotonic, and (c) actually has fewer low-confidence words than the
    original - if the "fix" doesn't demonstrably help (or re-alignment on the trimmed audio fails
    outright), the original `words` are returned untouched.
    """
    regions = [r for r in summarize_low_confidence_regions(words) if r.word_count >= min_words]
    if not regions:
        return words

    exclude_ranges = [(r.start - padding_sec, r.end + padding_sec) for r in regions]

    with tempfile.TemporaryDirectory() as tmp:
        trimmed_path = Path(tmp) / "trimmed.wav"
        splice = _build_trimmed_audio(vocals_path, exclude_ranges, trimmed_path)
        try:
            new_words = align_lyrics(trimmed_path, lyrics_text, language=language, force_cpu=force_cpu)
        except AlignmentError:
            return words

    if len(new_words) != len(words):
        return words

    remapped = [
        WordTimestamp(word=w.word, start=splice.to_original_time(w.start), end=splice.to_original_time(w.end), score=w.score)
        for w in new_words
    ]

    prev_end = -1.0
    for word in remapped:
        if word.end < word.start or word.start < prev_end:
            return words
        prev_end = word.end

    old_weak_count = len(low_confidence_words(words))
    new_weak_count = len(low_confidence_words(remapped))
    if new_weak_count >= old_weak_count:
        return words

    return remapped


_model_cache: dict[tuple[str, torch.dtype], tuple] = {}
_model_cache_lock = threading.Lock()


def _cached_alignment_model(device: str, dtype: torch.dtype) -> tuple:
    """The MMS alignment model, loaded once per process: loading it took ~3s of every ~16s
    alignment, and the web server runs many jobs in one long-lived process."""
    with _model_cache_lock:
        key = (device, dtype)
        if key not in _model_cache:
            _model_cache[key] = load_alignment_model(device, dtype=dtype)
        return _model_cache[key]


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
    model, tokenizer = _cached_alignment_model(device, dtype)

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
