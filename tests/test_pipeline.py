from pathlib import Path

import karaoke_he.web.pipeline as pipeline_module
from karaoke_he.alignment import LowConfidenceRegion
from karaoke_he.web.pipeline import _format_low_confidence_warnings


def test_format_low_confidence_warnings_empty_when_no_regions(monkeypatch):
    monkeypatch.setattr(pipeline_module, "summarize_low_confidence_regions", lambda words: [])
    assert _format_low_confidence_warnings([], Path("/tmp/vocals.wav")) == []


def test_format_low_confidence_warnings_skips_diagnosis_below_threshold(monkeypatch):
    monkeypatch.setattr(
        pipeline_module,
        "summarize_low_confidence_regions",
        lambda words: [LowConfidenceRegion(10.0, 11.0, 2)],
    )

    def _fail_load_model():
        raise AssertionError("should not load the diagnostic model for a small region")

    monkeypatch.setattr(pipeline_module, "load_diagnostic_model", _fail_load_model)

    warnings = _format_low_confidence_warnings([], Path("/tmp/vocals.wav"))
    assert len(warnings) == 1
    assert "langue" not in warnings[0]


def test_format_low_confidence_warnings_runs_diagnosis_at_threshold(monkeypatch):
    monkeypatch.setattr(
        pipeline_module,
        "summarize_low_confidence_regions",
        lambda words: [LowConfidenceRegion(200.0, 210.0, 8)],
    )
    monkeypatch.setattr(pipeline_module, "load_diagnostic_model", lambda: "fake-model")
    monkeypatch.setattr(
        pipeline_module, "transcribe_diagnostic_region", lambda model, path, start, end: ("fr", "some text")
    )

    warnings = _format_low_confidence_warnings([], Path("/tmp/vocals.wav"))
    assert len(warnings) == 1
    assert "fr" in warnings[0]
    assert "some text" not in warnings[0]


def test_format_low_confidence_warnings_omits_language_when_transcription_empty(monkeypatch):
    monkeypatch.setattr(
        pipeline_module,
        "summarize_low_confidence_regions",
        lambda words: [LowConfidenceRegion(200.0, 210.0, 8)],
    )
    monkeypatch.setattr(pipeline_module, "load_diagnostic_model", lambda: "fake-model")
    monkeypatch.setattr(pipeline_module, "transcribe_diagnostic_region", lambda model, path, start, end: ("en", ""))

    warnings = _format_low_confidence_warnings([], Path("/tmp/vocals.wav"))
    assert len(warnings) == 1
    assert "langue" not in warnings[0]
