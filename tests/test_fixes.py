"""Focused regression coverage for the five fixes documented in the README.

One test class per fix so any future failure points at exactly which
contract regressed.
"""

from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path
from typing import Iterable

import pytest

from music_diff.matcher import diff
from music_diff.models import Track
from music_diff.normalize import (
    normalize_artists,
    normalize_title,
    split_artist_string,
)
from music_diff.report import _format_candidate, write_output


def _spot(
    title: str,
    artists: Iterable[str] = ("Anon",),
    *,
    isrc: str | None = None,
    source_id: str | None = None,
    duration_ms: int | None = None,
    album: str | None = None,
) -> Track:
    return Track(
        title=title,
        artists=tuple(artists),
        album=album,
        duration_ms=duration_ms,
        source="spotify",
        source_id=source_id,
        isrc=isrc,
    )


def _apple(
    title: str,
    artists: Iterable[str] = ("Anon",),
    *,
    isrc: str | None = None,
    source_id: str | None = None,
    duration_ms: int | None = None,
    album: str | None = None,
) -> Track:
    return Track(
        title=title,
        artists=tuple(artists),
        album=album,
        duration_ms=duration_ms,
        source="apple",
        source_id=source_id,
        isrc=isrc,
    )


# ---------------------------------------------------------------------------
# Fix 1: robust title normalization
# ---------------------------------------------------------------------------


class TestFix1TitleNormalization:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1989 (Taylor's Version)", "1989"),
            ("1989 (Taylor\u2019s Version)", "1989"),
            ("Bad Blood - Taylor's Version", "bad blood"),
            ("Hello - Single", "hello"),
            ("Wonderwall (with Liam Gallagher)", "wonderwall"),
            ("Mixtape - EP", "mixtape"),
            ("Anti-Hero (feat. Bleachers)", "anti hero"),
        ],
    )
    def test_strips_extended_tag_set(self, raw: str, expected: str) -> None:
        assert normalize_title(raw) == expected


# ---------------------------------------------------------------------------
# Fix 2: set-based artist matching with delimiter splitting
# ---------------------------------------------------------------------------


class TestFix2ArtistSplitting:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Surfaces & salem ilese", ["Surfaces", "salem ilese"]),
            ("Taylor Swift, Kendrick Lamar", ["Taylor Swift", "Kendrick Lamar"]),
            ("Calvin Harris and Dua Lipa", ["Calvin Harris", "Dua Lipa"]),
            ("Drake feat. Future", ["Drake", "Future"]),
            ("Jay-Z + Kanye West", ["Jay-Z", "Kanye West"]),
            ("Daft Punk", ["Daft Punk"]),
        ],
    )
    def test_split(self, raw: str, expected: list[str]) -> None:
        assert split_artist_string(raw) == expected

    def test_set_intersection_across_delimiters(self) -> None:
        # The whole point: Apple's compound string and Spotify's pre-split
        # tuple must produce overlapping sets.
        apple_set = normalize_artists(("Surfaces & salem ilese",))
        spot_set = normalize_artists(("Surfaces", "salem ilese"))
        assert apple_set & spot_set == frozenset({"surfaces", "salem ilese"})

    def test_matcher_now_matches_compound_apple_artist(self) -> None:
        # Reproduces the real-world bug: same recording, Apple gives "A & B",
        # Spotify gives ("A", "B"). Before the fix this went to only_apple.
        spot = [_spot("Come With Me", ("Surfaces", "salem ilese"))]
        app = [_apple("Come With Me", ("Surfaces & salem ilese",))]
        r = diff(spot, app)
        assert len(r.matched) == 1
        assert not r.only_spotify
        assert not r.only_apple


# ---------------------------------------------------------------------------
# Fix 3: enforced string types for IDs in CSV output
# ---------------------------------------------------------------------------


class TestFix3StringIds:
    def test_numeric_apple_id_serializes_as_string(self, tmp_path: Path) -> None:
        # Construct a Track with a numeric-looking ID. The CSV writer must
        # emit it verbatim, not in scientific notation; missing IDs must be
        # empty strings, never the literal 'None' or 'nan'.
        spot = [_spot("Foo", source_id=None, isrc=None)]
        app = [_apple("Foo", source_id="1600780752", isrc=None)]
        r = diff(spot, app)

        out = tmp_path / "out.csv"
        write_output(r, out, "csv")
        rows = list(csv.DictReader(StringIO(out.read_text(encoding="utf-8"))))
        # The single matched row pairs apple's id verbatim and spotify's empty.
        matched_row = next(r for r in rows if r["bucket"] == "matched")
        assert matched_row["apple_source_id"] == "1600780752"
        assert matched_row["spotify_source_id"] == ""
        # Belt and suspenders: nothing leaked the literal 'None' / 'nan' /
        # scientific notation.
        for value in matched_row.values():
            assert value not in {"None", "nan", "NaN"}
            assert "e+" not in value.lower()


# ---------------------------------------------------------------------------
# Fix 4: enriched ambiguous candidate formatting
# ---------------------------------------------------------------------------


class TestFix4AmbiguousFormat:
    def test_format_includes_album_duration_id(self) -> None:
        c = _spot(
            "Bad Blood",
            ("Taylor Swift", "Kendrick Lamar"),
            album="1989 (Deluxe)",
            duration_ms=211_000,
            source_id="orig",
        )
        s = _format_candidate(c)
        assert s == (
            "Bad Blood (Taylor Swift, Kendrick Lamar) "
            "[1989 (Deluxe)] - 3:31 - ID: orig"
        )

    def test_missing_metadata_renders_dashes_not_blank(self) -> None:
        c = _spot("Lonely Track", ())
        s = _format_candidate(c)
        # Structure (parens, brackets, separators) is preserved even when
        # every metadata field is missing - keeps the column parseable.
        assert s == "Lonely Track (-) [-] - - - ID: -"

    def test_distinct_candidates_render_distinctly(self, tmp_path: Path) -> None:
        # Regression for "duplicate strings in ambiguous_candidates": two
        # candidates that share a title but differ in album/duration/id
        # MUST be distinguishable in the rendered cell.
        spot = [
            _spot("Bad Blood", ("Taylor Swift",),
                  album="1989", duration_ms=211_000, source_id="orig"),
            _spot("Bad Blood", ("Taylor Swift", "Kendrick Lamar"),
                  album="1989 (Deluxe)", duration_ms=193_000, source_id="remix"),
        ]
        app = [_apple("Bad Blood", ("Taylor Swift",))]
        r = diff(spot, app)
        assert len(r.ambiguous) == 1

        out = tmp_path / "out.csv"
        write_output(r, out, "csv")
        rows = list(csv.DictReader(StringIO(out.read_text(encoding="utf-8"))))
        amb = next(r for r in rows if r["bucket"] == "ambiguous")
        cell = amb["ambiguous_candidates"]
        # Cell has both candidates, separated by ' | ', and they render as
        # distinct strings (not the same one twice).
        parts = cell.split(" | ")
        assert len(parts) == 2
        assert parts[0] != parts[1]
        assert "ID: orig" in cell and "ID: remix" in cell
        assert "[1989]" in cell and "[1989 (Deluxe)]" in cell


# ---------------------------------------------------------------------------
# Fix 5: ISRC-first deterministic matching
# ---------------------------------------------------------------------------


class TestFix5IsrcMatching:
    def test_same_isrc_matches_even_with_wildly_different_titles(self) -> None:
        # ISRC is the recording's true identity; a localized / repackaged
        # title on one side must NOT defeat matching when the ISRC agrees.
        spot = [_spot("Despacito", ("Luis Fonsi",), isrc="QMRSZ1700255")]
        app = [_apple("???", ("???",), isrc="QMRSZ1700255")]
        r = diff(spot, app)
        assert len(r.matched) == 1
        assert not r.only_spotify
        assert not r.only_apple
        assert not r.ambiguous

    def test_isrc_consumes_before_title_heuristic(self) -> None:
        # Two Spotify tracks share the Apple track's normalized title but
        # only one shares its ISRC. ISRC pre-pass must pick the ISRC twin
        # and leave the title-twin in only_spotify - NOT route to ambiguous.
        spot = [
            _spot("Bad Blood", ("Taylor Swift",), isrc="USCJY1431530", source_id="iso"),
            _spot("Bad Blood", ("Taylor Swift",), isrc="USCJY9999999", source_id="other"),
        ]
        app = [_apple("Bad Blood", ("Taylor Swift",), isrc="USCJY1431530")]
        r = diff(spot, app)
        assert len(r.matched) == 1
        assert r.matched[0].spotify.source_id == "iso"
        assert not r.ambiguous
        assert {t.source_id for t in r.only_spotify} == {"other"}

    def test_missing_isrc_falls_through_to_heuristics(self) -> None:
        # If neither side has ISRC, behavior must match the legacy heuristic.
        spot = [_spot("Hello", ("Adele",))]
        app = [_apple("Hello", ("Adele",))]
        r = diff(spot, app)
        assert len(r.matched) == 1

    def test_isrc_case_insensitive(self) -> None:
        spot = [_spot("X", isrc="usum72100506")]
        app = [_apple("Y", isrc="USUM72100506")]
        r = diff(spot, app)
        assert len(r.matched) == 1
