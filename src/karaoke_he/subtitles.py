from dataclasses import dataclass

from karaoke_he.alignment import WordTimestamp
from karaoke_he.text_utils import strip_display_punctuation

# Standard karaoke convention: show a line roughly a second before it needs to be sung, so
# viewers notice it before they need to start reading it.
MIN_LEAD_IN_SEC = 1.0

# Common subtitle-readability guideline (professional captioning guides typically target
# somewhere in the 15-20 characters/second range); used to extend the lead-in further when a
# line is short-lived relative to its length, so viewers have had a chance to read it once
# before it's gone.
READING_CHARS_PER_SEC = 15.0

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,Arial Hebrew,65,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,2,2,10,10,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


class SubtitleError(RuntimeError):
    """Subtitle generation failed - typically a word-count mismatch between the lyrics lines
    and the aligned words, which would make positional line-grouping unsafe."""


@dataclass
class SubtitleLine:
    words: list[WordTimestamp]

    @property
    def text(self) -> str:
        return " ".join(strip_display_punctuation(w.word) for w in self.words)


def group_words_by_line(lyrics_lines: list[str], words: list[WordTimestamp]) -> list[SubtitleLine]:
    """Group the flat, jalon-3 word list back into per-line groups.

    Both sides come from splitting the exact same source text the aligner ran on (see
    `alignment.align_lyrics`, which flattens `lyrics_text.split()`), so counting words per line
    with the same plain `.split()` is safe *as long as the totals match exactly*. Unlike
    legacy/align.py, which positionally mapped an independently-transcribed ASR output onto the
    lyrics file and could silently drift when the two didn't tokenize to the same count, this
    fails loudly instead of guessing when the counts disagree.
    """
    counts = [len(line.split()) for line in lyrics_lines]
    if sum(counts) != len(words):
        raise SubtitleError(
            f"Word count mismatch between lyrics lines ({sum(counts)} words) and aligned words "
            f"({len(words)}) - cannot safely group words into subtitle lines."
        )

    result = []
    idx = 0
    for count in counts:
        if count > 0:
            result.append(SubtitleLine(words=words[idx : idx + count]))
        idx += count
    return result


def format_ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds % 1) * 100))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _line_display_start(line: SubtitleLine, previous_line_end: float) -> float:
    first_word = line.words[0]
    last_word = line.words[-1]
    sung_duration = last_word.end - first_word.start
    reading_time = len(line.text) / READING_CHARS_PER_SEC
    lead_in = max(MIN_LEAD_IN_SEC, reading_time - sung_duration)
    start = max(0.0, first_word.start - lead_in)
    return max(start, previous_line_end)


def generate_ass(lyrics_lines: list[str], words: list[WordTimestamp]) -> str:
    """Plain synced lyric lines - no word-by-word karaoke highlight.

    libass has a known, unresolved bug (libass#406) where `\\k` karaoke fill always progresses
    left-to-right on screen regardless of a line's bidi (RTL) display direction. A `\\c`-based
    per-word recoloring workaround was tried instead of `\\k`, but tagging each word with its own
    override split the line into many small bidi runs and made word order visibly unstable
    between events. Rather than keep chasing a robust highlight, each line is emitted as a single
    plain-text event - just the line visible while it's being sung, correctly RTL-placed by
    ordinary (non-buggy) bidi, with no per-word animation.
    """
    subtitle_lines = group_words_by_line(lyrics_lines, words)

    events = []
    previous_end = 0.0
    for line in subtitle_lines:
        display_start = _line_display_start(line, previous_end)
        display_end = line.words[-1].end
        events.append(
            f"Dialogue: 0,{format_ass_time(display_start)},{format_ass_time(display_end)},"
            f"Karaoke,,0,0,0,,{line.text}"
        )
        previous_end = display_end

    return ASS_HEADER + "\n".join(events) + "\n"
