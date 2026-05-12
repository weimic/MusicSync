"""Tests for music_diff.apple_client.parse_playlist_html.

The fixture is a trimmed slice of a live Apple Music playlist page so that
silent regressions in the real-world payload surface here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from music_diff.apple_client import AppleMusicError, parse_playlist_html

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestSerializedPayload:
    def test_parses_three_tracks(self) -> None:
        tracks = parse_playlist_html(_read("apple_serialized.html"))
        assert [t.title for t in tracks] == [
            "Sunshine",
            "Come With Me",
            "family ties",
        ]

    def test_extracts_single_and_multi_artist_correctly(self) -> None:
        tracks = parse_playlist_html(_read("apple_serialized.html"))
        assert tracks[0].artists == ("OneRepublic",)
        assert tracks[1].artists == ("Surfaces", "salem ilese")
        assert tracks[2].artists == ("Baby Keem", "Kendrick Lamar")

    def test_albums_present_but_not_used_for_match(self) -> None:
        tracks = parse_playlist_html(_read("apple_serialized.html"))
        # We carry album for display only; presence is enough to verify.
        assert tracks[0].album == "Sunshine. The EP"
        assert tracks[2].album == "The Melodic Blue (Deluxe)"

    def test_duration_already_in_ms(self) -> None:
        tracks = parse_playlist_html(_read("apple_serialized.html"))
        assert tracks[0].duration_ms == 163855
        assert tracks[1].duration_ms == 209732
        assert tracks[2].duration_ms == 252262

    def test_extracts_store_adam_id(self) -> None:
        tracks = parse_playlist_html(_read("apple_serialized.html"))
        assert tracks[0].source_id == "1600780752"
        assert tracks[1].source_id == "1711473159"


class TestErrorPaths:
    def test_raises_when_no_data_block(self) -> None:
        with pytest.raises(AppleMusicError, match="serialized-server-data"):
            parse_playlist_html("<html><body>no data here</body></html>")

    def test_raises_when_payload_has_no_tracks(self) -> None:
        empty = (
            "<html><head>"
            '<script id="serialized-server-data" type="application/json">'
            '{"data": []}'
            "</script></head></html>"
        )
        with pytest.raises(AppleMusicError, match="no tracks"):
            parse_playlist_html(empty)
