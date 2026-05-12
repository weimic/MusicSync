"""Tests for music_diff.matcher.diff."""

from __future__ import annotations

import pytest

from music_diff.matcher import (
    AmbiguousCase,
    MatchResult,
    diff,
    promote_to_match,
)
from music_diff.models import Track


def s(
    title: str,
    artists: tuple[str, ...] = ("Anon",),
    *,
    album: str | None = None,
    duration_ms: int | None = None,
    source_id: str | None = None,
) -> Track:
    return Track(
        title=title,
        artists=artists,
        album=album,
        duration_ms=duration_ms,
        source="spotify",
        source_id=source_id,
    )


def a(
    title: str,
    artists: tuple[str, ...] = ("Anon",),
    *,
    album: str | None = None,
    duration_ms: int | None = None,
    source_id: str | None = None,
) -> Track:
    return Track(
        title=title,
        artists=artists,
        album=album,
        duration_ms=duration_ms,
        source="apple",
        source_id=source_id,
    )


class TestExactMatching:
    def test_identical_titles_and_artists_match(self) -> None:
        spot = [s("Hello", ("Adele",))]
        app = [a("Hello", ("Adele",))]
        r = diff(spot, app)
        assert len(r.matched) == 1
        assert r.matched[0].apple.title == "Hello"
        assert not r.only_spotify
        assert not r.only_apple
        assert not r.ambiguous

    def test_album_difference_does_not_block_match(self) -> None:
        # Same recording cataloged on a "Greatest Hits" album on one service
        # and a single on the other. Must still match.
        spot = [s("Hello", ("Adele",), album="25 (Greatest Hits)")]
        app = [a("Hello", ("Adele",), album="Hello - Single")]
        r = diff(spot, app)
        assert len(r.matched) == 1


class TestNormalizationDriven:
    def test_feat_placement_difference_matches(self) -> None:
        # Spotify lists Kendrick as an artist; Apple folds him into the title.
        spot = [s("Bad Blood", ("Taylor Swift", "Kendrick Lamar"))]
        app = [a("Bad Blood (feat. Kendrick Lamar)", ("Taylor Swift",))]
        r = diff(spot, app)
        assert len(r.matched) == 1

    def test_remaster_suffix_matches_original(self) -> None:
        spot = [s("Wonderwall", ("Oasis",))]
        app = [a("Wonderwall - Remastered", ("Oasis",))]
        r = diff(spot, app)
        assert len(r.matched) == 1

    def test_diacritics_match(self) -> None:
        spot = [s("Cafe del Mar", ("Energy 52",))]
        app = [a("Café del Mar", ("Energy 52",))]
        r = diff(spot, app)
        assert len(r.matched) == 1


class TestArtistGate:
    def test_same_title_different_artist_is_no_match(self) -> None:
        spot = [s("Hello", ("Lionel Richie",))]
        app = [a("Hello", ("Adele",))]
        r = diff(spot, app)
        assert not r.matched
        assert len(r.only_spotify) == 1
        assert len(r.only_apple) == 1


class TestAmbiguous:
    def test_collision_without_duration_goes_ambiguous(self) -> None:
        spot = [
            s("Bad Blood", ("Taylor Swift",), source_id="orig"),
            s("Bad Blood", ("Taylor Swift", "Kendrick Lamar"), source_id="remix"),
        ]
        app = [a("Bad Blood", ("Taylor Swift",))]
        r = diff(spot, app)
        assert not r.matched
        assert len(r.ambiguous) == 1
        ids = {c.source_id for c in r.ambiguous[0].candidates}
        assert ids == {"orig", "remix"}
        # Both candidates also survive in only_spotify since nothing consumed them.
        assert {t.source_id for t in r.only_spotify} == {"orig", "remix"}

    def test_duration_breaks_tie(self) -> None:
        spot = [
            s("Bad Blood", ("Taylor Swift",), duration_ms=211_000, source_id="orig"),
            s(
                "Bad Blood",
                ("Taylor Swift", "Kendrick Lamar"),
                duration_ms=193_000,
                source_id="remix",
            ),
        ]
        app = [a("Bad Blood", ("Taylor Swift",), duration_ms=210_500)]
        r = diff(spot, app)
        assert len(r.matched) == 1
        assert r.matched[0].spotify.source_id == "orig"
        assert not r.ambiguous

    def test_duration_within_window_for_multiple_stays_ambiguous(self) -> None:
        spot = [
            s("Foo", duration_ms=180_000, source_id="a"),
            s("Foo", duration_ms=181_500, source_id="b"),
        ]
        app = [a("Foo", duration_ms=180_500)]
        r = diff(spot, app)
        assert not r.matched
        assert len(r.ambiguous) == 1


class TestPromoteToMatch:
    def test_promotes_and_removes_from_only_spotify(self) -> None:
        spot = [
            s("Bad Blood", ("Taylor Swift",), source_id="orig"),
            s("Bad Blood", ("Taylor Swift", "Kendrick Lamar"), source_id="remix"),
        ]
        app = [a("Bad Blood", ("Taylor Swift",))]
        r = diff(spot, app)
        assert len(r.ambiguous) == 1

        case = r.ambiguous[0]
        chosen = next(c for c in case.candidates if c.source_id == "remix")
        promote_to_match(r, case, chosen)

        assert not r.ambiguous
        assert len(r.matched) == 1
        assert r.matched[0].spotify.source_id == "remix"
        assert {t.source_id for t in r.only_spotify} == {"orig"}

    def test_promote_rejects_non_candidate(self) -> None:
        spot = [s("X"), s("X")]
        app = [a("X")]
        r = diff(spot, app)
        assert len(r.ambiguous) == 1
        with pytest.raises(ValueError):
            promote_to_match(r, r.ambiguous[0], s("Different Song"))


class TestNotInOther:
    def test_unmatched_split_correctly(self) -> None:
        spot = [s("OnlyOnSpotify", ("X",)), s("Shared", ("Y",))]
        app = [a("OnlyOnApple", ("Z",)), a("Shared", ("Y",))]
        r = diff(spot, app)
        assert [t.title for t in r.only_spotify] == ["OnlyOnSpotify"]
        assert [t.title for t in r.only_apple] == ["OnlyOnApple"]
        assert len(r.matched) == 1


class TestStability:
    def test_empty_inputs(self) -> None:
        r = diff([], [])
        assert isinstance(r, MatchResult)
        assert not r.matched
        assert not r.only_spotify
        assert not r.only_apple
        assert not r.ambiguous

    def test_one_side_empty(self) -> None:
        r = diff([s("A")], [])
        assert [t.title for t in r.only_spotify] == ["A"]
        r2 = diff([], [a("B")])
        assert [t.title for t in r2.only_apple] == ["B"]
