"""Tests for the amp-api path in music_diff.apple_client.

We don't make real HTTP calls; instead we inject a fake ``requests.Session``
whose ``get`` returns canned responses keyed by URL substring. That gives us
deterministic coverage of:

* URL -> (storefront, playlist_id) parsing,
* bearer-token discovery (root page -> JS bundle),
* amp-api pagination via the ``next`` cursor,
* graceful fallback to HTML scraping when the amp-api path fails.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

from music_diff.apple_client import (
    AppleMusicError,
    fetch_playlist_tracks,
    parse_playlist_url,
)

FIXTURES = Path(__file__).parent / "fixtures"

_BUNDLE_PATH = "/assets/index~abc123.js"
_BUNDLE_JS = (
    'const t = "eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9.'
    'eyJpc3MiOiJBTVBXZWJQbGF5In0.'
    "AAAA-BBBB_CCCC-DDDD_EEEE-FFFF_GGGG-HHHH_IIII-JJJJ"
    '";'
)
_HOME_HTML = f'<html><body><script src="{_BUNDLE_PATH}"></script></body></html>'


@dataclass
class _Resp:
    status_code: int = 200
    text: str = ""
    _json: object | None = None

    def json(self) -> object:
        if self._json is None:
            return json.loads(self.text)
        return self._json


class FakeSession:
    """Minimal stand-in for ``requests.Session`` whose ``get`` is router-driven."""

    def __init__(self, route: Callable[[str], _Resp]) -> None:
        self._route = route
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs: object) -> _Resp:  # noqa: D401
        self.calls.append(url)
        return self._route(url)


def _track_payload(
    track_id: int,
    name: str,
    artist: str,
    album: str,
    duration_ms: int,
    isrc: str | None = None,
) -> dict[str, object]:
    attrs: dict[str, object] = {
        "name": name,
        "artistName": artist,
        "albumName": album,
        "durationInMillis": duration_ms,
    }
    if isrc:
        attrs["isrc"] = isrc
    return {"id": str(track_id), "type": "songs", "attributes": attrs}


def _amp_response(items: list[dict[str, object]], next_path: str | None) -> _Resp:
    body: dict[str, object] = {"data": items}
    if next_path is not None:
        body["next"] = next_path
    return _Resp(status_code=200, text=json.dumps(body))


class TestParsePlaylistUrl:
    def test_extracts_us_storefront(self) -> None:
        store, pid = parse_playlist_url(
            "https://music.apple.com/us/playlist/main/pl.u-pMyl16RT51ymgG8"
        )
        assert store == "us"
        assert pid == "pl.u-pMyl16RT51ymgG8"

    def test_handles_trailing_whitespace_and_slash(self) -> None:
        store, pid = parse_playlist_url(
            "  https://music.apple.com/gb/playlist/my-list/pl.abc123XYZ/  "
        )
        assert store == "gb"
        assert pid == "pl.abc123XYZ"

    def test_rejects_non_apple_url(self) -> None:
        with pytest.raises(AppleMusicError, match="Not an Apple Music URL"):
            parse_playlist_url("https://example.com/")

    def test_rejects_malformed_path(self) -> None:
        with pytest.raises(AppleMusicError, match="Could not parse"):
            parse_playlist_url("https://music.apple.com/us/album/foo/12345")


class TestAmpApiHappyPath:
    def test_paginates_via_next_cursor(self) -> None:
        page1 = _amp_response(
            [
                _track_payload(
                    1, "Sunshine", "OneRepublic", "Sunshine. The EP", 163855,
                    isrc="USUM72100506",
                ),
                # Compound artistName must be split by the ingest path.
                _track_payload(
                    2, "Come With Me", "Surfaces & salem ilese",
                    "Pacifico", 209732,
                ),
            ],
            next_path="/v1/catalog/us/playlists/pl.test/tracks?offset=2",
        )
        page2 = _amp_response(
            [_track_payload(3, "family ties", "Baby Keem", "The Melodic Blue", 252262)],
            next_path=None,
        )

        def route(url: str) -> _Resp:
            if url.endswith("/us/"):
                return _Resp(status_code=200, text=_HOME_HTML)
            if url.endswith(_BUNDLE_PATH):
                return _Resp(status_code=200, text=_BUNDLE_JS)
            if "offset=0" in url:
                return page1
            if "offset=2" in url:
                return page2
            raise AssertionError(f"unexpected url: {url}")

        sess = FakeSession(route)
        tracks = fetch_playlist_tracks(
            "https://music.apple.com/us/playlist/main/pl.test", session=sess
        )

        assert [t.title for t in tracks] == ["Sunshine", "Come With Me", "family ties"]
        assert [t.artists for t in tracks] == [
            ("OneRepublic",),
            ("Surfaces", "salem ilese"),  # split by split_artist_string
            ("Baby Keem",),
        ]
        assert tracks[0].duration_ms == 163855
        assert tracks[0].source_id == "1"
        assert tracks[0].isrc == "USUM72100506"
        assert tracks[1].isrc is None
        assert tracks[2].source_id == "3"
        # We expect: root, bundle, page1, page2 - in that order.
        assert any(c.endswith("/us/") for c in sess.calls)
        assert any(c.endswith(_BUNDLE_PATH) for c in sess.calls)
        offsets = [c for c in sess.calls if "amp-api" in c]
        assert len(offsets) == 2
        assert "offset=0" in offsets[0]
        assert "offset=2" in offsets[1]


class TestAmpApiFailureFallsBackToHtml:
    def test_falls_back_when_token_extraction_fails(self) -> None:
        # Root page exists but contains no JS bundle reference.
        fixture_html = (FIXTURES / "apple_serialized.html").read_text(encoding="utf-8")

        def route(url: str) -> _Resp:
            if url.endswith("/us/"):
                return _Resp(status_code=200, text="<html><body>nope</body></html>")
            # The fallback re-fetches the playlist URL itself.
            return _Resp(status_code=200, text=fixture_html)

        sess = FakeSession(route)
        tracks = fetch_playlist_tracks(
            "https://music.apple.com/us/playlist/main/pl.test", session=sess
        )
        assert [t.title for t in tracks] == ["Sunshine", "Come With Me", "family ties"]

    def test_falls_back_on_amp_api_error_status(self) -> None:
        fixture_html = (FIXTURES / "apple_serialized.html").read_text(encoding="utf-8")

        def route(url: str) -> _Resp:
            if url.endswith("/us/"):
                return _Resp(status_code=200, text=_HOME_HTML)
            if url.endswith(_BUNDLE_PATH):
                return _Resp(status_code=200, text=_BUNDLE_JS)
            if "amp-api" in url:
                return _Resp(status_code=403, text="Forbidden")
            return _Resp(status_code=200, text=fixture_html)

        sess = FakeSession(route)
        tracks = fetch_playlist_tracks(
            "https://music.apple.com/us/playlist/main/pl.test", session=sess
        )
        assert len(tracks) == 3
