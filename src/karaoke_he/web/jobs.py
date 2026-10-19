import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from karaoke_he.languages import DEFAULT_LANGUAGE
from karaoke_he.web.pipeline import run_pipeline


@dataclass
class Job:
    id: str
    status: str = "queued"
    detail: str | None = None
    error: str | None = None
    output_path: Path | None = None
    warnings: list[str] = field(default_factory=list)


_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def create_job(
    song_slug: str,
    input_audio: Path | None,
    artist: str,
    title: str,
    youtube_url: str | None = None,
    album: str | None = None,
    manual_lyrics: str | None = None,
    language: str = DEFAULT_LANGUAGE,
) -> Job:
    job = Job(id=uuid.uuid4().hex[:12])
    with _lock:
        _jobs[job.id] = job

    def _on_progress(stage: str, detail: str | None = None) -> None:
        with _lock:
            job.status = stage
            job.detail = detail

    def _run() -> None:
        try:
            result = run_pipeline(
                song_slug,
                input_audio,
                artist,
                title,
                youtube_url=youtube_url,
                album=album,
                manual_lyrics=manual_lyrics,
                language=language,
                on_progress=_on_progress,
            )
            with _lock:
                job.status = "done"
                job.output_path = result.output_path
                job.warnings = result.warnings
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller via job.error, not raised
            with _lock:
                job.status = "error"
                job.error = str(exc)

    threading.Thread(target=_run, daemon=True).start()
    return job


def get_job(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(job_id)
