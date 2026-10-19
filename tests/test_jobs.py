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
