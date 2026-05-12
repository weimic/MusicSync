"""In-memory store for completed diffs.

Keyed by uuid, with a soft cap and a TTL so a long-running process can't
blow up. Local-only, single-user; restart wipes all entries. The plan
explicitly trades persistence for simplicity here.
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from music_diff.duplicates import DuplicateReport
from music_diff.matcher import MatchResult


_DEFAULT_MAX_ENTRIES = 16
_DEFAULT_TTL = timedelta(hours=1)


@dataclass(slots=True)
class StoredDiff:
    """A computed diff plus the metadata needed to render its result page."""

    spotify_filename: str
    apple_url: str
    spotify_track_count: int
    apple_track_count: int
    result: MatchResult
    spotify_dupes: DuplicateReport
    apple_dupes: DuplicateReport
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class DiffStore:
    """Thread-safe LRU+TTL dict from uuid string -> StoredDiff."""

    def __init__(
        self,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        ttl: timedelta = _DEFAULT_TTL,
    ) -> None:
        self._max = max_entries
        self._ttl = ttl
        self._items: OrderedDict[str, StoredDiff] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, diff: StoredDiff) -> str:
        """Insert ``diff`` and return its newly-assigned uuid."""
        diff_id = uuid.uuid4().hex
        with self._lock:
            self._evict_expired_locked()
            self._items[diff_id] = diff
            while len(self._items) > self._max:
                self._items.popitem(last=False)
        return diff_id

    def get(self, diff_id: str) -> StoredDiff | None:
        with self._lock:
            self._evict_expired_locked()
            stored = self._items.get(diff_id)
            if stored is not None:
                # LRU touch: most recently accessed moves to the end.
                self._items.move_to_end(diff_id)
            return stored

    def _evict_expired_locked(self) -> None:
        cutoff = datetime.now(timezone.utc) - self._ttl
        for key in [k for k, v in self._items.items() if v.created_at < cutoff]:
            del self._items[key]
