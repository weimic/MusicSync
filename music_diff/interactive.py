"""Terminal-driven resolver for the ambiguous bucket.

Kept out of ``matcher.py`` so the diff stays pure / deterministic /
trivially testable. The CLI calls ``resolve_ambiguous`` only when the user
passes ``--interactive``.
"""

from __future__ import annotations

from typing import Callable

from rich.console import Console

from .matcher import AmbiguousCase, MatchResult, promote_to_match
from .models import Track

# `input` is injected to keep this unit-testable without monkeypatching builtins.
InputFn = Callable[[str], str]


def _fmt_duration(ms: int | None) -> str:
    if ms is None:
        return "?:??"
    seconds = ms // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


def _describe(t: Track) -> str:
    return (
        f"{t.title}  -  {', '.join(t.artists) or '(unknown)'}  "
        f"[{_fmt_duration(t.duration_ms)}]  album: {t.album or '-'}"
    )


def resolve_ambiguous(
    result: MatchResult,
    *,
    console: Console | None = None,
    input_fn: InputFn = input,
) -> None:
    """Walk every ambiguous case and let the operator pick a winner (or skip).

    The result is mutated in place. Skipped cases remain in ``result.ambiguous``.
    """
    console = console or Console()
    if not result.ambiguous:
        return

    console.print(
        f"\n[bold]{len(result.ambiguous)} ambiguous case(s)[/bold]. "
        "For each, type a candidate number, [bold]s[/bold] to skip, or "
        "[bold]q[/bold] to stop resolving.\n"
    )

    # Iterate over a snapshot since ``promote_to_match`` mutates ``ambiguous``.
    for case in list(result.ambiguous):
        action = _prompt_one(case, console, input_fn)
        if action == "quit":
            break
        if action == "skip" or action is None:
            continue
        chosen = case.candidates[action]
        promote_to_match(result, case, chosen)


def _prompt_one(
    case: AmbiguousCase, console: Console, input_fn: InputFn
) -> int | str | None:
    """Return the index chosen by the user, ``"skip"``, ``"quit"``, or None."""
    console.print(f"[bold]Apple:[/bold]   {_describe(case.apple)}")
    for i, c in enumerate(case.candidates, start=1):
        console.print(f"  [bold]{i}.[/bold] {_describe(c)}")

    while True:
        try:
            raw = input_fn("Pick [1-{n}/s/q]: ".format(n=len(case.candidates))).strip()
        except EOFError:
            return "quit"
        if not raw:
            continue
        low = raw.lower()
        if low in {"q", "quit", "exit"}:
            return "quit"
        if low in {"s", "skip", "n", "no"}:
            return "skip"
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(case.candidates):
                return idx
        console.print("[red]Unrecognized input.[/red]")
