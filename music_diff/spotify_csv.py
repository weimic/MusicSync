"""Read tracks from an Exportify CSV.

Exportify (https://exportify.app) lets any Spotify user - including free
accounts - export a playlist to CSV. Its column names have been stable for
years, but to stay resilient against forks/renames we look up each field by
a small set of accepted aliases.

The canonical Exportify columns we care about are:

* ``Track URI``         (e.g. ``spotify:track:6habFhsOp2NvshLv26DqMb``)
* ``Track Name``
* ``Artist Name(s)``    (comma-separated when multiple)
* ``Album Name``
* ``Track Duration (ms)``
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from .models import Track
from .normalize import split_artist_string


class SpotifyCsvError(RuntimeError):
    """Raised when the CSV can't be read or required columns are missing."""


_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("Track Name", "Name"),
    "artists": ("Artist Name(s)", "Artist Names", "Artists", "Artist"),
    "album": ("Album Name", "Album"),
    "duration_ms": ("Track Duration (ms)", "Duration (ms)", "Duration"),
    "uri": ("Track URI", "URI", "Spotify URI"),
    "isrc": ("ISRC", "Isrc", "International Standard Recording Code"),
}

_TRACK_URI_RE = re.compile(r"^spotify:track:([A-Za-z0-9]+)$")


def read_exportify_csv(path: str | Path) -> list[Track]:
    """Parse an Exportify CSV into ``Track`` objects.

    Raises ``SpotifyCsvError`` on missing required columns or unreadable file.
    Empty or malformed rows are silently skipped so a single bad row doesn't
    break a 200-track playlist diff.
    """
    p = Path(path)
    if not p.is_file():
        raise SpotifyCsvError(f"Spotify CSV not found: {p}")

    try:
        with p.open("r", encoding="utf-8-sig", newline="") as f:
            return _read_csv_stream(f)
    except (OSError, UnicodeDecodeError) as exc:
        raise SpotifyCsvError(f"Could not read {p}: {exc}") from exc


def read_exportify_csv_text(text: str) -> list[Track]:
    """Parse an Exportify CSV that has already been read into a string.

    Used by the web app, which receives the upload as bytes and decodes
    once at the boundary so all encoding errors surface in one place.
    """
    return _read_csv_stream(io.StringIO(text))


def _read_csv_stream(stream: "io.TextIOBase") -> list[Track]:
    reader = csv.DictReader(stream)
    fieldnames = reader.fieldnames or []
    column_map = _resolve_columns(fieldnames)
    tracks: list[Track] = []
    for row in reader:
        track = _row_to_track(row, column_map)
        if track is not None:
            tracks.append(track)
    return tracks


def _resolve_columns(fieldnames: list[str]) -> dict[str, str]:
    """Map our logical field names to whichever alias is present in the CSV."""
    available = {name: name for name in fieldnames}
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for logical, aliases in _FIELD_ALIASES.items():
        chosen: str | None = None
        for alias in aliases:
            if alias in available:
                chosen = alias
                break
        if chosen is None:
            # title + artists are required; other fields are optional metadata.
            if logical in {"title", "artists"}:
                missing.append(f"{logical} (any of {aliases!r})")
        else:
            resolved[logical] = chosen
    if missing:
        raise SpotifyCsvError(
            "CSV is missing required column(s): " + "; ".join(missing)
        )
    return resolved


def _row_to_track(row: dict[str, str], cols: dict[str, str]) -> Track | None:
    title = (row.get(cols["title"]) or "").strip()
    artists_raw = (row.get(cols["artists"]) or "").strip()
    if not title or not artists_raw:
        return None

    # Exportify uses commas, but a producer/forked exporter can use ``&`` or
    # ``and``. ``split_artist_string`` handles all of them.
    artists = tuple(split_artist_string(artists_raw))

    album_col = cols.get("album")
    album = (row.get(album_col) or "").strip() if album_col else ""
    album_value: str | None = album or None

    duration_col = cols.get("duration_ms")
    duration_ms: int | None = None
    if duration_col:
        raw_duration = (row.get(duration_col) or "").strip()
        if raw_duration:
            try:
                duration_ms = int(float(raw_duration))
            except ValueError:
                duration_ms = None

    source_id: str | None = None
    uri_col = cols.get("uri")
    if uri_col:
        uri = (row.get(uri_col) or "").strip()
        m = _TRACK_URI_RE.match(uri)
        if m:
            source_id = m.group(1)
        elif uri:
            source_id = uri

    isrc: str | None = None
    isrc_col = cols.get("isrc")
    if isrc_col:
        isrc_raw = (row.get(isrc_col) or "").strip()
        if isrc_raw:
            isrc = isrc_raw.upper()

    return Track(
        title=title,
        artists=artists,
        album=album_value,
        duration_ms=duration_ms,
        source="spotify",
        source_id=source_id,
        isrc=isrc,
    )
