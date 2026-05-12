"""``python -m music_diff.cli`` entry point.

The Spotify side accepts one of two inputs, exactly one required:

* ``--spotify-csv path.csv`` - an Exportify CSV (works on free Spotify accounts).
* ``--spotify-url <playlist URL or id>`` - hits the Web API directly. Spotify
  now requires the developer app's owner to have a Premium subscription, so
  this path will fail for free-tier accounts.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

from . import __version__
from .apple_client import AppleMusicError, fetch_playlist_tracks as fetch_apple
from .interactive import resolve_ambiguous
from .matcher import diff
from .models import Track
from .report import print_console_report, write_output
from .spotify_client import FetchStats, SpotifyClient, SpotifyError
from .spotify_csv import SpotifyCsvError, read_exportify_csv

log = logging.getLogger("music_diff")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="music_diff",
        description="Diff a Spotify playlist against a shared Apple Music playlist.",
    )

    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--spotify-csv",
        type=Path,
        default=None,
        help="Path to an Exportify CSV (https://exportify.app). Recommended.",
    )
    src.add_argument(
        "--spotify-url",
        default=None,
        help=(
            "Public Spotify playlist URL or 22-char ID. Requires a Premium "
            "Spotify account on the developer app's owner."
        ),
    )

    p.add_argument("--apple-url", required=True, help="Shared Apple Music playlist URL.")
    p.add_argument(
        "--fuzzy-threshold",
        type=int,
        default=90,
        help="rapidfuzz WRatio cutoff (0-100). Default: 90.",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="If set, write the full diff to this file.",
    )
    p.add_argument(
        "--format",
        choices=("csv", "json"),
        default="csv",
        help="Output file format. Default: csv.",
    )
    p.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt to resolve each ambiguous case in the terminal.",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")
    p.add_argument("--version", action="version", version=f"music_diff {__version__}")
    return p


def _force_utf8_stdio() -> None:
    """Reconfigure stdout/stderr to UTF-8 on platforms (Windows console) that
    default to a legacy code page like cp1252.

    Without this, printing a track title containing emoji or non-Latin
    characters via ``rich`` raises ``UnicodeEncodeError`` and tears down the
    process *before* the CSV is written. Errors policy is ``replace`` so a
    truly unencodable byte is shown as ``?`` rather than crashing.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover - best effort, never fatal
            pass


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    _force_utf8_stdio()
    console = Console()

    if not 0 <= args.fuzzy_threshold <= 100:
        console.print("[red]--fuzzy-threshold must be between 0 and 100[/red]")
        return 2

    try:
        spotify_tracks, stats = _load_spotify(args, console)
        console.print("[dim]Fetching Apple Music playlist...[/dim]")
        apple_tracks = fetch_apple(args.apple_url)
        console.print(f"[dim]Apple Music: {len(apple_tracks)} tracks loaded.[/dim]")
    except SpotifyError as exc:
        console.print(f"[red]Spotify error:[/red] {exc}")
        return 1
    except SpotifyCsvError as exc:
        console.print(f"[red]Spotify CSV error:[/red] {exc}")
        return 1
    except AppleMusicError as exc:
        console.print(f"[red]Apple Music error:[/red] {exc}")
        return 1

    result = diff(spotify_tracks, apple_tracks, fuzzy_threshold=args.fuzzy_threshold)

    if args.interactive:
        resolve_ambiguous(result, console=console)

    # Persist the diff BEFORE the console report so a print-time crash (e.g.
    # rare encoding failure on a non-UTF-8 console) cannot cause the user
    # to lose the result of a long network fetch.
    if args.out is not None:
        write_output(result, args.out, args.format)
        console.print(f"[green]Wrote diff to {args.out} ({args.format}).[/green]")

    print_console_report(
        result,
        console=console,
        spotify_skipped_null=stats.skipped_null,
        spotify_skipped_local=stats.skipped_local,
    )

    return 0


def _load_spotify(
    args: argparse.Namespace, console: Console
) -> tuple[list[Track], FetchStats]:
    """Resolve the Spotify input source and return tracks + skip stats."""
    if args.spotify_csv is not None:
        console.print(f"[dim]Reading Spotify playlist from {args.spotify_csv}...[/dim]")
        tracks = read_exportify_csv(args.spotify_csv)
        console.print(f"[dim]Spotify: {len(tracks)} tracks loaded from CSV.[/dim]")
        return tracks, FetchStats()

    load_dotenv()
    client_id = os.environ.get("SPOTIFY_CLIENT_ID", "")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
    spotify = SpotifyClient(client_id=client_id, client_secret=client_secret)
    console.print("[dim]Fetching Spotify playlist via API...[/dim]")
    tracks, stats = spotify.fetch_playlist_tracks(args.spotify_url)
    console.print(f"[dim]Spotify: {len(tracks)} tracks loaded.[/dim]")
    return tracks, stats


if __name__ == "__main__":
    sys.exit(main())
