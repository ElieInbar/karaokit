from pathlib import Path

from pymkv import MKVFile, MKVTrack

# BCP 47 tag, not a raw ISO 639-2 code: modern mkvmerge writes the new LanguageIETF element from
# this and derives the legacy ISO 639-2 "Language" element from it automatically, rather than us
# computing/guessing the legacy code ourselves.
DEFAULT_LANGUAGE = "he"


class MuxError(RuntimeError):
    """mkvmerge failed to produce the output file."""


def mux_video(
    background_video: Path,
    instrumental_path: Path,
    vocals_path: Path,
    output_path: Path,
    *,
    subtitles_path: Path | None = None,
    language: str = DEFAULT_LANGUAGE,
) -> Path:
    """`subtitles_path` is optional: the karaoke highlight is now hard-burned into
    `background_video` by `subtitle_render.py` rather than carried as a soft `.ass` track (see
    CLAUDE.md), so most callers won't pass one. It's kept as an option for a plain-text fallback
    subtitle track if one is ever wanted alongside the burned-in video."""
    required = [
        ("background video", background_video),
        ("instrumental audio", instrumental_path),
        ("vocals audio", vocals_path),
    ]
    if subtitles_path is not None:
        required.append(("subtitles", subtitles_path))
    for label, path in required:
        if not Path(path).is_file():
            raise FileNotFoundError(f"{label} not found: {path}")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    mkv = MKVFile()
    mkv.add_track(MKVTrack(str(background_video), track_id=0))
    mkv.add_track(
        MKVTrack(
            str(instrumental_path),
            track_name="Instrumental",
            language_ietf=language,
            default_track=True,
        )
    )
    mkv.add_track(
        MKVTrack(
            str(vocals_path),
            track_name="Vocals (isolated)",
            language_ietf=language,
            default_track=False,
        )
    )
    if subtitles_path is not None:
        mkv.add_track(
            MKVTrack(
                str(subtitles_path),
                track_name="Karaoke",
                language_ietf=language,
                default_track=True,
            )
        )

    exit_code = mkv.mux(str(output_path), silent=True)
    if exit_code not in (0, 1):  # mkvmerge: 0 = ok, 1 = ok with warnings, 2 = error
        raise MuxError(f"mkvmerge failed muxing {output_path} (exit code {exit_code})")

    return output_path
