from pathlib import Path

import karaoke_he.web.pipeline as pipeline_module
from karaoke_he.alignment import LowConfidenceRegion, WordTimestamp
from karaoke_he.web.pipeline import _format_low_confidence_warnings


def test_format_low_confidence_warnings_empty_when_no_regions(monkeypatch):
    monkeypatch.setattr(pipeline_module, "summarize_low_confidence_regions", lambda words: [])
    assert _format_low_confidence_warnings([], Path("/tmp/vocals.wav"), "") == []


def test_format_low_confidence_warnings_skips_diagnosis_below_threshold(monkeypatch):
    monkeypatch.setattr(
        pipeline_module,
        "summarize_low_confidence_regions",
        lambda words: [LowConfidenceRegion(10.0, 11.0, 2)],
    )

    def _fail_load_model():
        raise AssertionError("should not load the diagnostic model for a small region")

    monkeypatch.setattr(pipeline_module, "load_diagnostic_model", _fail_load_model)

    warnings = _format_low_confidence_warnings([], Path("/tmp/vocals.wav"), "")
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

    warnings = _format_low_confidence_warnings([], Path("/tmp/vocals.wav"), "")
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

    warnings = _format_low_confidence_warnings([], Path("/tmp/vocals.wav"), "")
    assert len(warnings) == 1
    assert "langue" not in warnings[0]


def test_format_low_confidence_warnings_uses_parenthetical_message_for_bracketed_words(monkeypatch):
    words = [
        WordTimestamp("(ouh,", 10.0, 10.5, score=-6.0),
        WordTimestamp("nanana)", 10.5, 11.0, score=-6.0),
    ]

    def _fail_diagnosis(*args, **kwargs):
        raise AssertionError("should not run language diagnosis on a parenthetical region")

    monkeypatch.setattr(pipeline_module, "load_diagnostic_model", _fail_diagnosis)
    monkeypatch.setattr(pipeline_module, "transcribe_diagnostic_region", _fail_diagnosis)

    warnings = _format_low_confidence_warnings(words, Path("/tmp/vocals.wav"), "a (ouh, nanana) b")

    assert len(warnings) == 1
    assert "parenthesized lyrics" in warnings[0]
    assert "backing vocals" in warnings[0]
    assert "0:10–0:11" in warnings[0]


def test_format_low_confidence_warnings_uses_generic_message_outside_parentheses(monkeypatch):
    words = [
        WordTimestamp("bonjour", 10.0, 10.5, score=-6.0),
        WordTimestamp("monde", 10.5, 11.0, score=-6.0),
    ]
    monkeypatch.setattr(pipeline_module, "load_diagnostic_model", lambda: "fake-model")
    monkeypatch.setattr(pipeline_module, "transcribe_diagnostic_region", lambda model, path, start, end: ("fr", ""))

    warnings = _format_low_confidence_warnings(words, Path("/tmp/vocals.wav"), "bonjour monde")

    assert len(warnings) == 1
    assert "parenthesized" not in warnings[0]
    assert "spoken or unsung content" in warnings[0]


def test_format_time_range_collapses_same_second_and_uses_minutes():
    assert pipeline_module._format_time_range(36.2, 36.4) == "0:36"
    assert pipeline_module._format_time_range(210.7, 224.6) == "3:31–3:45"


def test_pasted_lyrics_win_over_lrclib(monkeypatch, tmp_path):
    def _fail_resolve(*args, **kwargs):
        raise AssertionError("LRCLIB must not be consulted when the user pasted lyrics")

    monkeypatch.setattr(pipeline_module, "resolve_lyrics", _fail_resolve)

    result = pipeline_module._resolve_job_lyrics(
        "a_b", "A", "B", "corrected line one\nline two", album=None, duration_sec=60.0, songs_dir=tmp_path
    )

    assert result.text == "corrected line one\nline two"
    assert (tmp_path / "a_b" / "lyrics.txt").read_text(encoding="utf-8") == "corrected line one\nline two"


def test_without_pasted_lyrics_falls_back_to_resolve_lyrics(monkeypatch, tmp_path):
    sentinel = object()
    monkeypatch.setattr(pipeline_module, "resolve_lyrics", lambda *args, **kwargs: sentinel)

    assert pipeline_module._resolve_job_lyrics("a_b", "A", "B", "   ", album=None, duration_sec=60.0, songs_dir=tmp_path) is sentinel


def test_stems_are_stale_unless_stamped_with_the_same_input(tmp_path):
    assert pipeline_module._stems_are_stale(tmp_path, "abc")  # no stamp (legacy cache)

    (tmp_path / pipeline_module.STEMS_SOURCE_STAMP).write_text("abc")
    assert not pipeline_module._stems_are_stale(tmp_path, "abc")
    assert pipeline_module._stems_are_stale(tmp_path, "def")  # different audio, same song name


def test_saved_alignment_emissions_are_reused_instead_of_recomputed(monkeypatch, tmp_path):
    import numpy as np

    from karaoke_he.alignment import AcousticEmissions

    saved = AcousticEmissions(emissions=np.random.rand(10, 32).astype(np.float32), stride=20.0)
    path = tmp_path / "alignment_emissions.npz"
    saved.save(path)

    def _fail(*args, **kwargs):
        raise AssertionError("emissions should have been loaded from the saved file")

    monkeypatch.setattr(pipeline_module, "compute_emissions", _fail)
    loaded = pipeline_module._load_or_compute_emissions(path, tmp_path / "vocals.wav", on_progress=lambda m: None)
    assert loaded.stride == 20.0
    assert np.array_equal(loaded.emissions, saved.emissions)


def test_missing_alignment_emissions_are_computed_and_saved(monkeypatch, tmp_path):
    import numpy as np

    from karaoke_he.alignment import AcousticEmissions

    computed = AcousticEmissions(emissions=np.zeros((4, 32), dtype=np.float32), stride=20.0)
    monkeypatch.setattr(pipeline_module, "compute_emissions", lambda *a, **k: computed)
    path = tmp_path / "alignment_emissions.npz"
    assert pipeline_module._load_or_compute_emissions(path, tmp_path / "v.wav", on_progress=lambda m: None) is computed
    assert path.is_file()
