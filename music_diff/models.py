"""Data model shared across fetchers, matcher, and reporter.

A single immutable ``Track`` shape is used for both services. The ``album``
field is intentionally kept on the model but is **never** consumed by the
matcher (see ``music_diff.matcher``); albums diverge across services
(remasters, regional editions, singles vs. compilations) and would produce
false negatives if used as a matching signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Source = Literal["spotify", "apple"]


@dataclass(frozen=True, slots=True)
class Track:
    title: str
    artists: tuple[str, ...]
    album: str | None
    duration_ms: int | None
    source: Source
    source_id: str | None
    isrc: str | None = None
    """International Standard Recording Code; when present on both sides it
    is treated as a deterministic identity match (see ``music_diff.matcher``)."""

    @property
    def primary_artist(self) -> str:
        return self.artists[0] if self.artists else ""

    def display(self) -> str:
        artists = ", ".join(self.artists) if self.artists else "(unknown artist)"
        return f"{self.title} - {artists}"
