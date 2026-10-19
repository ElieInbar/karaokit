import shutil
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import requests

from karaoke_he.alignment import AlignmentError
from karaoke_he.languages import DEFAULT_LANGUAGE
from karaoke_he.web.pipeline import PipelineError, run_pipeline
from karaoke_he.youtube import YouTubeError, friendly_youtube_error

FINISHED_STATUSES = {"done", "error", "cancelled"}


class JobCancelled(Exception):
    """Raised from a job's own progress callback once cancellation is requested - progress
    callbacks fire every few seconds in every long stage (download, separation, rendering), so
    that's where a running pipeline can actually stop. Deliberately not a RuntimeError, so no
    stage's error handling swallows it by accident."""


class JobConflictError(RuntimeError):
    """Another job for the same song is still running - both would write into the same
    `work/<song_slug>/` directory at once."""


def user_facing_error(exc: Exception, stage: str) -> str:
    """Turns a pipeline exception into a message for the web UI. The raw exception (often a URL,
    an HTTP status line or an ffmpeg exit code) is still printed to the server log in full."""
    if isinstance(exc, requests.exceptions.RequestException):
        return "The lyrics service (LRCLIB) isn't responding right now. Try again in a few minutes."
    if isinstance(exc, YouTubeError):
        return friendly_youtube_error(exc)
    if isinstance(exc, (PipelineError, AlignmentError)):
        return str(exc)
    return f"Something went wrong during the {stage.replace('_', ' ')} step. Details are in the server log."


@dataclass
class Job:
    id: str
    song_slug: str = ""
    status: str = "queued"
    detail: str | None = None
    error: str | None = None
    output_path: Path | None = None
    preview_path: Path | None = None
    warnings: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    cancel_requested: threading.Event = field(default_factory=threading.Event)

    @property
    def elapsed_sec(self) -> float:
        return time.monotonic() - self.started_at


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
    job = Job(id=uuid.uuid4().hex[:12], song_slug=song_slug)
    with _lock:
        if any(j.song_slug == song_slug and j.status not in FINISHED_STATUSES for j in _jobs.values()):
            raise JobConflictError(song_slug)
        _jobs[job.id] = job

    def _on_progress(stage: str, detail: str | None = None) -> None:
        if job.cancel_requested.is_set():
            raise JobCancelled()
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
                job.preview_path = result.preview_path
                job.warnings = result.warnings
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller via job.error, not raised
            if job.cancel_requested.is_set():
                # Whatever the cancellation surfaced as (yt-dlp, for one, rewraps exceptions
                # raised from its progress hook as its own download errors), it was a cancel.
                with _lock:
                    job.status = "cancelled"
                return
            traceback.print_exc()
            with _lock:
                job.error = user_facing_error(exc, job.status)
                job.status = "error"
        finally:
            # An upload lives in its own temp dir (see app.py); the pipeline copies it into
            # work/<slug>/ first thing, so the temp copy is never needed again.
            if input_audio is not None and Path(input_audio).parent.name.startswith("karaoke_he_upload_"):
                shutil.rmtree(Path(input_audio).parent, ignore_errors=True)

    threading.Thread(target=_run, daemon=True).start()
    return job


def get_job(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(job_id)


def cancel_job(job_id: str) -> bool | None:
    """Requests cancellation; the job stops at its next progress update. Returns None for an
    unknown job, False if it had already finished."""
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        if job.status in FINISHED_STATUSES:
            return False
        job.cancel_requested.set()
        return True
