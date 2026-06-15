# Auto IPTV — Autonomous Playlist Scraper

An autonomous pipeline that scrapes the full public **iptv-org** catalogue
(plus any extra mirrors you configure), **validates every stream**, and
publishes ready-to-use **M3U playlists + a JSON API** — rebuilt automatically
every **15 minutes** via GitHub Actions and served from **GitHub Pages**.

> This only aggregates **publicly listed free-to-air** channels from
> [iptv-org/iptv](https://github.com/iptv-org/iptv) and mirrors you provide.
> No paywalled / pirated content is hosted.

---

## What it produces

```
output/
├── index.m3u                 # ALL streams (full, unfiltered)
├── working.m3u               # validated-ONLINE streams only  ← use this one
├── index.html                # browsable landing page (GitHub Pages)
├── last_updated.json         # build timestamp + counts
├── categories/<name>.m3u     # online-only playlist per category
├── categories/<name>.all.m3u # all streams per category
├── countries/<code>.m3u      # online-only per country
├── languages/<code>.m3u      # online-only per language
└── api/
    ├── streams.json          # every stream + live status
    ├── channels.json         # deduped channels with all URLs
    ├── categories.json       # category -> counts
    ├── countries.json        # country -> counts
    └── report.json           # full run summary
```

## How to use the playlists

In VLC / mpv / IPTV apps, add a URL (replace `<user>` and `<repo>`):

```
https://<user>.github.io/<repo>/working.m3u          # all online channels
https://<user>.github.io/<repo>/categories/sports.m3u # sports only
https://<user>.github.io/<repo>/countries/us.m3u      # USA only
https://<user>.github.io/<repo>/index.m3u             # everything
```

JSON API (for apps / dashboards):

```
https://<user>.github.io/<repo>/api/streams.json
https://<user>.github.io/<repo>/api/report.json
```

## EPG (TV guide)

Per-channel guide site is included in `api/channels.json` (`guide` field) and
the full XMLTV guide is published by iptv-org at
`https://iptv-org.github.io/epg/guide.xml` (very large; point your player at
that URL for EPG data).

---

## Setup (one-time, after pushing to GitHub)

1. **Enable GitHub Pages** (the workflow deploys automatically once enabled):
   - *Settings → Pages → Build and deployment → Source = **GitHub Actions***
2. **Run it now** (don't wait for the cron):
   - *Actions → "Auto Scrape & Update Playlists" → Run workflow*

The schedule runs **every 15 minutes**. To change frequency, edit the `cron`
line in [`.github/workflows/scrape.yml`](.github/workflows/scrape.yml):
`'*/10 * * * *'` → ~every 10 min, `'*/30 * * * *'` → every 30 min.

> Note: GitHub Actions cron is best-effort and can be delayed a few minutes
> during peak load. Runs also keep the repo "active" so the schedule is never
> auto-disabled.

---

## Adding your own sources

Edit [`config/sources.json`](config/sources.json):

```json
{
  "sources": [
    { "name": "my-mirror", "url": "https://example.com/list.m3u", "enabled": true }
  ]
}
```

…or set a repository **secret** named `EXTRA_SOURCES_URLS` (comma-separated URLs)
to keep private lists out of the repo. Both are merged into every run.

---

## Running locally

```bash
pip install -r requirements.txt
python scripts/scraper.py --outdir output
# tune with env vars:
WORKERS=80 TIMEOUT=6 VALIDATE_LIMIT=500 python scripts/scraper.py
# skip validation (build full playlists fast):
python scripts/scraper.py --no-validate
```

## Tuning (env vars)

| Var             | Default | Meaning                              |
|-----------------|---------|--------------------------------------|
| `WORKERS`       | `60`    | concurrent stream validators         |
| `TIMEOUT`       | `8`     | per-stream timeout (seconds)         |
| `VALIDATE_LIMIT`| `0`     | cap streams validated (`0` = all)    |
| `EXTRA_SOURCES_URLS` | — | comma-sep extra `.m3u` URLs         |

## How it works

1. Fetches iptv-org's JSON API (channels, streams, categories, countries,
   languages, guides) — the canonical, community-maintained index of free TV.
2. Merges in any mirrors from `config/sources.json` / `EXTRA_SOURCES_URLS`.
3. Validates each stream concurrently (HLS checks `#EXTM3U`, DASH checks
   `<MPD>`, with HTTP status + latency recorded).
4. Builds per-category / per-country / per-language playlists (online-only
   + `.all` variants) and a JSON API.
5. Commits results to `output/` and deploys to GitHub Pages.

## License / attribution

Stream data © [iptv-org](https://github.com/iptv-org/iptv) contributors
(MIT). This project is an automation layer on top of public sources.
