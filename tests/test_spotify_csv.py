"""Tests for music_diff.spotify_csv.read_exportify_csv."""

from __future__ import annotations

from pathlib import Path

import pytest

from music_diff.spotify_csv import SpotifyCsvError, read_exportify_csv

FIXTURES = Path(__file__).parent / "fixtures"


class TestExportifyCsv:
    def test_reads_three_tracks(self) -> None:
        tracks = read_exportify_csv(FIXTURES / "exportify_sample.csv")
        assert [t.title for t in tracks] == [
            "Bad Blood",
            "Café del Mar",
            "Wonderwall - Remastered",
        ]

    def test_splits_multi_artist_field(self) -> None:
        tracks = read_exportify_csv(FIXTURES / "exportify_sample.csv")
        assert tracks[0].artists == ("Taylor Swift", "Kendrick Lamar")
        assert tracks[1].artists == ("Energy 52",)

    def test_parses_duration_ms(self) -> None:
        tracks = read_exportify_csv(FIXTURES / "exportify_sample.csv")
        assert tracks[0].duration_ms == 211934
        assert tracks[1].duration_ms == 425000

    def test_extracts_track_id_from_uri(self) -> None:
        tracks = read_exportify_csv(FIXTURES / "exportify_sample.csv")
        assert tracks[0].source_id == "1zHlj4dQ8ZAtrayhuDDmkY"
        assert tracks[2].source_id == "1qPbGZqppFwLwcBC1JaqEd"

    def test_extracts_isrc(self) -> None:
        tracks = read_exportify_csv(FIXTURES / "exportify_sample.csv")
        assert tracks[0].isrc == "USCJY1431530"
        assert tracks[1].isrc == "DEXX12345678"

    def test_carries_album_as_display_only(self) -> None:
        tracks = read_exportify_csv(FIXTURES / "exportify_sample.csv")
        assert tracks[0].album == "1989 (Deluxe Edition)"

    def test_skips_blank_rows(self) -> None:
        tracks = read_exportify_csv(FIXTURES / "exportify_sample.csv")
        assert len(tracks) == 3

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(SpotifyCsvError, match="not found"):
            read_exportify_csv(tmp_path / "does_not_exist.csv")

    def test_missing_required_column_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.csv"
        bad.write_text("Foo,Bar\n1,2\n", encoding="utf-8")
        with pytest.raises(SpotifyCsvError, match="missing required column"):
            read_exportify_csv(bad)

    def test_accepts_alias_column_names(self, tmp_path: Path) -> None:
        # A hypothetical Exportify fork might use shorter names.
        alt = tmp_path / "alt.csv"
        alt.write_text(
            "URI,Name,Artists,Album,Duration\n"
            "spotify:track:abc,Hello,Adele,25,295000\n",
            encoding="utf-8",
        )
        tracks = read_exportify_csv(alt)
        assert tracks == [
            # We rely on the same Track dataclass; field-by-field check below.
            tracks[0],
        ]
        t = tracks[0]
        assert t.title == "Hello"
        assert t.artists == ("Adele",)
        assert t.album == "25"
        assert t.duration_ms == 295000
        assert t.source_id == "abc"
        assert t.source == "spotify"
