"""String normalization used by the matcher.

Goals:
* make titles and artists comparable across Spotify and Apple Music despite
  cosmetic differences (case, diacritics, ``feat.`` placement, remaster tags),
* be conservative: only strip things that are reliably cosmetic, never strip
  content that distinguishes one recording from another (e.g. a "Live in
  Tokyo" title is *not* the same recording as the studio version).

Anything that could distinguish two different recordings - including album
identity - is deliberately left to the matcher's tiebreaker logic, not folded
into normalization.
"""

from __future__ import annotations

import re
import unicodedata

_TAG_WORDS = (
    r"feat\.?|ft\.?|featuring|with"
    r"|remaster(?:ed)?(?:\s*\d{2,4})?"
    r"|re[-\s]?recorded(?:\s*version)?"
    r"|taylor['\u2019]?s\s+version"
    r"|deluxe(?:\s*edition)?|expanded(?:\s*edition)?|bonus\s*track"
    r"|single\s*version|album\s*version|radio\s*edit|edit"
    r"|single|ep"  # standalone trailing-suffix tags ("- Single", "- EP")
    r"|mono(?:\s*version)?|stereo(?:\s*version)?"
    r"|explicit|clean"
)

_PAREN_TAG_RE = re.compile(
    rf"\s*[\(\[\{{][^\(\)\[\]\{{\}}]*\b(?:{_TAG_WORDS})\b[^\(\)\[\]\{{\}}]*[\)\]\}}]",
    flags=re.IGNORECASE,
)

_TRAIL_DASH_TAG_RE = re.compile(
    rf"\s*-\s*\b(?:{_TAG_WORDS})\b.*$",
    flags=re.IGNORECASE,
)

_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]+")
_WS_RE = re.compile(r"\s+")
_LEADING_THE_RE = re.compile(r"^the\s+")

# Splitters used by ``split_artist_string`` for collapsing a single
# delimiter-separated artist string into individual artist names. Order
# matters only insofar as we want longer alternates ("feat.") to win over
# shorter ("&") - re.split tries alternates left-to-right but is greedy on
# match length, so we list the more specific tokens first defensively.
_ARTIST_DELIM_RE = re.compile(
    r"\s*(?:"
    r",|/|\u2022"             # commas, slashes, bullets
    r"|&|\+|\u00d7"           # ampersand, plus, multiplication sign
    # The trailing ``\.?`` for ``feat`` / ``ft`` / ``vs`` sits AFTER the word
    # boundary so an abbreviated period is consumed without requiring another
    # boundary after it (``feat.`` followed by a space has no word boundary
    # between ``.`` and `` ``).
    r"|\bfeat\b\.?|\bft\b\.?|\bfeaturing\b|\bwith\b"
    r"|\band\b|\bx\b|\bvs\b\.?"
    r")\s*",
    flags=re.IGNORECASE,
)


def _strip_diacritics(s: str) -> str:
    decomposed = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _base_clean(s: str) -> str:
    s = _strip_diacritics(s).lower()
    s = _NON_ALNUM_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def normalize_title(title: str) -> str:
    """Canonical form of a track title for matching.

    Order matters: parenthetical/bracketed tag clauses and trailing
    ``- remastered 2011``-style tails are stripped *before* punctuation is
    flattened, otherwise the regex anchors break.
    """
    if not title:
        return ""
    s = title
    while True:
        new = _PAREN_TAG_RE.sub("", s)
        if new == s:
            break
        s = new
    s = _TRAIL_DASH_TAG_RE.sub("", s)
    return _base_clean(s)


def normalize_artist(artist: str) -> str:
    """Canonical form of a single artist name for matching."""
    if not artist:
        return ""
    s = _base_clean(artist)
    s = _LEADING_THE_RE.sub("", s)
    return s


def split_artist_string(value: str) -> list[str]:
    """Split a single delimiter-separated artist string into individual names.

    Apple Music's amp-api returns multi-artist credits as one string
    (``"Surfaces & salem ilese"``); Spotify CSVs use commas
    (``"Surfaces, salem ilese"``). Both must produce the same artist set or
    the matcher's set-overlap rule misfires. This helper splits on the union
    of delimiters seen in the wild (``,`` ``&`` ``/`` ``+`` ``feat.`` ``ft.``
    ``with`` ``and`` ``x`` ``vs``) and trims each piece. Empty pieces are
    dropped. The original is returned as a single-element list when no
    delimiter matches, so single-artist strings round-trip cleanly.
    """
    if not value:
        return []
    parts = [p.strip() for p in _ARTIST_DELIM_RE.split(value)]
    return [p for p in parts if p]


def normalize_artists(artists: tuple[str, ...] | list[str]) -> frozenset[str]:
    """Set of normalized artist names; empty strings are dropped.

    Each input string is itself ``split_artist_string``-expanded first, so
    multi-artist values smuggled in as one string (Apple's
    ``"Surfaces & salem ilese"``) become two set members and intersect
    correctly with Spotify's pre-split ``("Surfaces", "salem ilese")``.

    A frozenset is returned so the matcher can use it as a dict/set element.
    """
    expanded: list[str] = []
    for item in artists:
        expanded.extend(split_artist_string(item))
    return frozenset(n for n in (normalize_artist(a) for a in expanded) if n)
