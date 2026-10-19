import threading

import pytest
import requests

import karaoke_he.web.jobs as jobs_module
from karaoke_he.web.jobs import JobConflictError, create_job, user_facing_error
from karaoke_he.web.pipeline import PipelineError
from karaoke_he.youtube import YouTubeError


def test_user_facing_error_hides_raw_lrclib_http_errors():
    message = user_facing_error(requests.exceptions.HTTPError("503 Server Error: ... https://lrclib.net/api/get?x"), "lyrics")
    assert "lrclib.net/api" not in message
    assert "LRCLIB" in message


def test_user_facing_error_explains_youtube_403():
    assert "Try again" in user_facing_error(YouTubeError("HTTP Error 403: Forbidden"), "downloading")


def test_user_facing_error_keeps_pipeline_messages_and_hides_unexpected_ones():
    assert user_facing_error(PipelineError("No lyrics found"), "lyrics") == "No lyrics found"
    message = user_facing_error(KeyError("internal"), "rendering_subtitles")
    assert "rendering subtitles" in message
    assert "internal" not in message


def test_second_job_for_the_same_song_is_rejected_while_the_first_runs(monkeypatch):
    release = threading.Event()

    def _slow_pipeline(*args, **kwargs):
        release.wait(5)
        raise PipelineError("stopped")

    monkeypatch.setattr(jobs_module, "run_pipeline", _slow_pipeline)
    monkeypatch.setattr(jobs_module, "_jobs", {})

    create_job("same_song", None, "A", "B", youtube_url="https://youtu.be/x")
    with pytest.raises(JobConflictError):
        create_job("same_song", None, "A", "B", youtube_url="https://youtu.be/x")
    create_job("other_song", None, "C", "D", youtube_url="https://youtu.be/y")
    release.set()


def _wait_until(predicate, timeout=5.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_cancel_stops_the_job_at_its_next_progress_update(monkeypatch):
    def _long_pipeline(*args, on_progress, **kwargs):
        for i in range(1000):
            on_progress("rendering_subtitles", f"{i % 100}%")
            threading.Event().wait(0.01)
        raise AssertionError("pipeline should have been cancelled")

    monkeypatch.setattr(jobs_module, "run_pipeline", _long_pipeline)
    monkeypatch.setattr(jobs_module, "_jobs", {})

    job = create_job("song", None, "A", "B", youtube_url="https://youtu.be/x")
    assert _wait_until(lambda: job.status == "rendering_subtitles")
    assert jobs_module.cancel_job(job.id) is True
    assert _wait_until(lambda: job.status == "cancelled")
    assert job.error is None
    assert jobs_module.cancel_job(job.id) is False  # already finished
    assert jobs_module.cancel_job("unknown") is None

    # A cancelled job no longer blocks a new one for the same song.
    monkeypatch.setattr(jobs_module, "run_pipeline", lambda *a, **k: (_ for _ in ()).throw(PipelineError("x")))
    create_job("song", None, "A", "B", youtube_url="https://youtu.be/x")


def test_cancel_wins_over_whatever_error_it_surfaced_as(monkeypatch):
    def _pipeline(*args, on_progress, **kwargs):
        try:
            while True:
                on_progress("downloading", "5%")
                threading.Event().wait(0.01)
        except jobs_module.JobCancelled as exc:
            raise YouTubeError("download aborted") from exc  # like yt-dlp rewrapping it

    monkeypatch.setattr(jobs_module, "run_pipeline", _pipeline)
    monkeypatch.setattr(jobs_module, "_jobs", {})

    job = create_job("song2", None, "A", "B", youtube_url="https://youtu.be/x")
    assert _wait_until(lambda: job.status == "downloading")
    jobs_module.cancel_job(job.id)
    assert _wait_until(lambda: job.status == "cancelled")
