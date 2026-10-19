import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yt_dlp

YOUTUBE_URL_RE = re.compile(r"^https?://(www\.)?(youtube\.com/watch\?v=|youtu\.be/)")

# A line consisting of just one of these words (in the languages this pipeline supports),
# optionally followed by a colon, marks the start of an embedded lyrics section in a video
# description - a common convention for lyric-video uploads.
_LYRICS_MARKERS = ("lyrics", "paroles", "letra", "letras", "текст", "текст песни", "كلمات", "كلمات الأغنية", "מילים", "מילות השיר")
_LYRICS_HEADER_RE = re.compile(
    r"(?im)^[ \t]*(?:" + "|".join(re.escape(marker) for marker in _LYRICS_MARKERS) + r")[ \t]*:?[ \t]*$"
)
# Lines commonly found right after a description's lyrics block (social links, credits,
# hashtags) - stripped from the end of the extracted text so they don't get treated as lyrics.
_JUNK_LINE_RE = re.compile(
    r"(?i)^\s*(https?://\S+|www\.\S+|[#@]\S+|"
    r"(follow|subscribe|instagram|facebook|tiktok|twitter|spotify|apple music|copyright|"
    r"all rights reserved)\b.*)\s*$"
)
MIN_EXTRACTED_LYRICS_LENGTH = 60


class YouTubeError(RuntimeError):
    """A YouTube lookup or download failed with a user-facing cause (private/age-restricted/
    region-locked video, invalid URL, network failure), as opposed to an unexpected bug."""


def friendly_youtube_error(exc: Exception) -> str:
    """A short message for the web UI. yt-dlp's own errors are long and full of CLI advice
    (`--cookies-from-browser`, FAQ links) that means nothing to someone using the web page."""
    raw = str(exc)
    if "not a bot" in raw or "Sign in to confirm" in raw:
        return (
            "YouTube is temporarily blocking requests from this computer (its anti-bot check, "
            "usually after many downloads in a row). Wait a while and try again, or upload the "
            "audio file instead."
        )
    if "403" in raw:
        return "YouTube refused the download - this happens now and then. Try again in a moment."
    if "Private video" in raw or "unavailable" in raw.lower():
        return "This video is private or unavailable."
    if raw.startswith("Not a YouTube URL"):
        return "That doesn't look like a YouTube link."
    return "Couldn't get this video from YouTube. Check the link and try again."


@dataclass
class YouTubeMetadata:
    video_id: str
    title: str
    suggested_artist: str
    suggested_title: str
    uploader: str
    duration_sec: float | None
    description: str = ""


def is_youtube_url(url: str) -> bool:
    return bool(YOUTUBE_URL_RE.match(url.strip()))


def _split_artist_title(info: dict) -> tuple[str, str]:
    """Best-effort artist/title guess from yt-dlp's extracted info, for the frontend to prefill
    (the user can still edit before submitting). `artist`/`track` are set by yt-dlp for YouTube
    Music videos and are the most reliable signal when present; otherwise fall back to splitting
    the video title on the common "Artist - Title" convention, and failing that, to the uploader
    name as the artist guess with the full title as-is."""
    artist = info.get("artist")
    track = info.get("track")
    if artist and track:
        return artist, track

    title = info.get("title") or ""
    if " - " in title:
        artist, _, track = title.partition(" - ")
        return artist.strip(), track.strip()

    return info.get("uploader") or "", title


def fetch_youtube_metadata(url: str) -> YouTubeMetadata:
    if not is_youtube_url(url):
        raise YouTubeError(f"Not a YouTube URL: {url!r}")

    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise YouTubeError(f"Could not fetch video info for {url!r}: {exc}") from exc

    suggested_artist, suggested_title = _split_artist_title(info)
    return YouTubeMetadata(
        video_id=info.get("id", ""),
        title=info.get("title", ""),
        suggested_artist=suggested_artist,
        suggested_title=suggested_title,
        uploader=info.get("uploader") or "",
        duration_sec=info.get("duration"),
        description=info.get("description") or "",
    )


def extract_lyrics_from_description(description: str) -> str | None:
    """Best-effort extraction of an embedded lyrics section from a YouTube video description -
    many lyric-video uploads already include the full lyrics under a "Lyrics:"/"Paroles:"/etc.
    header. Returns `None` if no such header is found, or if what follows it is too short to
    plausibly be real lyrics (e.g. "Lyrics: see pinned comment") rather than a genuine block of
    text - in both cases the caller is expected to fall back to LRCLIB instead."""
    if not description:
        return None

    match = _LYRICS_HEADER_RE.search(description)
    if match is None:
        return None

    lines = description[match.end() :].splitlines()

    # Stop at the next short heading-like line (e.g. "Credits:", "Follow me:") - these commonly
    # mark the end of the lyrics block and the start of an unrelated section.
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.endswith(":") and 0 < len(stripped) <= 40:
            lines = lines[:i]
            break

    # Trailing social/credit lines sometimes appear without a heading at all - trim those too.
    while lines and (not lines[-1].strip() or _JUNK_LINE_RE.match(lines[-1])):
        lines.pop()

    text = "\n".join(lines).strip()
    return text if len(text) >= MIN_EXTRACTED_LYRICS_LENGTH else None


def download_youtube_audio(
    url: str,
    dest_dir: Path,
    *,
    filename_stem: str = "input",
    on_progress: Callable[[str], None] = lambda message: None,
) -> Path:
    """Downloads the best available audio-only stream for `url` into `dest_dir` and converts it
    to WAV via `ffmpeg` (yt-dlp's `FFmpegExtractAudio` postprocessor - `ffmpeg` is already a
    required system dependency for muxing/rendering, so this needs no new dependency). A forced
    re-encode, not the native container: confirmed live that `soundfile` (the pipeline's first
    duration check, in `separation.py`) cannot open YouTube's native webm/opus container at all
    ("Format not recognised") - `audioread`'s fallback decoding only kicks in elsewhere, not for
    that `sf.info()` call. Returns the resulting `.wav` file's path."""
    if not is_youtube_url(url):
        raise YouTubeError(f"Not a YouTube URL: {url!r}")

    dest_dir.mkdir(parents=True, exist_ok=True)

    def _hook(status: dict) -> None:
        if status["status"] == "downloading":
            pct = status.get("_percent_str", "").strip().rstrip("%")
            if pct:
                try:
                    on_progress(f"{round(float(pct))}%")
                except ValueError:
                    pass

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(dest_dir / f"{filename_stem}.%(ext)s"),
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
        "progress_hooks": [_hook],
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as exc:
        raise YouTubeError(f"Could not download audio for {url!r}: {exc}") from exc

    downloaded_path = dest_dir / f"{filename_stem}.wav"
    if not downloaded_path.is_file():
        raise YouTubeError(f"yt-dlp reported success but produced no file for {url!r}")
    return downloaded_path
