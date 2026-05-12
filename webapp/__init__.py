"""MusicScraper Flask web UI.

A thin wrapper around the existing ``music_diff`` package: same fetchers,
matcher, and report writers - just behind an HTML form instead of an
argparse CLI. Intended to be run locally via ``python -m webapp``.
"""

from __future__ import annotations

from flask import Flask

from .store import DiffStore


def create_app() -> Flask:
    """Application factory.

    Kept side-effect free so tests can call it repeatedly with their own
    config and isolated stores.
    """
    app = Flask(
        __name__,
        static_folder="static",
        template_folder="templates",
    )
    # 16 MB upload cap. Exportify CSVs for ~10k-track libraries weigh ~3 MB,
    # so this is generous; mainly there to refuse a malicious huge file.
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
    app.config["DIFF_STORE"] = DiffStore()

    from . import routes

    app.register_blueprint(routes.bp)
    return app
