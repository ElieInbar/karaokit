import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import requests

LRCLIB_BASE_URL = "https://lrclib.net/api"
SYNCED_LINE_RE = re.compile(r"^\[\d{2}:\d{2}\.\d{2,3}\]\s?")


class LyricsSource(Enum):
    LRCLIB = "lrclib"
    MANUAL = "manual"


@dataclass
class LyricsResult:
    text: str
    source: LyricsSource
    synced: bool
    raw_response: dict | None


class LyricsNotFoundError(RuntimeError):
    """No lyrics found on LRCLIB and no manual fallback file available."""


def strip_synced_timestamps(synced_lyrics: str) -> str:
    lines = [SYNCED_LINE_RE.sub("", line) for line in synced_lyrics.splitlines()]
    return "\n".join(line for line in lines if line.strip())


def _lyrics_result_from_lrclib_payload(payload: dict) -> LyricsResult | None:
    plain = payload.get("plainLyrics")
    if plain:
        return LyricsResult(text=plain.strip(), source=LyricsSource.LRCLIB, synced=False, raw_response=payload)

    synced = payload.get("syncedLyrics")
    if synced:
        return LyricsResult(
            text=strip_synced_timestamps(synced), source=LyricsSource.LRCLIB, synced=True, raw_response=payload
        )

    return None


def fetch_from_lrclib(
    artist: str,
    title: str,
    *,
    album: str | None = None,
    duration_sec: float | None = None,
    timeout: float = 10.0,
) -> LyricsResult | None:
    params = {"artist_name": artist, "track_name": title}
    if album:
        params["album_name"] = album
    if duration_sec is not None:
        params["duration"] = round(duration_sec)

    response = requests.get(f"{LRCLIB_BASE_URL}/get", params=params, timeout=timeout)
    if response.status_code == 200:
        result = _lyrics_result_from_lrclib_payload(response.json())
        if result is not None:
            return result
    elif response.status_code != 404:
        response.raise_for_status()

    # The combined artist+track search can come up empty when LRCLIB stores the artist name
    # transliterated (e.g. "Idan Raichel" for "עידן רייכל") even though the track exists under
    # that title; retry with the track title alone before giving up.
    candidates: list[dict] = []
    for search_params in ({"artist_name": artist, "track_name": title}, {"track_name": title}):
        search_response = requests.get(f"{LRCLIB_BASE_URL}/search", params=search_params, timeout=timeout)
        search_response.raise_for_status()
        candidates = [c for c in search_response.json() if not c.get("instrumental")]
        if candidates:
            break

    if duration_sec is not None:
        candidates.sort(key=lambda c: abs((c.get("duration") or 0) - duration_sec))

    for candidate in candidates:
        result = _lyrics_result_from_lrclib_payload(candidate)
        if result is not None:
            return result

    return None


def load_manual_fallback(song_slug: str, songs_dir: Path) -> LyricsResult | None:
    lyrics_path = Path(songs_dir) / song_slug / "lyrics.txt"
    if not lyrics_path.is_file():
        return None

    lines = [
        line for line in lyrics_path.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("#")
    ]
    text = "\n".join(lines).strip()
    if not text:
        return None

    return LyricsResult(text=text, source=LyricsSource.MANUAL, synced=False, raw_response=None)


def resolve_lyrics(
    song_slug: str,
    artist: str,
    title: str,
    *,
    songs_dir: Path,
    album: str | None = None,
    duration_sec: float | None = None,
) -> LyricsResult:
    result = fetch_from_lrclib(artist, title, album=album, duration_sec=duration_sec)
    if result is not None:
        return result

    result = load_manual_fallback(song_slug, songs_dir)
    if result is not None:
        return result

    raise LyricsNotFoundError(
        f"No lyrics found on LRCLIB for artist={artist!r} title={title!r}, and no manual fallback "
        f"at songs/{song_slug}/lyrics.txt. Fill in that file with the lyrics and rerun."
    )
