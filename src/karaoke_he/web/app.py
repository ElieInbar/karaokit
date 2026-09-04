import re
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from karaoke_he.web.jobs import create_job, get_job

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


@app.post("/jobs")
async def create_job_endpoint(
    file: UploadFile = File(...),
    artist: str = Form(...),
    title: str = Form(...),
    album: str | None = Form(None),
    lyrics: str | None = Form(None),
) -> dict:
    song_slug = f"{_slugify(artist)}_{_slugify(title)}"
    tmp_dir = Path(tempfile.mkdtemp(prefix="karaoke_he_upload_"))
    input_path = tmp_dir / (file.filename or "input.mp3")
    input_path.write_bytes(await file.read())

    job = create_job(song_slug, input_path, artist, title, album=album or None, manual_lyrics=lyrics or None)
    return {"job_id": job.id}


@app.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return {
        "id": job.id,
        "status": job.status,
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
