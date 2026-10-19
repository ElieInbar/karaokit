import re
import tempfile
from pathlib import Path

import requests
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from karaoke_he.language_detect import detect_language
from karaoke_he.languages import DEFAULT_LANGUAGE, LANGUAGES
from karaoke_he.lyrics import fetch_from_lrclib, search_suggestions
from karaoke_he.web.jobs import create_job, get_job
from karaoke_he.youtube import YouTubeError, extract_lyrics_from_description, fetch_youtube_metadata, is_youtube_url

app = FastAPI(title="karaoke_he")

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _slugify(text: str) -> str:
    """Filesystem-safe directory name for `work/<slug>/`. `\\w` is Unicode-aware, so Hebrew (and
    any other script) survives - a plain ASCII-only slugify would collapse every Hebrew
    artist/title (this project's main case) down to the same empty/fallback slug."""
    slug = re.sub(r"[^\w]+", "_", text.strip()).strip("_").lower()
    return slug or "song"


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


LRCLIB_UNAVAILABLE_MESSAGE = "LRCLIB seems to be temporarily unavailable — try again in a moment."


@app.get("/api/suggestions")
def suggestions_endpoint(q: str = "") -> list[dict]:
    """Backs the artist/title autocomplete dropdown. Proxied through the backend (rather than
    the frontend calling LRCLIB directly) so the API key/rate-limit story stays server-side and
    the response can be trimmed to just what the dropdown needs. A transient LRCLIB outage here
    just means no suggestions - not worth surfacing as an error for an autocomplete dropdown."""
    try:
        suggestions = search_suggestions(q)
    except requests.exceptions.RequestException:
        return []
    return [
        {"artist": s.artist, "title": s.title, "album": s.album, "duration_sec": s.duration_sec}
        for s in suggestions
    ]


@app.get("/api/lyrics-check")
def lyrics_check_endpoint(
    artist: str, title: str, album: str | None = None, youtube_url: str | None = None
) -> dict:
    """Looks up lyrics for the given artist/title *without* running a job, so the frontend can
    confirm lyrics were actually found (and let the user review them) before the "Generate
    karaoke video" button is enabled - catching a "no lyrics found" failure up front instead of
    after a multi-minute separation run. Doesn't touch the manual-fallback file (that only makes
    sense once a song_slug/job exists); a not-found result here is exactly the signal that the
    user needs to paste lyrics into the manual field instead.

    When `youtube_url` is given (the YouTube-import flow), the video's own description is checked
    first for an embedded lyrics section - many lyric-video uploads already include the full
    lyrics there - before falling back to LRCLIB. A description match comes back as
    `source: "youtube_description"`; the frontend feeds that text straight into the manual-lyrics
    textarea, since LRCLIB won't necessarily have this song at all and `resolve_lyrics` needs it
    in `songs/<slug>/lyrics.txt` (via the existing manual-fallback path) for the real job to
    succeed - this endpoint is only a preview, it never writes that file itself.

    A transient LRCLIB outage (a 5xx or network failure, as opposed to a genuine "no lyrics for
    this song") is reported as `found: false` with an `error` message distinguishing "try again"
    from "paste the lyrics manually" - letting this propagate as an unhandled 500 previously made
    a temporary LRCLIB blip look like the whole app had crashed.
    """
    if youtube_url and is_youtube_url(youtube_url):
        try:
            metadata = fetch_youtube_metadata(youtube_url)
        except YouTubeError:
            metadata = None
        if metadata is not None:
            extracted = extract_lyrics_from_description(metadata.description)
            if extracted is not None:
                return {
                    "found": True,
                    "source": "youtube_description",
                    "synced": False,
                    "text": extracted,
                    "detected_language": detect_language(extracted),
                }

    try:
        result = fetch_from_lrclib(artist, title, album=album or None)
    except requests.exceptions.RequestException:
        return {"found": False, "error": LRCLIB_UNAVAILABLE_MESSAGE}
    if result is None:
        return {"found": False}
    return {
        "found": True,
        "source": result.source.value,
        "synced": result.synced,
        "text": result.text,
        "detected_language": detect_language(result.text),
    }


@app.get("/api/youtube-metadata")
def youtube_metadata_endpoint(url: str) -> dict:
    """Fetches video title/uploader/duration (no download) so the frontend can prefill the
    artist/title fields for the user to confirm - reusing the existing lyrics-check/review gate
    unchanged, rather than trying to guess the "right" artist/title automatically."""
    if not is_youtube_url(url):
        raise HTTPException(400, "Not a YouTube URL")
    try:
        metadata = fetch_youtube_metadata(url)
    except YouTubeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "video_id": metadata.video_id,
        "title": metadata.title,
        "suggested_artist": metadata.suggested_artist,
        "suggested_title": metadata.suggested_title,
        "uploader": metadata.uploader,
        "duration_sec": metadata.duration_sec,
    }


@app.post("/jobs")
async def create_job_endpoint(
    file: UploadFile | None = File(None),
    youtube_url: str | None = Form(None),
    artist: str = Form(...),
    title: str = Form(...),
    album: str | None = Form(None),
    lyrics: str | None = Form(None),
    language: str = Form(DEFAULT_LANGUAGE),
) -> dict:
    if language not in LANGUAGES:
        raise HTTPException(400, f"Unknown language code {language!r}; expected one of {sorted(LANGUAGES)}")
    if bool(file) == bool(youtube_url):
        raise HTTPException(400, "Provide exactly one of an uploaded file or a youtube_url")
    if youtube_url and not is_youtube_url(youtube_url):
        raise HTTPException(400, "Not a YouTube URL")

    song_slug = f"{_slugify(artist)}_{_slugify(title)}"

    input_path: Path | None = None
    if file is not None:
        tmp_dir = Path(tempfile.mkdtemp(prefix="karaoke_he_upload_"))
        input_path = tmp_dir / (file.filename or "input.mp3")
        input_path.write_bytes(await file.read())

    job = create_job(
        song_slug,
        input_path,
        artist,
        title,
        youtube_url=youtube_url or None,
        album=album or None,
        manual_lyrics=lyrics or None,
        language=language,
    )
    return {"job_id": job.id}


@app.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return {
        "id": job.id,
        "status": job.status,
        "detail": job.detail,
        "error": job.error,
        "done": job.status == "done",
        "warnings": job.warnings,
    }


@app.get("/jobs/{job_id}/video")
def job_video(job_id: str) -> FileResponse:
    job = get_job(job_id)
    if job is None or job.output_path is None:
        raise HTTPException(404, "Video not ready")
    return FileResponse(job.output_path, filename=job.output_path.name)
