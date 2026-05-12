"""Tests for music_diff.normalize."""

from __future__ import annotations

import pytest

from music_diff.normalize import (
    normalize_artist,
    normalize_artists,
    normalize_title,
)


class TestNormalizeTitle:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Bad Blood", "bad blood"),
            ("  Bad   Blood  ", "bad blood"),
            ("Café del Mar", "cafe del mar"),
            ("AC/DC - Back in Black", "ac dc back in black"),
            ("Bad Blood (feat. Kendrick Lamar)", "bad blood"),
            ("Bad Blood (Feat. Kendrick Lamar)", "bad blood"),
            ("Bad Blood [feat. Kendrick Lamar]", "bad blood"),
            ("Wonderwall - Remastered", "wonderwall"),
            ("Wonderwall - Remastered 2011", "wonderwall"),
            ("Wonderwall (Remastered 2011)", "wonderwall"),
            ("Africa - Single Version", "africa"),
            ("Africa (Radio Edit)", "africa"),
            ("Hey Jude - Mono Version", "hey jude"),
            ("Song (feat. A) [Remastered 2009]", "song"),
        ],
    )
    def test_canonicalizes(self, raw: str, expected: str) -> None:
        assert normalize_title(raw) == expected

    def test_keeps_distinguishing_descriptors(self) -> None:
        # "Live" can be a distinguishing recording difference; we deliberately
        # do NOT strip it from titles because conflating live and studio
        # versions would be a correctness regression.
        assert normalize_title("Wonderwall - Live at Wembley") != normalize_title("Wonderwall")

    def test_empty(self) -> None:
        assert normalize_title("") == ""


class TestNormalizeArtist:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Beyoncé", "beyonce"),
            ("The Beatles", "beatles"),
            ("THE BEATLES", "beatles"),
            ("AC/DC", "ac dc"),
            ("Tyler, The Creator", "tyler the creator"),
        ],
    )
    def test_canonicalizes(self, raw: str, expected: str) -> None:
        assert normalize_artist(raw) == expected


class TestNormalizeArtists:
    def test_set_intersect(self) -> None:
        a = normalize_artists(("Taylor Swift", "Kendrick Lamar"))
        b = normalize_artists(("kendrick lamar",))
        assert a & b == frozenset({"kendrick lamar"})

    def test_drops_empty(self) -> None:
        assert normalize_artists(("", "Adele")) == frozenset({"adele"})
