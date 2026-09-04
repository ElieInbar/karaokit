# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A karaoke-video pipeline for Hebrew songs, rebuilt as a proper git repo + Python package after an
earlier single-script prototype (now under `legacy/`). Given a song, it separates vocals from
instrumental, resolves synced/plain lyrics, force-aligns them to the vocal track, renders an
animated word-by-word karaoke highlight (right-to-left aware) hard-burned onto the background
video, and muxes everything into a final `.mkv`. The project is built and validated one module at
a time — each module has its own manual test and acceptance criterion before the next one is
started. The original per-stage implementation plan lives at
`/Users/elieinbar/.claude/plans/refactored-hugging-owl.md` (jalons 0-5, all done).

On top of the CLI pipeline there is now a local web app (`src/karaoke_he/web/`, see Layout below)
that orchestrates the whole thing end to end from a browser: upload a song + artist/title, poll
job status, download the finished `.mkv`. A follow-up plan for that app (multi-language support,
detailed per-stage progress, a visual redesign, translating the frontend to English) is written up
at `/Users/elieinbar/.claude/plans/temporal-kindling-valley.md` but **not yet implemented** - don't
assume any of it is done without checking that plan/the actual code first.

## Layout

- `src/karaoke_he/` — the package. One module per pipeline stage: `separation.py`, `lyrics.py`,
  `text_utils.py` (Hebrew nikud/orthography normalization, shared by lyrics/alignment/subtitles),
  `alignment.py`, `subtitles.py` (plain-text `.ass` generation, no longer the primary rendering
  path - see Subtitles below), `subtitle_render.py` (the animated karaoke overlay, hard-burned
  into the video), `mux.py`. `src/karaoke_he/cli/` holds one Typer CLI per stage for manual,
  isolated testing (`python -m karaoke_he.cli.<stage>`).
- `src/karaoke_he/web/` — the local web app that orchestrates all of the above into one job:
  `pipeline.py`'s `run_pipeline()` calls each stage function in sequence (unchanged - no logic is
  duplicated here) and returns a `PipelineResult` (output path + any alignment low-confidence
  warnings, see Alignment below); `jobs.py` runs it in a background thread per upload with an
  in-memory `Job` (no persistence - job history is lost on server restart, acceptable for the
  current personal-use-only scope); `app.py` is the FastAPI app (`POST /jobs`, `GET /jobs/{id}`,
  `GET /jobs/{id}/video`); `static/index.html` is a single dependency-free HTML/CSS/JS page
  (currently French - see the pending plan above for translating it). Run locally with
  `uvicorn karaoke_he.web.app:app --host 127.0.0.1 --port 8000`, then open
  `http://127.0.0.1:8000`. Uses `separate_audio_remote` (Modal), never the local `separate_audio`.
- `songs/<slug>/` — small, git-tracked, human-authored inputs per song: `meta.json`
  (artist/title/album/duration) and `lyrics.txt` (manual lyrics fallback used when the song isn't
  on LRCLIB). `songs/or_caze/` is the current working example.
- `work/<slug>/` — gitignored, fully regenerable working files per song: separated stems,
  resolved lyrics, alignment word timestamps, the rendered subtitle overlay and the
  background-with-subtitles video, and the final `.mkv`. Never commit anything here — rerun the
  relevant CLI stage instead.
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
   checkpoint, not Demucs v4 (lower SDR). `separate_audio()` runs it locally (~20-30 min on this
   Mac's MPS - BS-Roformer has no efficient flash-attention kernel there, and MPS was also
   observed to slow down progressively and die silently on long runs, hence `force_cpu` as a
   fallback). For anything latency-sensitive (the target app needs closer to real-time),
   `separate_audio_remote()` calls the same model via the Modal-deployed GPU function in
   `deploy/modal_app.py` instead (~2m20s on an A10G) - same `SeparationResult` contract, `modal`
   imported lazily so it's not a hard dependency of the local-only path. The Modal app must be
   deployed once with `modal deploy deploy/modal_app.py` (redeploy after changing that file);
   `cli/separate.py --remote` then calls the already-deployed function directly via
   `modal.Function.from_name(...)`, without needing `modal run` at all.
2. **Lyrics** (`lyrics.py`) — LRCLIB first (free, no API key, often pre-synced), falling back to
   the manual `songs/<slug>/lyrics.txt` file rather than scraping Genius. Hebrew lyrics must be
   normalized (`text_utils.normalize_hebrew`: strip nikud, NFC) before any text matching, since
   ASR output never carries nikud.
3. **Alignment** (`alignment.py`) — `ctc-forced-aligner` (Meta MMS, native Hebrew support via the
   `heb` language code), not WhisperX's wav2vec2 path (only an unofficial, non-vetted community
   Hebrew checkpoint exists for it).

   Forced alignment must map every lyric word somewhere across the *entire* audio, so when the
   audio actually contains something the fetched lyrics text doesn't account for - a spoken
   passage, a foreign-language aside, an extra unwritten chorus repeat - nearby real words get
   shoved to implausible timestamps instead of the pipeline failing loudly (found on a real song,
   `ממעמקים` / Idan Raichel, which has a spoken passage partway through that isn't part of the
   transcribed lyrics). `low_confidence_words()` already flags individual words whose acoustic
   score is far below the rest (see `LOW_CONFIDENCE_SCORE`'s own calibration note);
   `summarize_low_confidence_regions()` groups those into contiguous time ranges instead of a flat
   per-word list, since dozens of low-confidence words are often really just one or two real
   trouble spots. `web/pipeline.py`'s `run_pipeline()` surfaces these as `PipelineResult.warnings`
   (shown to the user in the web UI) - this **detects and surfaces** the problem, it does not fix
   the underlying misalignment (that would need audio segmentation to exclude the offending
   stretch before alignment, not attempted); the CLI (`cli/align.py`) prints the same regions to
   stderr.

   `stable_whisper`/`stable-ts` (originally kept as a plain fallback/comparison aligner, per the
   old plan) is now used for exactly one thing: `load_diagnostic_model()` +
   `transcribe_diagnostic_region()` auto-transcribe a flagged region to detect its spoken
   language, since "somewhere around here is off" is far less actionable than "this doesn't sound
   like the song's declared language". The transcribed text itself is deliberately never surfaced
   (only the detected language) - it's possibly-copyrighted content, and doesn't need to be to be
   useful. Tested on `ממעמקים`: Whisper's language ID is unreliable on short/isolated clips
   (wildly different, implausible languages guessed for different single-word blips in the same
   song) and language-ID is inherently weaker on sung audio than its usual speech use case, so
   `web/pipeline.py`'s `MIN_WORDS_FOR_DIAGNOSIS` only runs this for regions with several words
   (skipping likely-noise 1-2 word blips entirely), and the warning phrases the result as a hint,
   not a fact.

   `try_correct_alignment()` is the actual-fix attempt (not just detection): splice the
   substantial low-confidence region(s) out of the vocals audio, re-run `align_lyrics` with the
   same unchanged text against the trimmed audio, remap the resulting timestamps back onto the
   original timeline, and only keep the result if it's provably better (same word count, still
   monotonic, *and* strictly fewer low-confidence words than before) - otherwise silently fall
   back to the original alignment, so this can never make things worse for the caller. Tested on
   `ממעמקים`: it did **not** help in practice (52 low-confidence words after vs. 43 before,
   monotonic and word-count-matched, so the safety net correctly rejected it and fell back) -
   splicing out multiple ranges introduces new discontinuities at each cut point, and the
   resulting artifacts apparently confuse the acoustic model more than the untranscribed content
   did. The safety net did its job (no regression shipped), but the splice-and-realign technique
   itself needs more work before it's a real fix - e.g. crossfading the cut points instead of a
   hard splice, or correcting one region at a time instead of all of them in a single trimmed
   file - not attempted yet.
4. **Subtitles** (`subtitles.py` + `subtitle_render.py`) — libass has a known, unresolved bug
   ([libass#406](https://github.com/libass/libass/issues/406)) where `\k` karaoke fill always
   progresses left-to-right regardless of BiDi display direction; wrapping text in RLE/PDF
   Unicode marks (the old `legacy/align.py` approach) does not fix this, and no reliable
   community workaround for reversing word order into a correctly-reading `\k` line was found. A
   `\c`-based per-word recoloring workaround (stacked `Dialogue` events per word boundary) was
   tried and *visually* validated by burn-in, but tagging every word with its own override split
   each line into many small bidi runs and made word order visibly unstable between events on
   closer inspection (in motion, not just single frames) - not worth the complexity.
   `subtitles.py` still generates a plain-text `.ass` (one `Dialogue` event per line, no
   highlight) as a kept-for-reference/fallback path, but it is **not the primary rendering path
   any more**.

   The primary path is `subtitle_render.py`: it renders the karaoke highlight itself, frame by
   frame, as an RGBA image sequence (Pillow, `+stroke` outline; Hebrew needs no
   `arabic_reshaper`-style contextual shaping, but Pillow here has no `libraqm`, so BiDi visual
   ordering is computed manually per line-chunk via `python-bidi`'s `get_display`), piped
   straight into `ffmpeg` as an alpha-channel ProRes 4444 `.mov` (`render_overlay_video`). That
   overlay is then composited onto the background video with ffmpeg's `overlay` filter
   (`burn_overlay`), hard-burning the highlight into the video track - sidestepping `\k`/libass
   entirely rather than fighting it. Highlighting is a short (~150ms) colour crossfade per word
   rather than an instant swap, since the instant-swap `.ass` version looked cheap/jarring in
   motion even though individual frames were correct. Validated on `or_caze`'s full ~4-minute
   overlay (rendered in ~2m20s) composited and played back in VLC.
5. **Mux** (`mux.py`) — `pymkv2`/`mkvmerge`: the (already subtitle-burned) video + instrumental
   audio (default track) + isolated vocal audio (secondary, non-default), tagged
   `language_ietf="he"` (BCP 47) rather than a hand-computed ISO 639-2 code - modern mkvmerge
   writes the new `LanguageIETF` element from this and derives the legacy `heb` `Language`
   element from it automatically. `subtitles_path` is accepted but optional, for a plain-text
   `.ass` track alongside the burned-in video if one is ever wanted; it's normally omitted since
   the highlight is already in the picture. Validated on `or_caze` in VLC: instrumental default,
   vocals present but not selected, Hebrew shown as the track language.

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
