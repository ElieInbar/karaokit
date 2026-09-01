# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A karaoke-video pipeline for Hebrew songs, rebuilt as a proper git repo + Python package after an
earlier single-script prototype (now under `legacy/`). Given a song, it separates vocals from
instrumental, resolves synced/plain lyrics, force-aligns them to the vocal track, emits an ASS
karaoke subtitle file (right-to-left aware), and muxes everything into a final `.mkv`. The
project is built and validated one module at a time — each module has its own manual test and
acceptance criterion before the next one is started. The active implementation plan lives at
`/Users/elieinbar/.claude/plans/refactored-hugging-owl.md`.

## Layout

- `src/karaoke_he/` — the package. One module per pipeline stage: `separation.py`, `lyrics.py`,
  `text_utils.py` (Hebrew nikud/orthography normalization, shared by lyrics/alignment/subtitles),
  `alignment.py`, `subtitles.py`, `mux.py`. `src/karaoke_he/cli/` holds one Typer CLI per stage
  for manual, isolated testing (`python -m karaoke_he.cli.<stage>`).
- `songs/<slug>/` — small, git-tracked, human-authored inputs per song: `meta.json`
  (artist/title/album/duration) and `lyrics.txt` (manual lyrics fallback used when the song isn't
  on LRCLIB). `songs/or_caze/` is the current working example.
- `work/<slug>/` — gitignored, fully regenerable working files per song: separated stems,
  resolved lyrics, alignment word timestamps, the generated `.ass`, a burn-in preview clip, and
  the final `.mkv`. Never commit anything here — rerun the relevant CLI stage instead.
- `legacy/` — `align.py`, the original flat prototype script, kept for reference only (its old
  sample input/output files were dropped, not migrated).
- `.venv/` — the single consolidated virtualenv (Python 3.12). Install everything with
  `pip install -e .` from `pyproject.toml`.
- `tests/` — pytest unit tests for pure logic (e.g. `text_utils`), no audio/model dependencies.

## Environment

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
```

`ffmpeg` is available via Homebrew (`/opt/homebrew/bin/ffmpeg`). `mkvmerge` (MKVToolNix) is
required for the mux stage and must be installed separately: `brew install mkvtoolnix`.

Known dependency quirks on this setup:
- `audio-separator` needs `audioread` as an extra explicit dependency (not pulled in
  transitively) — already listed in `pyproject.toml`.
- Use `pymkv2` (import name still `pymkv`), not the original `pymkv` package — `pymkv` imports
  `pkg_resources`, which recent `setuptools` no longer vendors, so it fails to import.
- The PyPI package literally named `ctc-forced-aligner` is a different, Latin-alphabet-only
  project (deskpai) that squats the name of the actual MMS-based multilingual aligner with real
  Hebrew support; install the real one from
  `git+https://github.com/MahmoudAshraf97/ctc-forced-aligner.git` (already reflected in
  `pyproject.toml`).
- Neither Homebrew's `ffmpeg` nor `ffmpeg-full` (both installed here) are built with `libass`, so
  ffmpeg's `subtitles`/`ass` filters are unavailable for burning in or visually checking `.ass`
  files on this machine, even though the standalone `libass` package is present. `mpv`
  (`brew install mpv`) is used instead to render/screenshot `.ass` content for the subtitles
  stage, since it links against `libass` directly.

## Pipeline stages and technical choices

These choices came out of a state-of-the-art audit of the original plan (Demucs / Genius /
WhisperX) and correct known weaknesses in it:

1. **Separation** (`separation.py`) — `audio-separator` with a BS-Roformer/Mel-Band-Roformer
   checkpoint, not Demucs v4 (lower SDR).
2. **Lyrics** (`lyrics.py`) — LRCLIB first (free, no API key, often pre-synced), falling back to
   the manual `songs/<slug>/lyrics.txt` file rather than scraping Genius. Hebrew lyrics must be
   normalized (`text_utils.normalize_hebrew`: strip nikud, NFC) before any text matching, since
   ASR output never carries nikud.
3. **Alignment** (`alignment.py`) — `ctc-forced-aligner` (Meta MMS, native Hebrew support via the
   `heb` language code), not WhisperX's wav2vec2 path (only an unofficial, non-vetted community
   Hebrew checkpoint exists for it). `stable_whisper`/`stable-ts` is kept as a fallback/comparison
   method.
4. **Subtitles** (`subtitles.py`) — libass has a known, unresolved bug
   ([libass#406](https://github.com/libass/libass/issues/406)) where `\k` karaoke fill always
   progresses left-to-right regardless of BiDi display direction; wrapping text in RLE/PDF
   Unicode marks (the old `legacy/align.py` approach) does not fix this, and no reliable
   community workaround for reversing word order into a correctly-reading `\k` line was found. A
   `\c`-based per-word recoloring workaround (stacked `Dialogue` events per word boundary) was
   tried and *visually* validated by burn-in, but tagging every word with its own override split
   each line into many small bidi runs and made word order visibly unstable between events on
   closer inspection (in motion, not just single frames) - not worth the complexity. The current
   `subtitles.py` emits one plain-text `Dialogue` event per lyric line (no highlight, no `\k`,
   no `\c`), correctly RTL/LTR-placed by ordinary (non-buggy) bidi, timed with a research-backed
   lead-in (see below).

   **Known limitation / planned improvement**: this gives up the word-by-word karaoke highlight
   entirely. Research into how professional/commercial tools do this (VEED, Filmora, an After
   Effects karaoke-animator script, etc.) shows they don't rely on ASS/libass's `\k` rendering at
   all - they either have their own text-compositing engine or pre-render the animation into real
   video frames. The equivalent for this all-Python pipeline would be rendering each frame's
   highlight overlay ourselves (Pillow + `arabic_reshaper` + `python-bidi` for correct Hebrew
   shaping/ordering, composited over the video via ffmpeg) instead of depending on a subtitle
   renderer's own `\k` logic. Flagged as future work, not yet implemented.
5. **Mux** (`mux.py`) — `pymkv2`/`mkvmerge`: background video + instrumental audio (default
   track) + isolated vocal audio (secondary, non-default) + the `.ass` subtitles (default track),
   all tagged `language_ietf="he"` (BCP 47) rather than a hand-computed ISO 639-2 code - modern
   mkvmerge writes the new `LanguageIETF` element from this and derives the legacy `heb`
   `Language` element from it automatically. Validated on `or_caze` in VLC: instrumental default,
   vocals present but not selected, subtitles synced, Hebrew shown as the track language.

## Why `legacy/align.py` isn't used as a reference

The old prototype is kept for historical context only — its RTL approach (RLE/PDF marks) doesn't
fix the `\k` bug (see above), and its line-timing logic (a flat `ANTICIPATION_SEC` constant
subtracted from every line's first word, regardless of line length) was judged too simplistic to
carry forward. Do not port its constants or structure into new modules without independently
re-deriving and justifying them:

- Its word-to-line mapping was positional (consumed as many flat Whisper words per line as the
  line had whitespace-separated tokens), which drifts out of sync whenever the transcript's word
  count per line doesn't match the lyrics file. `subtitles.group_words_by_line` does something
  similar in shape (splitting by per-line word count) but only because both sides — the lyrics
  lines and the aligned words — come from splitting the exact same source string
  `alignment.align_lyrics` ran on; it fails loudly (`SubtitleError`) if the totals don't match
  exactly, rather than silently drifting like the old two-independent-sources case did.
- Line display timing (`subtitles._line_display_start`) instead follows general subtitle-timing
  conventions: a ~1s minimum lead-in before the first sung word (standard karaoke practice), or
  more if the line's text is long relative to how fast it's actually sung (using a ~15
  characters/second reading-speed baseline), always clamped to not overlap the previous line.
