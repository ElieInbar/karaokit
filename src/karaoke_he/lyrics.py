import re
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import requests

LRCLIB_BASE_URL = "https://lrclib.net/api"
SYNCED_LINE_RE = re.compile(r"^\[\d{2}:\d{2}\.\d{2,3}\]\s?")

# LRCLIB intermittently answers 503 "ServerOverloaded" for a few seconds at a time (seen live:
# the same request failing, then succeeding a minute later). A job only reaches the lyrics stage
# after minutes of download/separation work, so a single transient blip there shouldn't throw
# all of that away - retry server errors and network failures with a short backoff first.
LRCLIB_RETRY_DELAYS_SEC = (2.0, 5.0, 10.0)


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


def _lrclib_get(path: str, params: dict, timeout: float) -> requests.Response:
    """`requests.get` against LRCLIB, retried on 5xx responses and connection errors/timeouts
    (see `LRCLIB_RETRY_DELAYS_SEC`). 4xx responses (including a genuine 404 "not found") are
    returned immediately, since retrying can't change them."""
    for delay in (*LRCLIB_RETRY_DELAYS_SEC, None):
        try:
            response = requests.get(f"{LRCLIB_BASE_URL}{path}", params=params, timeout=timeout)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            if delay is None:
                raise
        else:
            if response.status_code < 500 or delay is None:
                return response
        time.sleep(delay)
    raise AssertionError("unreachable")


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

    response = _lrclib_get("/get", params, timeout)
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
        search_response = _lrclib_get("/search", search_params, timeout)
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


@dataclass
class SongSuggestion:
    artist: str
    title: str
    album: str | None
    duration_sec: float | None


def search_suggestions(query: str, *, limit: int = 8, timeout: float = 5.0) -> list[SongSuggestion]:
    """Autocomplete suggestions for an artist/title search box, backed by LRCLIB's generic `q`
    search (matches across both artist and track name, unlike `fetch_from_lrclib`'s exact
    artist_name/track_name params). Deliberately returns only artist/title/album/duration - never
    plainLyrics/syncedLyrics, which the raw LRCLIB payload also carries - since this is just for
    picking *which* song, not for the lyrics themselves.
    """
    query = query.strip()
    if not query:
        return []

    response = requests.get(f"{LRCLIB_BASE_URL}/search", params={"q": query}, timeout=timeout)
    response.raise_for_status()

    seen: set[tuple[str, str]] = set()
    suggestions = []
    for candidate in response.json():
        artist = candidate.get("artistName")
        title = candidate.get("trackName")
        if not artist or not title:
            continue
        key = (artist, title)
        if key in seen:
            continue
        seen.add(key)
        suggestions.append(
            SongSuggestion(
                artist=artist,
                title=title,
                album=candidate.get("albumName"),
                duration_sec=candidate.get("duration"),
            )
        )
        if len(suggestions) >= limit:
            break

    return suggestions


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
