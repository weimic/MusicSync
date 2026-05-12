"""Spotify Web API client (Client Credentials, read-only, public playlists).

This intentionally avoids ``spotipy`` to keep the dependency surface small and
the failure modes explicit. The only endpoints used are:

* ``POST /api/token`` (accounts.spotify.com) - get an app token.
* ``GET  /v1/playlists/{id}/tracks`` - paginated track list.
"""

from __future__ import annotations

import base64
import logging
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests

from .models import Track

log = logging.getLogger(__name__)

_TOKEN_URL = "https://accounts.spotify.com/api/token"
_API_BASE = "https://api.spotify.com/v1"

_PLAYLIST_ID_RE = re.compile(r"^[A-Za-z0-9]{22}$")
_REQUEST_TIMEOUT = 15
_MAX_RETRIES = 5


class SpotifyError(RuntimeError):
    """Raised for any unrecoverable Spotify API failure."""


@dataclass(frozen=True, slots=True)
class FetchStats:
    """Counts of items the fetcher chose to skip, surfaced to the caller."""

    skipped_null: int = 0
    skipped_local: int = 0


def extract_playlist_id(url_or_id: str) -> str:
    """Accept either a bare 22-char Spotify ID or a full open.spotify.com URL.

    Raises ``SpotifyError`` if the input can't be resolved to a playlist id.
    """
    s = url_or_id.strip()
    if _PLAYLIST_ID_RE.match(s):
        return s

    parsed = urlparse(s)
    parts = [p for p in parsed.path.split("/") if p]
    # Expected shape: ["playlist", "<id>"] possibly preceded by a locale segment.
    for i, part in enumerate(parts):
        if part == "playlist" and i + 1 < len(parts):
            candidate = parts[i + 1]
            if _PLAYLIST_ID_RE.match(candidate):
                return candidate
    raise SpotifyError(f"Could not extract a Spotify playlist id from: {url_or_id!r}")


class SpotifyClient:
    """Thin Client-Credentials Spotify client. Not thread-safe."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        session: requests.Session | None = None,
    ) -> None:
        if not client_id or not client_secret:
            raise SpotifyError(
                "Spotify credentials missing. Set SPOTIFY_CLIENT_ID and "
                "SPOTIFY_CLIENT_SECRET (e.g. in a .env file)."
            )
        self._client_id = client_id
        self._client_secret = client_secret
        self._session = session or requests.Session()
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 30:
            return self._token

        creds = f"{self._client_id}:{self._client_secret}".encode("utf-8")
        headers = {
            "Authorization": "Basic " + base64.b64encode(creds).decode("ascii"),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        resp = self._session.post(
            _TOKEN_URL,
            data={"grant_type": "client_credentials"},
            headers=headers,
            timeout=_REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            raise SpotifyError(
                f"Token request failed ({resp.status_code}): {resp.text[:200]}"
            )
        payload = resp.json()
        self._token = payload["access_token"]
        self._token_expires_at = time.time() + int(payload.get("expires_in", 3600))
        return self._token

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            token = self._get_token()
            resp = self._session.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=_REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 401:
                # Token may have been invalidated mid-flight; force refresh once.
                self._token = None
                continue
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", "1"))
                log.warning("Spotify rate limited; sleeping %.1fs", retry_after)
                time.sleep(retry_after)
                continue
            if 500 <= resp.status_code < 600:
                backoff = 2**attempt
                log.warning(
                    "Spotify %d; retrying in %ds (attempt %d/%d)",
                    resp.status_code,
                    backoff,
                    attempt + 1,
                    _MAX_RETRIES,
                )
                time.sleep(backoff)
                continue
            raise SpotifyError(
                f"GET {url} failed ({resp.status_code}): {resp.text[:200]}"
            )
        raise SpotifyError(
            f"GET {url} gave up after {_MAX_RETRIES} attempts; last error: {last_exc}"
        )

    def fetch_playlist_tracks(self, url_or_id: str) -> tuple[list[Track], FetchStats]:
        """Return every playable track in a public Spotify playlist.

        ``track == null`` entries (region-locked / removed) and local files are
        skipped and counted in ``FetchStats``.
        """
        playlist_id = extract_playlist_id(url_or_id)
        url = f"{_API_BASE}/playlists/{playlist_id}/tracks"
        params: dict[str, Any] = {
            "fields": (
                "items(is_local,track("
                "name,artists(name),album(name),duration_ms,id,is_local"
                ")),next"
            ),
            "limit": 100,
        }

        tracks: list[Track] = []
        skipped_null = 0
        skipped_local = 0

        while True:
            page = self._get(url, params=params)
            for item in page.get("items", []):
                if item.get("is_local"):
                    skipped_local += 1
                    continue
                t = item.get("track")
                if not t:
                    skipped_null += 1
                    continue
                if t.get("is_local"):
                    skipped_local += 1
                    continue
                tracks.append(_track_from_spotify(t))
            next_url = page.get("next")
            if not next_url:
                break
            # `next` is a fully-qualified URL with its own query string; clear params.
            url = next_url
            params = None  # type: ignore[assignment]

        return tracks, FetchStats(skipped_null=skipped_null, skipped_local=skipped_local)


def _track_from_spotify(t: dict[str, Any]) -> Track:
    artists = tuple(a.get("name", "") for a in (t.get("artists") or []) if a.get("name"))
    album = (t.get("album") or {}).get("name")
    return Track(
        title=t.get("name", ""),
        artists=artists,
        album=album,
        duration_ms=t.get("duration_ms"),
        source="spotify",
        source_id=t.get("id"),
    )
