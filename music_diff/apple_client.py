"""Apple Music shared-playlist fetcher.

Two fetch paths, in order of preference:

1. **amp-api** (preferred). ``amp-api.music.apple.com`` is the unofficial
   public endpoint Apple's own web player uses for infinite scroll. We
   borrow the same anonymous bearer token the web player embeds in its JS
   bundle and paginate ``/v1/catalog/<storefront>/playlists/<id>/tracks``.
   This returns the *entire* playlist, not just the first 300 entries that
   the server-rendered HTML inlines.

2. **HTML scrape fallback**. If step 1 fails for any reason (Apple changed
   the bundle layout, blocked the call, rate limited, etc.) we parse the
   ``serialized-server-data`` block of the playlist page itself. That gives
   us the first ~300 tracks - degraded but not broken.

This is not an officially documented API; Apple could change the shape or
restrict it at any time. For personal-use, low-volume tools this has been
stable for years, but the fallback exists exactly so a server-side change
gives us a graceful degradation rather than a crash.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .models import Track
from .normalize import split_artist_string

log = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 20
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
_AMP_API_BASE = "https://amp-api.music.apple.com"
_AMP_API_ORIGIN = "https://music.apple.com"

_BUNDLE_RE = re.compile(r'src="(/assets/index~[^"]+\.js)"')
_TOKEN_RE = re.compile(
    r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"
)
_PLAYLIST_URL_RE = re.compile(
    r"^/(?P<store>[a-z]{2})/playlist/[^/]+/(?P<pid>pl\.[A-Za-z0-9._\-]+)/?$"
)
_TRACK_SECTION_PREFIX = "track-list"


class AppleMusicError(RuntimeError):
    """Raised when the Apple Music playlist can't be fetched or parsed."""


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def parse_playlist_url(url: str) -> tuple[str, str]:
    """Return ``(storefront, playlist_id)`` for a music.apple.com URL.

    Raises ``AppleMusicError`` if the URL doesn't look like an Apple Music
    shared playlist link.
    """
    p = urlparse((url or "").strip())
    if "music.apple.com" not in p.netloc:
        raise AppleMusicError(f"Not an Apple Music URL: {url!r}")
    m = _PLAYLIST_URL_RE.match(p.path.rstrip("/"))
    if not m:
        raise AppleMusicError(
            f"Could not parse storefront/playlist id from {url!r}. "
            "Expected something like https://music.apple.com/us/playlist/<name>/<pl.id>"
        )
    return m.group("store"), m.group("pid")


def fetch_playlist_tracks(
    url: str, *, session: requests.Session | None = None
) -> list[Track]:
    """Fetch every track in a public/shared Apple Music playlist.

    Tries the amp-api path first, falls back to HTML scraping if that fails.
    """
    sess = session or requests.Session()
    storefront, playlist_id = parse_playlist_url(url)

    try:
        token = _fetch_developer_token(sess)
        tracks = _fetch_via_amp_api(sess, storefront, playlist_id, token)
        log.info("Apple Music: fetched %d tracks via amp-api", len(tracks))
        return tracks
    except Exception as exc:
        # Any failure in the amp-api path - including AppleMusicError from
        # token extraction or non-200 responses - falls back to the HTML
        # scrape so the tool degrades to "first ~300" instead of crashing.
        log.warning(
            "amp-api fetch failed (%s); falling back to HTML scrape (first ~300)",
            exc,
        )

    return _fetch_via_html(sess, url)


# ---------------------------------------------------------------------------
# amp-api path
# ---------------------------------------------------------------------------


def _fetch_developer_token(session: requests.Session) -> str:
    """Pull the anonymous web-player bearer token from Apple's JS bundle."""
    root = session.get(
        f"{_AMP_API_ORIGIN}/us/",
        headers=_BROWSER_HEADERS,
        timeout=_REQUEST_TIMEOUT,
    )
    if root.status_code != 200:
        raise AppleMusicError(
            f"Could not load music.apple.com root ({root.status_code})"
        )
    m = _BUNDLE_RE.search(root.text)
    if not m:
        raise AppleMusicError("Could not locate Apple Music JS bundle on the home page")
    bundle = session.get(
        f"{_AMP_API_ORIGIN}{m.group(1)}",
        headers=_BROWSER_HEADERS,
        timeout=_REQUEST_TIMEOUT,
    )
    if bundle.status_code != 200:
        raise AppleMusicError(f"Could not fetch JS bundle ({bundle.status_code})")
    tm = _TOKEN_RE.search(bundle.text)
    if not tm:
        raise AppleMusicError("Could not extract developer token from JS bundle")
    return tm.group(0)


def _fetch_via_amp_api(
    session: requests.Session,
    storefront: str,
    playlist_id: str,
    token: str,
) -> list[Track]:
    headers = {
        **_BROWSER_HEADERS,
        "Authorization": f"Bearer {token}",
        "Origin": _AMP_API_ORIGIN,
        "Referer": f"{_AMP_API_ORIGIN}/",
    }
    path: str | None = (
        f"/v1/catalog/{storefront}/playlists/{playlist_id}/tracks?offset=0&limit=100"
    )
    tracks: list[Track] = []

    while path:
        resp = session.get(
            f"{_AMP_API_BASE}{path}",
            headers=headers,
            timeout=_REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            raise AppleMusicError(
                f"amp-api {path} -> {resp.status_code}: {resp.text[:200]}"
            )
        try:
            body = resp.json()
        except ValueError as exc:
            raise AppleMusicError(f"amp-api returned non-JSON: {exc}") from exc

        for item in body.get("data", []) or []:
            track = _track_from_amp_api(item)
            if track is not None:
                tracks.append(track)

        next_path = body.get("next")
        path = next_path if isinstance(next_path, str) and next_path else None

    if not tracks:
        raise AppleMusicError(
            "amp-api returned 0 tracks. The playlist may be empty, region-locked, "
            "or no longer publicly shared."
        )
    return tracks


def _track_from_amp_api(item: Any) -> Track | None:
    if not isinstance(item, dict):
        return None
    attrs = item.get("attributes") or {}
    title = attrs.get("name") if isinstance(attrs, dict) else None
    if not isinstance(title, str) or not title:
        return None

    artist_name = attrs.get("artistName") if isinstance(attrs, dict) else None
    # amp-api hands back collabs as ONE string ("Surfaces & salem ilese"); split
    # so the matcher's set-overlap rule works against Spotify's pre-split list.
    artists = (
        tuple(split_artist_string(artist_name))
        if isinstance(artist_name, str) and artist_name
        else ()
    )

    album = attrs.get("albumName") if isinstance(attrs, dict) else None
    if not isinstance(album, str) or not album:
        album = None

    duration_ms = attrs.get("durationInMillis") if isinstance(attrs, dict) else None
    if not isinstance(duration_ms, int):
        duration_ms = None

    sid = item.get("id")
    source_id = str(sid) if sid is not None else None

    isrc_raw = attrs.get("isrc") if isinstance(attrs, dict) else None
    isrc = isrc_raw.strip().upper() if isinstance(isrc_raw, str) and isrc_raw else None

    return Track(
        title=title,
        artists=artists,
        album=album,
        duration_ms=duration_ms,
        source="apple",
        source_id=source_id,
        isrc=isrc,
    )


# ---------------------------------------------------------------------------
# HTML scrape fallback
# ---------------------------------------------------------------------------


def _fetch_via_html(session: requests.Session, url: str) -> list[Track]:
    resp = session.get(url, headers=_BROWSER_HEADERS, timeout=_REQUEST_TIMEOUT)
    if resp.status_code != 200:
        raise AppleMusicError(
            f"GET {url} failed ({resp.status_code}): {resp.text[:200]}"
        )
    return parse_playlist_html(resp.text)


def parse_playlist_html(html: str) -> list[Track]:
    """Extract tracks from the server-rendered playlist HTML (first ~300)."""
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("script", id="serialized-server-data")
    if tag is None:
        raise AppleMusicError(
            "Could not find the 'serialized-server-data' block in the Apple "
            "Music page. The page format may have changed, or the playlist "
            "may not be publicly shared."
        )
    raw = tag.string or tag.get_text() or ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AppleMusicError(f"Failed to parse Apple Music JSON payload: {exc}") from exc

    tracks = list(_extract_tracks_from_html(payload))
    if not tracks:
        raise AppleMusicError(
            "Parsed the Apple Music page but found no tracks. The playlist "
            "may be empty, region-restricted, or the page structure has "
            "changed in a way the parser does not handle."
        )
    return tracks


def _extract_tracks_from_html(payload: Any) -> Iterable[Track]:
    for section in _iter_sections(payload):
        section_id = section.get("id", "")
        if not isinstance(section_id, str):
            continue
        if not section_id.startswith(_TRACK_SECTION_PREFIX):
            continue
        if "footer" in section_id:
            continue
        for item in section.get("items", []) or []:
            track = _track_from_html_item(item)
            if track is not None:
                yield track


def _iter_sections(payload: Any) -> Iterable[dict[str, Any]]:
    for page in _as_list(payload.get("data")) if isinstance(payload, dict) else []:
        if not isinstance(page, dict):
            continue
        page_data = page.get("data")
        if not isinstance(page_data, dict):
            continue
        for section in page_data.get("sections", []) or []:
            if isinstance(section, dict):
                yield section


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


def _track_from_html_item(item: Any) -> Track | None:
    if not isinstance(item, dict):
        return None
    title = item.get("title")
    if not isinstance(title, str) or not title:
        return None

    artists = _extract_link_titles(item.get("subtitleLinks"))
    album = _first_link_title(item.get("tertiaryLinks"))
    duration_ms = item.get("duration")
    if not isinstance(duration_ms, int):
        duration_ms = None
    source_id = _source_id_from_content_descriptor(item.get("contentDescriptor"))

    return Track(
        title=title,
        artists=artists,
        album=album,
        duration_ms=duration_ms,
        source="apple",
        source_id=source_id,
    )


def _extract_link_titles(links: Any) -> tuple[str, ...]:
    if not isinstance(links, list):
        return ()
    out: list[str] = []
    for link in links:
        if isinstance(link, dict):
            t = link.get("title")
            if isinstance(t, str) and t:
                # A single subtitleLink can occasionally hold a compound
                # credit ("Surfaces & salem ilese"); split defensively so the
                # output of this fallback path matches the amp-api path.
                out.extend(split_artist_string(t))
    return tuple(out)


def _first_link_title(links: Any) -> str | None:
    titles = _extract_link_titles(links)
    return titles[0] if titles else None


def _source_id_from_content_descriptor(cd: Any) -> str | None:
    if not isinstance(cd, dict):
        return None
    ids = cd.get("identifiers")
    if not isinstance(ids, dict):
        return None
    adam = ids.get("storeAdamID")
    if isinstance(adam, str) and adam:
        return adam
    if isinstance(adam, int):
        return str(adam)
    return None
