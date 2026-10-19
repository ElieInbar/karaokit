"""The "Your videos" library: every finished karaoke video under `work/<slug>/mux/`.

There's no database - `work/` itself is the source of truth, so videos made from the CLI, or
before the web app recorded anything, still show up. Each web job also writes
`work/<slug>/job.json` (see `write_job_record`) with the human-readable artist/title it was
generated from; older songs fall back to `songs/<slug>/meta.json`, then to the slug itself.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

JOB_RECORD_NAME = "job.json"


@dataclass
class LibraryEntry:
    slug: str
    artist: str | None
    title: str
    language: str | None
    created_at: str  # ISO 8601, UTC
    size_bytes: int
    video_path: Path


def write_job_record(work_dir: Path, *, artist: str, title: str, language: str, source: str | None) -> None:
    record = {
        "artist": artist,
        "title": title,
        "language": language,
        "source": source,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (work_dir / JOB_RECORD_NAME).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _final_video(work_dir: Path) -> Path | None:
    candidates = sorted((work_dir / "mux").glob("*_karaoke.mkv"))
    return candidates[0] if candidates else None


def list_library(work_root: Path, songs_root: Path) -> list[LibraryEntry]:
    """Finished videos, newest first."""
    entries = []
    if not work_root.is_dir():
        return entries
    for work_dir in work_root.iterdir():
        if not work_dir.is_dir():
            continue
        video = _final_video(work_dir)
        if video is None:
            continue
        record = _read_json(work_dir / JOB_RECORD_NAME) or _read_json(songs_root / work_dir.name / "meta.json")
        stat = video.stat()
        created_at = record.get("created_at") or datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(
            timespec="seconds"
        )
        entries.append(
            LibraryEntry(
                slug=work_dir.name,
                artist=record.get("artist"),
                title=record.get("title") or work_dir.name.replace("_", " "),
                language=record.get("language"),
                created_at=created_at,
                size_bytes=stat.st_size,
                video_path=video,
            )
        )
    entries.sort(key=lambda e: e.created_at, reverse=True)
    return entries


def resolve_song_dir(work_root: Path, slug: str) -> Path | None:
    """`work_root / slug`, only if that's really a direct child of `work_root` - the slug comes
    from a URL, so this is what stops `..`/absolute-path tricks from reaching other files."""
    if not slug or "/" in slug or "\\" in slug or slug in {".", ".."}:
        return None
    candidate = (work_root / slug).resolve()
    if candidate.parent != work_root.resolve() or not candidate.is_dir():
        return None
    return candidate


def find_entry_video(work_root: Path, slug: str) -> Path | None:
    song_dir = resolve_song_dir(work_root, slug)
    return _final_video(song_dir) if song_dir else None
