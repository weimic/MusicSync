"""End-to-end-ish tests for the Flask web UI.

We monkeypatch ``music_diff.apple_client.fetch_playlist_tracks`` so the test
does no network. The Spotify side reads a real fixture CSV through the
production ``read_exportify_csv_text`` path.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Iterator

import pytest

from music_diff.models import Track
from webapp import create_app
from webapp import routes as webapp_routes

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator:
    app = create_app()
    app.config["TESTING"] = True

    def fake_fetch(url: str, *_, **__) -> list[Track]:
        # Two tracks: one matches the first row of the Exportify fixture by
        # title+artist; one is Apple-only. Lets us assert all three buckets
        # show up in the result page.
        return [
            Track(
                title="Bad Blood",
                artists=("Taylor Swift", "Kendrick Lamar"),
                album="1989",
                duration_ms=211000,
                source="apple",
                source_id="1",
                isrc="USCJY1431530",
            ),
            Track(
                title="Apple Only",
                artists=("Some Artist",),
                album="Some Album",
                duration_ms=120000,
                source="apple",
                source_id="2",
                isrc=None,
            ),
        ]

    monkeypatch.setattr(webapp_routes, "fetch_playlist_tracks", fake_fetch)

    with app.test_client() as c:
        yield c


class TestIndex:
    def test_get_renders_form(self, client) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Compare your playlists" in body
        assert 'name="apple_url"' in body
        assert 'name="spotify_csv"' in body


class TestPostDiff:
    def _csv_payload(self) -> dict[str, object]:
        csv_bytes = (FIXTURES / "exportify_sample.csv").read_bytes()
        return {
            "apple_url": "https://music.apple.com/us/playlist/test/pl.test",
            "spotify_csv": (io.BytesIO(csv_bytes), "exportify_sample.csv"),
        }

    def test_happy_path_redirects_to_result(self, client) -> None:
        resp = client.post("/diff", data=self._csv_payload(),
                           content_type="multipart/form-data")
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/result/")

    def test_result_page_shows_buckets_and_counts(self, client) -> None:
        post = client.post("/diff", data=self._csv_payload(),
                           content_type="multipart/form-data",
                           follow_redirects=False)
        result_url = post.headers["Location"]
        resp = client.get(result_url)
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        # 1 matched (Bad Blood via ISRC), 2 only-spotify, 1 only-apple, 0 ambiguous.
        assert "Matched" in body
        assert "Only in Spotify" in body
        assert "Only in Apple Music" in body
        assert "Ambiguous" in body
        assert "Duplicates within your Spotify playlist" in body
        assert "Duplicates within your Apple Music playlist" in body
        assert "Apple Only" in body  # the apple-only track shows up
        assert "Bad Blood" in body  # matched

    def test_csv_download_returns_csv(self, client) -> None:
        post = client.post("/diff", data=self._csv_payload(),
                           content_type="multipart/form-data")
        diff_id = post.headers["Location"].rsplit("/", 1)[-1]
        resp = client.get(f"/result/{diff_id}/diff.csv")
        assert resp.status_code == 200
        assert resp.mimetype == "text/csv"
        body = resp.get_data(as_text=True)
        first_line = body.splitlines()[0]
        assert "bucket" in first_line
        assert "apple_isrc" in first_line
        assert "spotify_isrc" in first_line


class TestValidationErrors:
    def test_missing_url_renders_400_with_error(self, client) -> None:
        csv_bytes = (FIXTURES / "exportify_sample.csv").read_bytes()
        resp = client.post(
            "/diff",
            data={
                "apple_url": "",
                "spotify_csv": (io.BytesIO(csv_bytes), "x.csv"),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert "Apple Music shared playlist URL" in resp.get_data(as_text=True)

    def test_missing_csv_renders_400_with_error(self, client) -> None:
        resp = client.post(
            "/diff",
            data={"apple_url": "https://music.apple.com/us/playlist/x/pl.x"},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert "Exportify CSV" in resp.get_data(as_text=True)


class TestUnknownDiffId:
    def test_result_404(self, client) -> None:
        assert client.get("/result/does-not-exist").status_code == 404

    def test_csv_404(self, client) -> None:
        assert client.get("/result/does-not-exist/diff.csv").status_code == 404
