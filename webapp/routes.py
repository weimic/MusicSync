"""HTTP routes for the MusicSync web UI.

The diff pipeline is invoked synchronously inside ``POST /diff``. For the
tool's local-personal-use scope this is appropriate: total wall clock for a
1000-track diff is ~5-15 seconds (mostly Apple amp-api pagination), and a
job queue would add accidental complexity without a real benefit.
"""

from __future__ import annotations

import io
import logging

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    url_for,
)

from music_diff.apple_client import AppleMusicError, fetch_playlist_tracks
from music_diff.duplicates import find_duplicates
from music_diff.matcher import diff
from music_diff.report import write_csv_to
from music_diff.spotify_csv import SpotifyCsvError, read_exportify_csv_text

from .store import DiffStore, StoredDiff

log = logging.getLogger(__name__)

bp = Blueprint("main", __name__)


def _store() -> DiffStore:
    return current_app.config["DIFF_STORE"]


@bp.get("/")
def index():
    """Render the upload form."""
    return render_template("index.html")


@bp.post("/diff")
def run_diff():
    """Validate inputs, run the full pipeline, store the result, redirect."""
    apple_url = (request.form.get("apple_url") or "").strip()
    spotify_file = request.files.get("spotify_csv")

    errors: list[str] = []
    if not apple_url:
        errors.append("Please paste an Apple Music shared playlist URL.")
    if spotify_file is None or not spotify_file.filename:
        errors.append("Please choose your Spotify Exportify CSV file.")
    if errors:
        return render_template("index.html", errors=errors,
                               apple_url=apple_url), 400

    try:
        csv_bytes = spotify_file.read()
        spotify_tracks = read_exportify_csv_text(csv_bytes.decode("utf-8-sig"))
    except UnicodeDecodeError:
        return render_template(
            "index.html",
            errors=["The CSV file isn't valid UTF-8. Re-export it from Exportify and try again."],
            apple_url=apple_url,
        ), 400
    except SpotifyCsvError as exc:
        return render_template(
            "index.html",
            errors=[f"Could not read the Spotify CSV: {exc}"],
            apple_url=apple_url,
        ), 400

    try:
        apple_tracks = fetch_playlist_tracks(apple_url)
    except AppleMusicError as exc:
        return render_template(
            "index.html",
            errors=[f"Could not fetch the Apple Music playlist: {exc}"],
            apple_url=apple_url,
        ), 400

    log.info(
        "Computing diff: %d Spotify tracks vs %d Apple tracks",
        len(spotify_tracks),
        len(apple_tracks),
    )
    result = diff(spotify_tracks, apple_tracks)
    spot_dupes = find_duplicates(spotify_tracks)
    apple_dupes = find_duplicates(apple_tracks)

    diff_id = _store().put(
        StoredDiff(
            spotify_filename=spotify_file.filename,
            apple_url=apple_url,
            spotify_track_count=len(spotify_tracks),
            apple_track_count=len(apple_tracks),
            result=result,
            spotify_dupes=spot_dupes,
            apple_dupes=apple_dupes,
        )
    )
    return redirect(url_for("main.result", diff_id=diff_id))


@bp.get("/result/<diff_id>")
def result(diff_id: str):
    """Render the tabbed result page for a previously computed diff."""
    stored = _store().get(diff_id)
    if stored is None:
        abort(404)
    return render_template(
        "result.html",
        diff_id=diff_id,
        stored=stored,
        result=stored.result,
        spotify_dupes=stored.spotify_dupes,
        apple_dupes=stored.apple_dupes,
    )


@bp.get("/result/<diff_id>/diff.csv")
def download_csv(diff_id: str):
    """Stream the CSV form of a stored diff."""
    stored = _store().get(diff_id)
    if stored is None:
        abort(404)
    buf = io.StringIO()
    write_csv_to(stored.result, buf)
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="diff.csv"',
        },
    )
