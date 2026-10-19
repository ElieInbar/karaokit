import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import requests

LRCLIB_BASE_URL = "https://lrclib.net/api"

# LRCLIB asks every client to identify itself (app name, version, homepage) in User-Agent.
# KARAOKIT_HOMEPAGE should point at wherever this copy of the project is published.
def _user_agent() -> str:
    try:
        from importlib.metadata import version

        app_version = version("karaokit")
    except Exception:  # noqa: BLE001 - running from a source tree that isn't installed
        app_version = "dev"
    homepage = os.environ.get("KARAOKIT_HOMEPAGE", "https://github.com/ElieInbar/karaokit")
    return f"Karaokit/{app_version} (+{homepage})"


LRCLIB_HEADERS = {"User-Agent": _user_agent()}
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


# Someone is waiting on the search box: retry, but briefly.
LRCLIB_INTERACTIVE_RETRY_DELAYS_SEC = (0.4, 1.0, 2.0)
_MAX_RETRY_AFTER_SEC = 5.0


def _lrclib_get(
    path: str, params: dict | None, timeout: float, retry_delays: tuple[float, ...] | None = None
) -> requests.Response:
    """`requests.get` against LRCLIB, retried on 5xx responses and connection errors/timeouts
    (see `LRCLIB_RETRY_DELAYS_SEC`). 4xx responses (including a genuine 404 "not found") are
    returned immediately, since retrying can't change them."""
    delays = LRCLIB_RETRY_DELAYS_SEC if retry_delays is None else retry_delays
    for delay in (*delays, None):
        try:
            response = requests.get(f"{LRCLIB_BASE_URL}{path}", params=params, timeout=timeout, headers=LRCLIB_HEADERS)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            if delay is None:
                raise
        else:
            # 429 "Too Many Requests" is transient too; honor its Retry-After when it's short.
            retryable = response.status_code >= 500 or response.status_code == 429
            if not retryable or delay is None:
                return response
            try:
                delay = max(delay, min(float(response.headers.get("Retry-After", 0)), _MAX_RETRY_AFTER_SEC))
            except ValueError:
                pass
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
class SongMatch:
    """One song found by `search_songs`, with its lyrics already attached: picking a result in
    the web UI is all it takes to confirm the lyrics, no second lookup."""

    lrclib_id: int
    artist: str
    title: str
    album: str | None
    duration_sec: float | None
    synced: bool
    lyrics: str | None  # plain text (synced timestamps stripped); None if LRCLIB has none


# Video-upload junk often left in LRCLIB track names, e.g. "Song [ZbIABUsnxFg]" or "(Official Video)".
_TITLE_NOISE_RE = re.compile(
    r"\s*(\[[A-Za-z0-9_-]{11}\]|\((official|lyric|lyrics|audio|video|music video|official video|official audio|hd|4k)[^)]*\))",
    re.IGNORECASE,
)


def _clean_title(title: str) -> str:
    return _TITLE_NOISE_RE.sub("", title).strip() or title.strip()


def _fold(text: str) -> str:
    """Case- and accent-insensitive form: "Ömer Balık" and "OMER BALIK" are the same artist."""
    decomposed = unicodedata.normalize("NFKD", text.casefold().replace("ı", "i"))
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).split())


def _strip_artist_prefix(artist: str, title: str) -> str:
    """Uploads often name tracks "ARTIST - Title"; the artist is already shown separately."""
    head, sep, rest = title.partition(" - ")
    return rest.strip() if sep and rest.strip() and _fold(head) == _fold(artist) else title


def _song_key(artist: str, title: str) -> tuple[str, str]:
    return (_fold(artist), _fold(_clean_title(_strip_artist_prefix(artist, title))))


# Recent searches, so retyping or going back to a query doesn't hit LRCLIB again (it
# rate-limits with 429s). Only successful lookups are cached.
_SEARCH_CACHE_TTL_SEC = 600
_SEARCH_CACHE_MAX = 200
_search_cache: dict[str, tuple[float, list]] = {}
_search_cache_lock = threading.Lock()


def search_songs(query: str, *, limit: int = 8, timeout: float = 6.0) -> list[SongMatch]:
    """Songs matching a free-text query (title and/or artist, any language) via LRCLIB's `q`
    search, which matches across artist and track name - LRCLIB has no artist-only search.

    LRCLIB often lists the same song many times (re-uploads, video-id suffixes, different case),
    so results are grouped by (artist, cleaned title), keeping the best version of each: synced
    lyrics over plain over none. Instrumentals are dropped. Raises `requests` exceptions if
    LRCLIB stays unavailable after a couple of quick retries."""
    query = " ".join(query.split())
    if len(query) < 2:
        return []

    cache_key = f"{_fold(query)}|{limit}"
    with _search_cache_lock:
        cached = _search_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < _SEARCH_CACHE_TTL_SEC:
            return list(cached[1])

    response = _lrclib_get("/search", {"q": query}, timeout, retry_delays=LRCLIB_INTERACTIVE_RETRY_DELAYS_SEC)
    response.raise_for_status()

    def rank(candidate: dict) -> int:
        return 2 if candidate.get("syncedLyrics") else 1 if candidate.get("plainLyrics") else 0

    best: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    for candidate in response.json():
        artist, title = candidate.get("artistName"), candidate.get("trackName")
        if not artist or not title or candidate.get("instrumental") or candidate.get("id") is None:
            continue
        key = _song_key(artist, title)
        if key not in best:
            order.append(key)
            best[key] = candidate
        elif rank(candidate) > rank(best[key]):
            best[key] = candidate

    matches = []
    for key in order[:limit]:
        c = best[key]
        result = _lyrics_result_from_lrclib_payload(c)
        matches.append(
            SongMatch(
                lrclib_id=int(c["id"]),
                artist=c["artistName"].strip(),
                title=_clean_title(_strip_artist_prefix(c["artistName"], c["trackName"])),
                album=c.get("albumName") or None,
                duration_sec=c.get("duration"),
                synced=bool(c.get("syncedLyrics")),
                lyrics=result.text if result else None,
            )
        )
    with _search_cache_lock:
        if len(_search_cache) >= _SEARCH_CACHE_MAX:
            _search_cache.pop(next(iter(_search_cache)))
        _search_cache[cache_key] = (time.monotonic(), list(matches))
    return matches


def fetch_lrclib_by_id(lrclib_id: int, *, timeout: float = 10.0) -> LyricsResult | None:
    """The exact LRCLIB record a user picked in the search box - so the job uses precisely the
    lyrics that were shown, rather than re-searching by artist/title and possibly landing on a
    different version."""
    response = _lrclib_get(f"/get/{int(lrclib_id)}", None, timeout)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return _lyrics_result_from_lrclib_payload(response.json())


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
