"""Render a ``MatchResult`` to the console and to CSV/JSON files."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

from rich.console import Console
from rich.table import Table

from .matcher import AmbiguousCase, MatchedPair, MatchResult
from .models import Track

_OUTPUT_FORMATS = ("csv", "json")


def _fmt_duration(ms: int | None) -> str:
    if ms is None:
        return ""
    seconds = ms // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


def _s(value: object) -> str:
    """Coerce any value to a CSV/JSON-safe string with empty-string default.

    This is what keeps numeric track IDs from leaking out as
    floats / scientific notation when downstream tools (Excel, pandas)
    try to be helpful and reinterpret an unquoted numeric string. We treat
    every identifier as a string from the moment it leaves the wire.
    """
    if value is None:
        return ""
    return str(value)


def _track_row(t: Track) -> dict[str, str]:
    return {
        "source": _s(t.source),
        "title": _s(t.title),
        "artists": ", ".join(t.artists),
        "album": _s(t.album),
        "duration": _fmt_duration(t.duration_ms),
        "source_id": _s(t.source_id),
        "isrc": _s(t.isrc),
    }


def _format_candidate(c: Track) -> str:
    """Human-readable, single-line candidate descriptor for the CSV.

    Format: ``Title (Artist) [Album] - mm:ss - ID: xxx``. Empty fields are
    rendered as ``-`` so the structure is preserved even on missing data
    and downstream parsers/operators can split on the constant separators.
    """
    artists = ", ".join(c.artists) if c.artists else "-"
    album = c.album if c.album else "-"
    duration = _fmt_duration(c.duration_ms) or "-"
    sid = _s(c.source_id) or "-"
    return f"{c.title} ({artists}) [{album}] - {duration} - ID: {sid}"


def print_console_report(
    result: MatchResult,
    *,
    console: Console | None = None,
    spotify_skipped_null: int = 0,
    spotify_skipped_local: int = 0,
) -> None:
    """Pretty-print the four buckets as ``rich`` tables."""
    console = console or Console()

    summary = Table(title="Playlist diff summary", show_header=True, header_style="bold")
    summary.add_column("Bucket")
    summary.add_column("Count", justify="right")
    summary.add_row("Matched", str(len(result.matched)))
    summary.add_row("Only in Spotify", str(len(result.only_spotify)))
    summary.add_row("Only in Apple Music", str(len(result.only_apple)))
    summary.add_row("Ambiguous", str(len(result.ambiguous)))
    console.print(summary)

    if spotify_skipped_null or spotify_skipped_local:
        console.print(
            f"[dim]Spotify skipped: {spotify_skipped_null} unavailable, "
            f"{spotify_skipped_local} local files[/dim]"
        )

    _print_track_table(console, "Only in Spotify", result.only_spotify)
    _print_track_table(console, "Only in Apple Music", result.only_apple)
    _print_matched_table(console, result.matched)
    _print_ambiguous_table(console, result.ambiguous)


def _print_track_table(console: Console, title: str, tracks: Iterable[Track]) -> None:
    tracks = list(tracks)
    if not tracks:
        return
    table = Table(title=f"{title} ({len(tracks)})", show_header=True, header_style="bold")
    table.add_column("Title")
    table.add_column("Artist(s)")
    table.add_column("Album", style="dim")
    table.add_column("Duration", justify="right")
    for t in tracks:
        table.add_row(
            t.title,
            ", ".join(t.artists),
            t.album or "",
            _fmt_duration(t.duration_ms),
        )
    console.print(table)


def _print_matched_table(console: Console, matched: Iterable[MatchedPair]) -> None:
    matched = list(matched)
    if not matched:
        return
    table = Table(
        title=f"Matched ({len(matched)})",
        show_header=True,
        header_style="bold",
    )
    table.add_column("Title")
    table.add_column("Artist(s)")
    table.add_column("Spotify album", style="dim")
    table.add_column("Apple album", style="dim")
    for pair in matched:
        # Prefer Apple's title for display; both should be semantically equivalent.
        table.add_row(
            pair.apple.title,
            ", ".join(pair.apple.artists or pair.spotify.artists),
            pair.spotify.album or "",
            pair.apple.album or "",
        )
    console.print(table)


def _print_ambiguous_table(console: Console, ambiguous: Iterable[AmbiguousCase]) -> None:
    ambiguous = list(ambiguous)
    if not ambiguous:
        return
    table = Table(
        title=f"Ambiguous ({len(ambiguous)})",
        show_header=True,
        header_style="bold yellow",
    )
    table.add_column("Apple track")
    table.add_column("Spotify candidates")
    for case in ambiguous:
        apple_cell = (
            f"{case.apple.title}\n"
            f"[dim]{', '.join(case.apple.artists)} "
            f"| {_fmt_duration(case.apple.duration_ms)}[/dim]"
        )
        candidates_cell = "\n".join(
            f"- {c.title} [dim]| {', '.join(c.artists)} "
            f"| {_fmt_duration(c.duration_ms)}[/dim]"
            for c in case.candidates
        )
        table.add_row(apple_cell, candidates_cell)
    console.print(table)


def write_output(result: MatchResult, path: Path, fmt: str) -> None:
    """Write the full diff to ``path`` in ``fmt`` (``csv`` or ``json``)."""
    fmt = fmt.lower()
    if fmt not in _OUTPUT_FORMATS:
        raise ValueError(f"Unsupported output format {fmt!r}; expected one of {_OUTPUT_FORMATS}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "csv":
        with path.open("w", newline="", encoding="utf-8") as f:
            write_csv_to(result, f)
    else:
        path.write_text(_render_json(result), encoding="utf-8")


def write_csv_to(result: MatchResult, stream) -> None:
    """Write the diff CSV to any text-mode writable stream.

    Extracted so the web download endpoint can stream into an in-memory
    ``io.StringIO`` without touching the filesystem.
    """
    fieldnames = [
        "bucket",
        "apple_title",
        "apple_artists",
        "apple_album",
        "apple_duration",
        "apple_source_id",
        "apple_isrc",
        "spotify_title",
        "spotify_artists",
        "spotify_album",
        "spotify_duration",
        "spotify_source_id",
        "spotify_isrc",
        "ambiguous_candidates",
    ]
    w = csv.DictWriter(stream, fieldnames=fieldnames)
    w.writeheader()

    for pair in result.matched:
        w.writerow(
            {
                "bucket": "matched",
                **_csv_apple(pair.apple),
                **_csv_spotify(pair.spotify),
                "ambiguous_candidates": "",
            }
        )
    for t in result.only_spotify:
        w.writerow(
            {
                "bucket": "only_spotify",
                **_csv_apple(None),
                **_csv_spotify(t),
                "ambiguous_candidates": "",
            }
        )
    for t in result.only_apple:
        w.writerow(
            {
                "bucket": "only_apple",
                **_csv_apple(t),
                **_csv_spotify(None),
                "ambiguous_candidates": "",
            }
        )
    for case in result.ambiguous:
        cand_str = " | ".join(_format_candidate(c) for c in case.candidates)
        w.writerow(
            {
                "bucket": "ambiguous",
                **_csv_apple(case.apple),
                **_csv_spotify(None),
                "ambiguous_candidates": cand_str,
            }
        )


def _csv_apple(t: Track | None) -> dict[str, str]:
    if t is None:
        return {
            "apple_title": "",
            "apple_artists": "",
            "apple_album": "",
            "apple_duration": "",
            "apple_source_id": "",
            "apple_isrc": "",
        }
    return {
        "apple_title": _s(t.title),
        "apple_artists": ", ".join(t.artists),
        "apple_album": _s(t.album),
        "apple_duration": _fmt_duration(t.duration_ms),
        "apple_source_id": _s(t.source_id),
        "apple_isrc": _s(t.isrc),
    }


def _csv_spotify(t: Track | None) -> dict[str, str]:
    if t is None:
        return {
            "spotify_title": "",
            "spotify_artists": "",
            "spotify_album": "",
            "spotify_duration": "",
            "spotify_source_id": "",
            "spotify_isrc": "",
        }
    return {
        "spotify_title": _s(t.title),
        "spotify_artists": ", ".join(t.artists),
        "spotify_album": _s(t.album),
        "spotify_duration": _fmt_duration(t.duration_ms),
        "spotify_source_id": _s(t.source_id),
        "spotify_isrc": _s(t.isrc),
    }


def _render_json(result: MatchResult) -> str:
    payload = {
        "matched": [
            {"apple": _track_row(p.apple), "spotify": _track_row(p.spotify)}
            for p in result.matched
        ],
        "only_spotify": [_track_row(t) for t in result.only_spotify],
        "only_apple": [_track_row(t) for t in result.only_apple],
        "ambiguous": [
            {
                "apple": _track_row(c.apple),
                "candidates": [_track_row(t) for t in c.candidates],
            }
            for c in result.ambiguous
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)
