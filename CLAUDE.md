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
- `legacy/` — the original prototype, kept for reference: `align.py` (the old flat script) and
  `example_song/` (its sample input/output files).
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
   Unicode marks (the old `legacy/align.py` approach) does not fix this. The workaround is to
   manually reverse word/syllable order before writing `\k` tags for RTL lines, with care around
   mixed RTL/LTR runs (Latin words, digits). This must be validated with an actual visual burn-in
   render, not just by inspecting the `.ass` text.
5. **Mux** (`mux.py`) — `pymkv2`/`mkvmerge`: background video + instrumental audio (default
   track) + isolated vocal audio (secondary, non-default) + the `.ass` subtitles, with correct
   ISO 639-2 language tags.

## Notes carried over from `legacy/align.py`

- Its word-to-line mapping was positional (consumed as many flat Whisper words per line as the
  line had whitespace-separated tokens), which drifts out of sync whenever the transcript's word
  count per line doesn't match the lyrics file. The new `alignment.py`/`subtitles.py` split should
  not repeat this — keep word-level timestamps and line boundaries as explicit, separate data
  rather than re-deriving line boundaries by counting.
- `ANTICIPATION_SEC` (how far a line's displayed start is pulled before the first word is sung,
  clamped against the previous line's end) is a UX behavior worth keeping in the new
  `subtitles.py`, just not the RTL/mapping mechanism around it.
