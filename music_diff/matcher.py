"""Diff two playlists into matched / only-A / only-B / ambiguous buckets.

Matching contract (see README for the full rationale):

* **ISRC pre-pass.** When both an Apple and a Spotify track expose the same
  International Standard Recording Code, that is treated as a deterministic
  identity match and consumes both tracks before any heuristic runs. ISRCs
  uniquely identify a recording across services; this is the only signal we
  trust unconditionally.
* **Heuristic pass** for everything ISRC didn't resolve:
  - Normalized title (exact, then fuzzy via rapidfuzz),
  - Normalized artist sets must intersect,
  - duration_ms used only as a tiebreaker (within 3000 ms).
* **Album is never a signal.** It is preserved on the ``Track`` model only so
  the report can show it.
* When multiple Spotify candidates survive the heuristic pass and duration
  can't pick exactly one, we refuse to silently guess and surface the case
  as ``ambiguous``.

This module is intentionally pure: no I/O, no prompts. Interactive
resolution lives in ``music_diff.interactive``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from rapidfuzz import fuzz

from .models import Track
from .normalize import normalize_artists, normalize_title

DEFAULT_FUZZY_THRESHOLD = 90
"""rapidfuzz.WRatio cutoff (0-100); >= is a match candidate."""

DURATION_TIEBREAKER_MS = 3000
"""Apple/Spotify duration agreement window when breaking candidate ties."""


@dataclass(frozen=True, slots=True)
class MatchedPair:
    apple: Track
    spotify: Track


@dataclass(frozen=True, slots=True)
class AmbiguousCase:
    """An Apple track for which the matcher refuses to silently pick a winner."""

    apple: Track
    candidates: tuple[Track, ...]


@dataclass(slots=True)
class MatchResult:
    matched: list[MatchedPair] = field(default_factory=list)
    only_spotify: list[Track] = field(default_factory=list)
    only_apple: list[Track] = field(default_factory=list)
    ambiguous: list[AmbiguousCase] = field(default_factory=list)


def diff(
    spotify_tracks: Iterable[Track],
    apple_tracks: Iterable[Track],
    *,
    fuzzy_threshold: int = DEFAULT_FUZZY_THRESHOLD,
) -> MatchResult:
    """Diff two playlists. See module docstring for the matching contract."""
    spotify_list = list(spotify_tracks)
    apple_list = list(apple_tracks)

    # Pre-compute normalization once per Spotify track so we don't redo work
    # inside the fuzzy loop. Order is preserved so that ties prefer earlier
    # entries deterministically.
    spotify_index: dict[str, list[int]] = {}
    spotify_norm_titles: list[str] = []
    spotify_artist_sets: list[frozenset[str]] = []
    spotify_isrc_index: dict[str, int] = {}
    for i, t in enumerate(spotify_list):
        nt = normalize_title(t.title)
        spotify_norm_titles.append(nt)
        spotify_artist_sets.append(normalize_artists(t.artists))
        spotify_index.setdefault(nt, []).append(i)
        if t.isrc:
            # First write wins on duplicates; the heuristic pass will still
            # pick up the loser via title/artist if it is a real match.
            spotify_isrc_index.setdefault(t.isrc.upper(), i)

    consumed: set[int] = set()
    result = MatchResult()
    apples_to_resolve: list[Track] = []

    # ---- ISRC pre-pass: deterministic, no heuristics. ----
    for apple in apple_list:
        if apple.isrc:
            spot_idx = spotify_isrc_index.get(apple.isrc.upper())
            if spot_idx is not None and spot_idx not in consumed:
                consumed.add(spot_idx)
                result.matched.append(
                    MatchedPair(apple=apple, spotify=spotify_list[spot_idx])
                )
                continue
        apples_to_resolve.append(apple)

    for apple in apples_to_resolve:
        a_title = normalize_title(apple.title)
        a_artists = normalize_artists(apple.artists)
        candidate_idx = _find_candidates(
            a_title=a_title,
            a_artists=a_artists,
            spotify_index=spotify_index,
            spotify_norm_titles=spotify_norm_titles,
            spotify_artist_sets=spotify_artist_sets,
            consumed=consumed,
            fuzzy_threshold=fuzzy_threshold,
        )

        if not candidate_idx:
            result.only_apple.append(apple)
            continue

        if len(candidate_idx) == 1:
            idx = candidate_idx[0]
            consumed.add(idx)
            result.matched.append(MatchedPair(apple=apple, spotify=spotify_list[idx]))
            continue

        # >1 candidates: try duration to break the tie.
        winner = _pick_by_duration(apple, candidate_idx, spotify_list)
        if winner is not None:
            consumed.add(winner)
            result.matched.append(MatchedPair(apple=apple, spotify=spotify_list[winner]))
        else:
            result.ambiguous.append(
                AmbiguousCase(
                    apple=apple,
                    candidates=tuple(spotify_list[i] for i in candidate_idx),
                )
            )

    for i, t in enumerate(spotify_list):
        if i not in consumed:
            result.only_spotify.append(t)

    return result


def _find_candidates(
    *,
    a_title: str,
    a_artists: frozenset[str],
    spotify_index: dict[str, list[int]],
    spotify_norm_titles: list[str],
    spotify_artist_sets: list[frozenset[str]],
    consumed: set[int],
    fuzzy_threshold: int,
) -> list[int]:
    """Return indices of viable Spotify matches for one Apple track.

    The exact-title pass runs first; only if it produces zero candidates do we
    fall back to a fuzzy scan, which is O(N) in the Spotify playlist size.
    """
    exact = [i for i in spotify_index.get(a_title, ()) if i not in consumed]
    exact = [i for i in exact if _artists_compatible(a_artists, spotify_artist_sets[i])]
    if exact:
        return exact

    if not a_title:
        return []

    fuzzy: list[int] = []
    for i, nt in enumerate(spotify_norm_titles):
        if i in consumed or not nt:
            continue
        if not _artists_compatible(a_artists, spotify_artist_sets[i]):
            continue
        if fuzz.WRatio(a_title, nt) >= fuzzy_threshold:
            fuzzy.append(i)
    return fuzzy


def _artists_compatible(a: frozenset[str], b: frozenset[str]) -> bool:
    """Two artist sets are compatible if they intersect.

    If *either* side is empty (e.g. a metadata gap), we treat the artist
    signal as inconclusive and let the title carry the match. That is a
    pragmatic concession to ragged Apple data, not a license to over-match:
    title equality / fuzziness still has to hold.
    """
    if not a or not b:
        return True
    return bool(a & b)


def _pick_by_duration(
    apple: Track, candidate_idx: list[int], spotify_list: list[Track]
) -> int | None:
    """Return the single candidate within the duration window, else None."""
    if apple.duration_ms is None:
        return None
    within: list[int] = []
    for i in candidate_idx:
        sd = spotify_list[i].duration_ms
        if sd is None:
            continue
        if abs(sd - apple.duration_ms) <= DURATION_TIEBREAKER_MS:
            within.append(i)
    if len(within) == 1:
        return within[0]
    return None


def promote_to_match(result: MatchResult, case: AmbiguousCase, chosen: Track) -> None:
    """Mutate ``result`` to promote one ambiguous case to a matched pair.

    Used by the interactive resolver. ``chosen`` must be one of the case's
    candidates and must not already be present in ``result.only_spotify`` for a
    different reason; we still remove it defensively.
    """
    try:
        result.ambiguous.remove(case)
    except ValueError as exc:  # pragma: no cover - programmer error
        raise ValueError("case is not part of this MatchResult") from exc
    if chosen not in case.candidates:
        raise ValueError("chosen track is not one of the case's candidates")
    # `chosen` may also appear in only_spotify if the same candidate showed up
    # in multiple ambiguous cases; remove the first such occurrence.
    if chosen in result.only_spotify:
        result.only_spotify.remove(chosen)
    result.matched.append(MatchedPair(apple=case.apple, spotify=chosen))
