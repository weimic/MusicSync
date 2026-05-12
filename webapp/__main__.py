"""``python -m webapp`` -> launches the Flask dev server on 127.0.0.1:5000."""

from __future__ import annotations

import argparse
import logging
import sys

from . import create_app


def _force_utf8_stdio() -> None:
    """Same workaround as the CLI: avoid Windows cp1252 crashes when log
    lines contain non-ASCII track titles."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover - best effort
            pass


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="webapp",
        description="Run the MusicSync web UI locally.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=5000, help="Bind port. Default: 5000")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode (auto-reload).")
    args = parser.parse_args()

    _force_utf8_stdio()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    app = create_app()
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
