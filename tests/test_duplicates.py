"""Tests for music_diff.duplicates.find_duplicates."""

from __future__ import annotations

from typing import Iterable

from music_diff.duplicates import DuplicateReport, find_duplicates
from music_diff.models import Track


def _t(
    title: str,
    artists: Iterable[str] = ("Anon",),
    *,
    isrc: str | None = None,
    source_id: str | None = None,
) -> Track:
    return Track(
        title=title,
        artists=tuple(artists),
        album=None,
        duration_ms=None,
        source="spotify",
        source_id=source_id,
        isrc=isrc,
    )


class TestEmptyAndNoDuplicates:
    def test_empty_input(self) -> None:
        r = find_duplicates([])
        assert isinstance(r, DuplicateReport)
        assert r.by_isrc == []
        assert r.by_title_artist == []
        assert r.total_groups == 0

    def test_singletons_never_grouped(self) -> None:
        tracks = [_t("Hello", ("Adele",), isrc="GB1101300390")]
        r = find_duplicates(tracks)
        assert r.by_isrc == []
        assert r.by_title_artist == []


class TestIsrcDuplicates:
    def test_same_isrc_groups(self) -> None:
        a = _t("Hello", ("Adele",), isrc="GB1101300390", source_id="a")
        b = _t("Hello (Single Version)", ("Adele",), isrc="gb1101300390", source_id="b")
        r = find_duplicates([a, b])
        assert len(r.by_isrc) == 1
        group = r.by_isrc[0]
        assert group.key == "GB1101300390"
        assert {t.source_id for t in group.tracks} == {"a", "b"}

    def test_isrcs_are_case_insensitive(self) -> None:
        a = _t("X", isrc="usum72100506", source_id="a")
        b = _t("X", isrc="USUM72100506", source_id="b")
        r = find_duplicates([a, b])
        assert len(r.by_isrc) == 1

    def test_missing_isrcs_ignored(self) -> None:
        a = _t("X", isrc=None, source_id="a")
        b = _t("X", isrc=None, source_id="b")
        r = find_duplicates([a, b])
        assert r.by_isrc == []


class TestTitleArtistDuplicates:
    def test_same_normalized_title_and_artist_groups(self) -> None:
        a = _t("Bad Blood", ("Taylor Swift",), source_id="a")
        b = _t("Bad Blood (feat. Kendrick Lamar)", ("Taylor Swift",), source_id="b")
        r = find_duplicates([a, b])
        # feat. is stripped by normalize_title -> both share key "bad blood"
        # and the same normalized artist set {"taylor swift"}.
        assert len(r.by_title_artist) == 1
        assert {t.source_id for t in r.by_title_artist[0].tracks} == {"a", "b"}

    def test_different_artist_set_does_not_group(self) -> None:
        a = _t("Hello", ("Adele",), source_id="a")
        b = _t("Hello", ("Lionel Richie",), source_id="b")
        r = find_duplicates([a, b])
        assert r.by_title_artist == []

    def test_blank_title_ignored(self) -> None:
        a = _t("", ("X",), source_id="a")
        b = _t("", ("X",), source_id="b")
        r = find_duplicates([a, b])
        assert r.by_title_artist == []


class TestBothViewsCanCoexist:
    def test_isrc_dupes_also_appear_in_title_pass_when_keys_align(self) -> None:
        # Same recording added twice with the same metadata - shows up in
        # both views, by design (the user sees both pieces of evidence).
        a = _t("Hello", ("Adele",), isrc="GB1101300390", source_id="a")
        b = _t("Hello", ("Adele",), isrc="GB1101300390", source_id="b")
        r = find_duplicates([a, b])
        assert len(r.by_isrc) == 1
        assert len(r.by_title_artist) == 1
        assert r.total_groups == 2
