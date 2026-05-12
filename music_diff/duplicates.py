"""Detect duplicates *within* a single playlist.

Two independent passes are reported because they have different confidence:

* ``by_isrc`` - high confidence. Two tracks sharing the same International
  Standard Recording Code are guaranteed to be the same recording.
* ``by_title_artist`` - heuristic. Two tracks whose normalized titles match
  AND whose normalized artist sets are identical. Catches the common case
  where the same recording was added twice but the two entries lack ISRCs
  (or have different ISRCs because of distinct masters).

Tracks that appear in ``by_isrc`` are NOT excluded from
``by_title_artist``; both views are independently useful and the user
decides which signal to act on.

This module is pure: no I/O, no logging side effects.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from .models import Track
from .normalize import normalize_artists, normalize_title


@dataclass(frozen=True, slots=True)
class DuplicateGroup:
    """Two or more tracks the detector believes are the same recording."""

    key: str
    tracks: tuple[Track, ...]


@dataclass(slots=True)
class DuplicateReport:
    by_isrc: list[DuplicateGroup] = field(default_factory=list)
    by_title_artist: list[DuplicateGroup] = field(default_factory=list)

    @property
    def total_groups(self) -> int:
        return len(self.by_isrc) + len(self.by_title_artist)


def find_duplicates(tracks: Iterable[Track]) -> DuplicateReport:
    """Return the duplicate report for one playlist."""
    track_list = list(tracks)
    return DuplicateReport(
        by_isrc=_group_by_isrc(track_list),
        by_title_artist=_group_by_title_artist(track_list),
    )


def _group_by_isrc(tracks: list[Track]) -> list[DuplicateGroup]:
    buckets: dict[str, list[Track]] = defaultdict(list)
    for t in tracks:
        if t.isrc:
            buckets[t.isrc.upper()].append(t)
    groups: list[DuplicateGroup] = []
    for isrc, members in buckets.items():
        if len(members) >= 2:
            groups.append(DuplicateGroup(key=isrc, tracks=tuple(members)))
    return groups


def _group_by_title_artist(tracks: list[Track]) -> list[DuplicateGroup]:
    buckets: dict[tuple[str, frozenset[str]], list[Track]] = defaultdict(list)
    for t in tracks:
        nt = normalize_title(t.title)
        if not nt:
            continue
        key = (nt, normalize_artists(t.artists))
        buckets[key].append(t)
    groups: list[DuplicateGroup] = []
    for (nt, artists), members in buckets.items():
        if len(members) >= 2:
            display_artists = ", ".join(sorted(artists)) if artists else "(unknown)"
            groups.append(
                DuplicateGroup(
                    key=f"{nt} :: {display_artists}",
                    tracks=tuple(members),
                )
            )
    return groups
