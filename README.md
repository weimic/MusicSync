# MusicScraper

A small Python CLI that compares a **Spotify playlist** with a **shared
Apple Music playlist** and shows you the mismatches: which tracks live in one
service but not the other, plus any cases the tool refuses to silently guess
("ambiguous").

The Spotify side has two input modes; you only need one:

- **`--spotify-csv path.csv`** (recommended): a CSV exported with
  [Exportify](https://exportify.app). Works on **free** Spotify accounts and
  doesn't require any developer setup.
- **`--spotify-url <url>`**: the Spotify Web API (Client Credentials flow).
  Spotify now requires the developer app's owner to have an active Premium
  subscription, so this path only works if you have Spotify Premium.

The Apple Music side calls Apple's `amp-api.music.apple.com` endpoint (the
same one Apple's own web player uses for infinite scroll), borrowing the
anonymous bearer token embedded in the public JS bundle. This returns the
entire playlist with full pagination, not just the first 300 tracks that
the server-rendered HTML inlines. If the API call ever fails (rate limits,
Apple changes the bundle, etc.) the tool falls back to scraping the HTML so
it degrades to "first ~300" rather than crashing.

The official Apple Music API is not used because it requires a paid Apple
Developer membership.

## Matching contract

Two tracks are considered the same recording when:

1. Their **normalized titles** match exactly, or are >= a fuzzy threshold
   (default 90 on `rapidfuzz.WRatio`), AND
2. Their **artist sets intersect** after normalization.

If both sides expose `duration_ms`, it is used **only** as a tiebreaker when
multiple candidates survive the rules above (within 3 seconds).

**Album is never used as a matching signal.** Albums diverge across services
(singles vs. compilations, regional editions, remasters, "Taylor's Version",
etc.). Album is carried through as display-only metadata.

When multiple candidates remain and duration cannot break the tie, the case
goes to the **Ambiguous** bucket rather than being silently resolved.

## Setup

1. Python 3.11+.
2. Create a virtualenv and install deps:

   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

3. In Apple Music: open the playlist's settings, enable **Public Playlist**,
   then **Share Playlist -> Copy Link**. Verify the link works in an
   incognito browser before using it.

4. On Spotify, choose one path:

   **CSV path (recommended, free):**
   1. Go to <https://exportify.app/>.
   2. Click **Get Started** and sign in with your normal Spotify account.
   3. Find your playlist in the list and click **Export**.
   4. Save the CSV anywhere you like, e.g. `playlists/my_list.csv`.

   **API path (only if you have Spotify Premium):**
   1. Go to <https://developer.spotify.com/dashboard>, create an app.
   2. Copy `Client ID` and `Client Secret` into a `.env` file:

      ```env
      SPOTIFY_CLIENT_ID=...
      SPOTIFY_CLIENT_SECRET=...
      ```

   3. Make sure the playlist you want to diff is public on Spotify.

## Usage

### Web UI (recommended for casual use)

```powershell
.\.venv\Scripts\Activate.ps1
python -m webapp
```

Open <http://127.0.0.1:5000> and:

1. Paste your Apple Music shared playlist URL.
2. Choose your Exportify CSV.
3. Click **Run diff**.

The result page shows six sections - Matched, Only in Spotify, Only in Apple
Music, Ambiguous, Duplicates within your Spotify playlist, Duplicates within
your Apple Music playlist - plus a **Download diff.csv** button. Each diff is
held in memory for one hour and is wiped on server restart; this is
intentional for a personal local-only tool.

### CLI (good for scripting)

#### CSV path (recommended)

```powershell
python -m music_diff.cli `
  --spotify-csv "playlists\my_list.csv" `
  --apple-url   "https://music.apple.com/us/playlist/my-list/pl.u-XXXXXXXXX" `
  --out diff.csv --format csv
```

#### API path (Premium only)

```powershell
python -m music_diff.cli `
  --spotify-url "https://open.spotify.com/playlist/XXXXXXXXXXXXXXXXXXXXXX" `
  --apple-url   "https://music.apple.com/us/playlist/my-list/pl.u-XXXXXXXXX" `
  --out diff.csv --format csv
```

### Flags

| Flag | Default | Description |
| --- | --- | --- |
| `--spotify-csv` | one of these | Path to an Exportify CSV. |
| `--spotify-url` | required | Public Spotify playlist URL or 22-char ID (Premium required). |
| `--apple-url` | required | Shared Apple Music playlist URL. |
| `--out` | none | If set, write the full diff to this file. |
| `--format` | `csv` | Output file format: `csv` or `json`. |
| `--fuzzy-threshold` | `90` | rapidfuzz WRatio cutoff (0-100). |
| `--interactive` | off | Prompt to resolve each ambiguous case in the terminal. |

The console always shows four `rich` tables: Matched, Only in Spotify, Only in
Apple Music, Ambiguous.

## Running tests

```powershell
pytest -q
```

## Known limits

- Apple's amp-api endpoint and JS bundle layout are not officially documented;
  if Apple reshapes either, the tool falls back to scraping the HTML page
  (first ~300 tracks). Both paths are covered by tests.
- No ISRCs are exposed by the Apple shared-page path, so true cross-service
  identity matching isn't possible; we rely on title + artist + (optional)
  duration.
- The Spotify Web API path (Premium-only since Feb 2026) handles rate limits
  with `Retry-After` backoff.
- Private Spotify playlists, the official Apple Music API, and Spotify
  account-side data (Liked Songs, etc.) are out of scope for this version.
