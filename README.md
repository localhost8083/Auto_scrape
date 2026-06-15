# Auto IPTV — Autonomous Playlist Scraper

An autonomous pipeline that scrapes the full public **iptv-org** catalogue
(plus any extra mirrors you configure), **validates every stream**, and
publishes ready-to-use **M3U playlists + a JSON API** — rebuilt automatically
every **15 minutes** via GitHub Actions and served **directly from the repo**
via `raw.githubusercontent.com` / **jsDelivr CDN** (no GitHub Pages / Pro plan
needed).

> This only aggregates **publicly listed free-to-air** channels from
> [iptv-org/iptv](https://github.com/iptv-org/iptv) and mirrors you provide.
> No paywalled / pirated content is hosted.

---

## What it produces

```
output/
├── index.m3u                 # ALL streams (full, unfiltered)
├── working.m3u               # validated-ONLINE streams only  ← use this one
├── index.html                # browsable landing page (open locally or via raw)
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

Playlists are committed to the repo, so you can stream them directly via
**raw** (works for public *and* private repos) or **jsDelivr** (cached CDN,
public repos only). Replace `<user>` and `<repo>`:

```
# raw.githubusercontent.com  (always up to date)
https://raw.githubusercontent.com/<user>/<repo>/main/output/working.m3u          # online only
https://raw.githubusercontent.com/<user>/<repo>/main/output/categories/sports.m3u # sports
https://raw.githubusercontent.com/<user>/<repo>/main/output/countries/us.m3u      # USA
https://raw.githubusercontent.com/<user>/<repo>/main/output/index.m3u             # everything

# jsDelivr CDN  (faster, cached ~10 min — public repos only)
https://cdn.jsdelivr.net/gh/<user>/<repo>@main/output/working.m3u
https://cdn.jsdelivr.net/gh/<user>/<repo>@main/output/categories/sports.m3u
```

JSON API (for apps / dashboards):

```
https://raw.githubusercontent.com/<user>/<repo>/main/output/api/streams.json
https://raw.githubusercontent.com/<user>/<repo>/main/output/api/report.json
```

> **Make the repo public** for free, no-auth URL access (raw + jsDelivr).
> If it must stay **private**, raw URLs require a token — or just `git clone`
> the repo and use the files in `output/` directly.

## EPG (TV guide)

Per-channel guide site is included in `api/channels.json` (`guide` field) and
the full XMLTV guide is published by iptv-org at
`https://iptv-org.github.io/epg/guide.xml` (very large; point your player at
that URL for EPG data).

---

## Setup (one-time, after pushing to GitHub)

1. **Make the repo public** (Settings → General → Danger Zone → Change visibility).
   This enables free, no-auth playlist URLs via `raw.githubusercontent.com` and
   the jsDelivr CDN. *(No GitHub Pages / Pro plan required.)*
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
5. Commits results to `output/` — accessible via `raw.githubusercontent.com`
   and jsDelivr (no Pages needed).

## License / attribution

Stream data © [iptv-org](https://github.com/iptv-org/iptv) contributors
(MIT). This project is an automation layer on top of public sources.
