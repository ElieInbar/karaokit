import json

from karaoke_he.web.library import find_entry_video, list_library, resolve_song_dir, write_job_record


def _make_video(work_root, slug, name=None):
    mux = work_root / slug / "mux"
    mux.mkdir(parents=True)
    (mux / f"{name or slug}_karaoke.mkv").write_bytes(b"x" * 10)


def test_list_library_uses_job_record_then_song_meta_then_slug(tmp_path):
    work, songs = tmp_path / "work", tmp_path / "songs"
    _make_video(work, "with_record")
    write_job_record(work / "with_record", artist="Omer Adam", title="מביט מהצד", language="heb", source=None)
    _make_video(work, "with_meta")
    (songs / "with_meta").mkdir(parents=True)
    (songs / "with_meta" / "meta.json").write_text(json.dumps({"artist": "A", "title": "Meta title"}))
    _make_video(work, "bare_slug")
    (work / "unfinished" / "stems").mkdir(parents=True)  # no final video -> not listed

    entries = {e.slug: e for e in list_library(work, songs)}

    assert set(entries) == {"with_record", "with_meta", "bare_slug"}
    assert (entries["with_record"].artist, entries["with_record"].title) == ("Omer Adam", "מביט מהצד")
    assert entries["with_meta"].title == "Meta title"
    assert entries["bare_slug"].title == "bare slug"
    assert entries["bare_slug"].size_bytes == 10


def test_resolve_song_dir_rejects_paths_outside_work_root(tmp_path):
    work = tmp_path / "work"
    _make_video(work, "song")
    (tmp_path / "secret").mkdir()

    assert resolve_song_dir(work, "song") == (work / "song").resolve()
    for bad in ["..", "../secret", "/etc", "song/../../secret", "", "missing"]:
        assert resolve_song_dir(work, bad) is None
    assert find_entry_video(work, "../secret") is None
