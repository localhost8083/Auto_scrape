#!/usr/bin/env python3
"""
Auto IPTV Scraper & Playlist Builder
=====================================
Pulls the full iptv-org catalogue (channels / streams / categories /
countries / languages / guides) plus any extra M3U mirrors from
config/sources.json, validates every stream concurrently, then emits:

  output/
    index.m3u               <- ALL streams (full, unfiltered)
    working.m3u             <- validated-ONLINE streams only
    categories/<id>.m3u     <- online-only playlist per category
    categories/<id>.all.m3u <- all streams per category
    countries/<code>.m3u    <- online-only per country
    languages/<code>.m3u    <- online-only per language
    api/streams.json        <- all streams + live status
    api/channels.json
    api/categories.json
    api/countries.json
    api/guides.json
    api/report.json         <- run summary
    last_updated.json
    index.html              <- browsable landing page (GitHub Pages)

Env overrides: WORKERS, TIMEOUT, VALIDATE_LIMIT, EXTRA_SOURCES_URLS
CLI: python scripts/scraper.py --outdir output [--no-validate]
"""

import argparse
import json
import os
import shutil
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

API = "https://iptv-org.github.io/api"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 AutoScrape/1.0")
HEADERS = {"User-Agent": UA, "Accept": "*/*"}


# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def http_get(url: str, timeout: float = 60) -> requests.Response:
    return requests.get(url, headers=HEADERS, timeout=timeout)


def fetch_json(url: str, timeout: float = 60):
    r = http_get(url, timeout)
    r.raise_for_status()
    return r.json()


def classify(url: str) -> str:
    p = urlparse(url).path.lower()
    if p.endswith(".m3u8") or p.endswith(".m3u"):
        return "hls"
    if p.endswith(".mpd"):
        return "dash"
    return "direct" if any(p.endswith(e) for e in (".ts", ".flv", ".mp4")) else "hls"


# --------------------------------------------------------------------------- #
@dataclass
class Item:
    name: str = ""
    url: str = ""
    logo: str = ""
    tvg_id: str = ""
    group: str = ""
    category: str = ""
    country: str = ""
    country_name: str = ""
    languages: list = field(default_factory=list)
    source: str = "iptv-org"
    guide: str = ""
    kind: str = ""
    status: str = "pending"
    http_code: int | None = None
    latency_ms: int | None = None
    content_type: str = ""


# --------------------------------------------------------------------------- #
def load_iptv_org() -> tuple[list[Item], dict, dict, dict, dict]:
    print("[*] Fetching iptv-org API...", flush=True)
    channels = fetch_json(f"{API}/channels.json", 90)
    streams = fetch_json(f"{API}/streams.json", 90)
    categories = fetch_json(f"{API}/categories.json", 60)
    countries = fetch_json(f"{API}/countries.json", 60)
    languages = fetch_json(f"{API}/languages.json", 60)
    try:
        guides = fetch_json(f"{API}/guides.json", 60)
    except Exception:
        guides = []
    try:
        subdivisions = fetch_json(f"{API}/subdivisions.json", 60)
    except Exception:
        subdivisions = []

    print(f"    channels={len(channels)} streams={len(streams)} "
          f"categories={len(categories)} countries={len(countries)}", flush=True)

    ch_by_id = {c["id"]: c for c in channels if "id" in c}
    cat_name = {c["id"]: c.get("name", c["id"]) for c in categories if "id" in c}
    ctry = {c["code"]: c for c in countries if "code" in c}
    lang_name = {l["code"]: l.get("name", l["code"]) for l in languages if "code" in l}
    guide_by_channel = {g["channel"]: g["site"] for g in guides
                        if "channel" in g and "site" in g}

    items: list[Item] = []
    for st in streams:
        url = st.get("url", "")
        if not url:
            continue
        ch = ch_by_id.get(st.get("channel"))
        cats = (ch or {}).get("categories", [])
        ccode = (ch or {}).get("country", "")
        cinfo = ctry.get(ccode, {})
        langs = cinfo.get("languages", []) or []
        primary_cat = cat_name.get(cats[0], "") if cats else ""
        group = ", ".join(cat_name.get(c, c) for c in cats) if cats else "Uncategorized"
        items.append(Item(
            name=(ch or {}).get("name", st.get("channel", "Stream")),
            url=url,
            logo=(ch or {}).get("logo", ""),
            tvg_id=st.get("channel", "") or (ch or {}).get("id", ""),
            group=group,
            category=primary_cat,
            country=ccode,
            country_name=cinfo.get("name", ccode),
            languages=langs,
            source="iptv-org",
            guide=guide_by_channel.get(st.get("channel"), ""),
            kind=classify(url),
        ))
    return items, cat_name, ctry, lang_name, guide_by_channel


# --------------------------------------------------------------------------- #
def parse_m3u(text: str, source: str) -> list[Item]:
    import re
    attrs_re = re.compile(r'([\w-]+)="([^"]*)"')
    out: list[Item] = []
    cur: dict = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.upper().startswith("#EXTINF"):
            cur = {}
            rest = line.split(",", 1)[-1] if "," in line else ""
            meta = line.split(",", 1)[0]
            cur = dict(attrs_re.findall(meta))
            cur["__name"] = rest.strip()
        elif line.startswith("#EXTGRP:"):
            cur["group"] = line.split(":", 1)[1].strip()
        elif line.startswith("#"):
            continue
        else:
            if not cur:
                cur = {"__name": urlparse(line).path.rsplit("/", 1)[-1] or "stream"}
            out.append(Item(
                name=cur.get("__name", "stream"),
                url=line, source=source,
                logo=cur.get("tvg-logo", ""), tvg_id=cur.get("tvg-id", ""),
                group=cur.get("group-title", cur.get("group", "External")),
                country=cur.get("tvg-country", ""),
                kind=classify(line),
            ))
            cur = {}
    return out


def load_extra_sources() -> list[Item]:
    out: list[Item] = []
    urls: list[str] = []
    cfg = Path(__file__).resolve().parent.parent / "config" / "sources.json"
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            urls += [s.get("url") for s in data.get("sources", [])
                     if s.get("url") and s.get("enabled", True)]
        except Exception as e:
            print(f"    [config] sources.json parse error: {e}", flush=True)
    env = os.environ.get("EXTRA_SOURCES_URLS", "")
    urls += [u.strip() for u in env.split(",") if u.strip()]
    seen = set()
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        try:
            r = http_get(u, 40)
            if r.status_code == 200:
                parsed = parse_m3u(r.text, f"external")
                out += parsed
                print(f"    [extra] {u}: {len(parsed)} streams", flush=True)
            else:
                print(f"    [extra] {u}: HTTP {r.status_code}", flush=True)
        except Exception as e:
            print(f"    [extra] {u}: {e}", flush=True)
    return out


# --------------------------------------------------------------------------- #
def validate_one(item: Item, timeout: float) -> Item:
    try:
        r = requests.get(item.url, headers=HEADERS, stream=True,
                         timeout=timeout, allow_redirects=True)
        item.http_code = r.status_code
        item.content_type = r.headers.get("Content-Type", "").split(";")[0].strip()
        head = b""
        try:
            head = next(r.iter_content(2048), b"") or b""
        except Exception:
            pass
        r.close()
        ok = 200 <= r.status_code < 400
        text = head.decode("utf-8", "ignore")
        if item.kind == "hls":
            ok = ok and ("#EXTM3U" in text or "#EXT-X-" in text)
        elif item.kind == "dash":
            ok = ok and ("<MPD" in text)
        item.status = "online" if ok else "offline"
    except requests.exceptions.Timeout:
        item.status = "offline"
    except Exception:
        item.status = "offline"
    return item


def validate_all(items: list[Item], workers: int, timeout: float) -> list[Item]:
    t0 = time.time()
    n = len(items)
    done = 0
    online = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(validate_one, it, timeout) for it in items]
        for f in as_completed(futs):
            r = f.result()
            done += 1
            if r.status == "online":
                online += 1
            if done % 200 == 0 or done == n:
                print(f"    validated {done}/{n}  online={online}  "
                      f"({time.time()-t0:.0f}s)", flush=True)
    print(f"[*] Validation complete: {online}/{n} online in {time.time()-t0:.0f}s", flush=True)
    return items


# --------------------------------------------------------------------------- #
def write_m3u(path: Path, items: list[Item]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for it in items:
            attrs = []
            if it.tvg_id:
                attrs.append(f'tvg-id="{it.tvg_id}"')
            if it.logo:
                attrs.append(f'tvg-logo="{it.logo}"')
            grp = it.group or it.category or "General"
            attrs.append(f'group-title="{grp}"')
            f.write(f"#EXTINF:-1 {' '.join(attrs)},{it.name}\n{it.url}\n")


def build_outputs(items: list[Item], cat_names: dict, lang_names: dict, outdir: Path):
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "api").mkdir(exist_ok=True)

    online = [i for i in items if i.status == "online"]
    pending = [i for i in items if i.status != "online"]

    write_m3u(outdir / "index.m3u", items)
    write_m3u(outdir / "working.m3u", online)

    # categories
    by_cat_all: dict[str, list[Item]] = defaultdict(list)
    by_cat_on: dict[str, list[Item]] = defaultdict(list)
    for it in items:
        cats = [c.strip() for c in (it.group or "").split(",")] if it.group else []
        cats = cats or ["Uncategorized"]
        for c in cats:
            by_cat_all[c].append(it)
            if it.status == "online":
                by_cat_on[c].append(it)
    cdir = outdir / "categories"
    if cdir.exists():
        shutil.rmtree(cdir)
    cdir.mkdir()
    for cat, lst in by_cat_on.items():
        slug = cat.lower().replace(" ", "-").replace("/", "-")
        write_m3u(cdir / f"{slug}.m3u", lst)
    for cat, lst in by_cat_all.items():
        slug = cat.lower().replace(" ", "-").replace("/", "-")
        write_m3u(cdir / f"{slug}.all.m3u", lst)

    # countries
    by_country: dict[str, list[Item]] = defaultdict(list)
    for it in online:
        if it.country:
            by_country[it.country].append(it)
    cod = outdir / "countries"
    if cod.exists():
        shutil.rmtree(cod)
    cod.mkdir()
    for code, lst in by_country.items():
        write_m3u(cod / f"{code.lower()}.m3u", lst)

    # languages
    by_lang: dict[str, list[Item]] = defaultdict(list)
    for it in online:
        for l in it.languages:
            by_lang[l].append(it)
    ldir = outdir / "languages"
    if ldir.exists():
        shutil.rmtree(ldir)
    ldir.mkdir()
    for code, lst in by_lang.items():
        write_m3u(ldir / f"{code.lower()}.m3u", lst)

    # JSON API
    def ser(i: Item) -> dict:
        return {"name": i.name, "url": i.url, "logo": i.logo, "tvg_id": i.tvg_id,
                "group": i.group, "category": i.category, "country": i.country,
                "country_name": i.country_name, "languages": i.languages,
                "source": i.source, "guide": i.guide, "kind": i.kind,
                "status": i.status, "http_code": i.http_code,
                "latency_ms": i.latency_ms, "content_type": i.content_type}

    with open(outdir / "api" / "streams.json", "w", encoding="utf-8") as f:
        json.dump([ser(i) for i in items], f, ensure_ascii=False)
    with open(outdir / "api" / "channels.json", "w", encoding="utf-8") as f:
        ch = {}
        for i in items:
            ch.setdefault(i.tvg_id or i.name, {
                "name": i.name, "logo": i.logo, "country": i.country,
                "country_name": i.country_name, "category": i.category,
                "group": i.group, "guide": i.guide,
                "urls": [], "online": False})
            entry = ch[i.tvg_id or i.name]
            entry["urls"].append(i.url)
            if i.status == "online":
                entry["online"] = True
        json.dump(list(ch.values()), f, ensure_ascii=False, indent=1)
    with open(outdir / "api" / "categories.json", "w", encoding="utf-8") as f:
        json.dump([{"id": k, "name": v, "online": len(by_cat_on.get(v, [])),
                    "total": len(by_cat_all.get(v, []))}
                   for k, v in sorted(cat_names.items())], f, ensure_ascii=False, indent=1)
    with open(outdir / "api" / "countries.json", "w", encoding="utf-8") as f:
        cc = {}
        for i in online:
            cc[i.country] = i.country_name
        json.dump([{"code": k, "name": v, "online": len([x for x in online if x.country == k])}
                   for k, v in sorted(cc.items())], f, ensure_ascii=False, indent=1)

    # report
    by_source: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for i in items:
        by_source[i.source][0] += 1
        if i.status == "online":
            by_source[i.source][1] += 1
    report = {
        "generated_at": now_iso(),
        "total_streams": len(items),
        "online_streams": len(online),
        "offline_streams": len(pending),
        "online_percent": round(100 * len(online) / max(len(items), 1), 1),
        "categories": len(by_cat_on),
        "countries": len(by_country),
        "languages": len(by_lang),
        "by_source": {k: {"total": v[0], "online": v[1]} for k, v in by_source.items()},
        "top_categories": sorted(
            [{"name": k, "online": len(v)} for k, v in by_cat_on.items()],
            key=lambda x: -x["online"])[:20],
    }
    with open(outdir / "api" / "report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    with open(outdir / "last_updated.json", "w", encoding="utf-8") as f:
        json.dump({"last_updated": now_iso(),
                   "online": len(online), "total": len(items)}, f, indent=2)

    write_index_html(outdir, report, by_cat_on, by_country)
    return report


def write_index_html(outdir: Path, report, by_cat_on, by_country):
    def slug(s):
        return s.lower().replace(" ", "-").replace("/", "-")
    cat_rows = "".join(
        f'<li><a href="categories/{slug(c)}.m3u">{c}</a> '
        f'<span>{len(lst)}</span></li>'
        for c, lst in sorted(by_cat_on.items(), key=lambda x: -len(x[1])))
    ctry_rows = "".join(
        f'<li><a href="countries/{c.lower()}.m3u">{c.upper()}</a> '
        f'<span>{len(lst)}</span></li>'
        for c, lst in sorted(by_country.items(), key=lambda x: -len(x[1]))[:60])
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Auto IPTV Playlists</title>
<style>
body{{font-family:system-ui,sans-serif;background:#0d1117;color:#e6edf3;margin:0;padding:28px;line-height:1.5}}
h1{{font-size:26px;margin:0 0 4px}} .sub{{color:#8b949e;margin-bottom:22px}}
.hero{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;max-width:760px;margin-bottom:28px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px}}
.card b{{display:block;font-size:30px;color:#58a6ff}} .card span{{color:#8b949e;font-size:12px}}
h2{{font-size:15px;text-transform:uppercase;letter-spacing:.6px;color:#8b949e;margin:24px 0 10px}}
a{{color:#58a6ff;text-decoration:none}} a:hover{{text-decoration:underline}}
.cols{{display:grid;grid-template-columns:1fr 1fr;gap:24px;max-width:760px}}
ul{{list-style:none;padding:0;margin:0;max-height:340px;overflow:auto;columns:2;-webkit-columns:2}}
li{{padding:4px 0;border-bottom:1px solid #21262d;display:flex;justify-content:space-between;gap:8px;break-inside:avoid}}
li span{{color:#8b949e;font-size:12px}} code{{background:#1c2330;padding:2px 6px;border-radius:4px;font-size:12px}}
.muted{{color:#8b949e;font-size:13px}}
</style></head><body>
<h1>&#128250; Auto IPTV</h1>
<div class="sub">Auto-updated public IPTV playlists &middot; last build {report['generated_at']}</div>
<div class="hero">
  <div class="card"><b>{report['online_streams']}</b><span>Online streams</span></div>
  <div class="card"><b>{report['total_streams']}</b><span>Total streams</span></div>
  <div class="card"><b>{report['categories']}</b><span>Categories</span></div>
  <div class="card"><b>{report['countries']}</b><span>Countries</span></div>
</div>
<p class="muted">Master playlist: <code>index.m3u</code> (all) &middot;
<code>working.m3u</code> (online only) &middot;
JSON API: <code>api/streams.json</code></p>
<div class="cols">
<div><h2>Categories (online)</h2><ul>{cat_rows}</ul></div>
<div><h2>Countries (online)</h2><ul>{ctry_rows}</ul></div>
</div>
<p class="muted" style="margin-top:24px">Add to VLC/mpv: <code>https://&lt;user&gt;.github.io/&lt;repo&gt;/working.m3u</code></p>
</body></html>"""
    with open(outdir / "index.html", "w", encoding="utf-8") as f:
        f.write(html)


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Auto IPTV scraper & playlist builder")
    ap.add_argument("--outdir", default="output")
    ap.add_argument("--no-validate", action="store_true")
    args = ap.parse_args()
    outdir = Path(args.outdir)

    workers = int(os.environ.get("WORKERS", "60"))
    timeout = float(os.environ.get("TIMEOUT", "8"))
    limit = int(os.environ.get("VALIDATE_LIMIT", "0"))

    print(f"== Auto IPTV Scraper == workers={workers} timeout={timeout}s "
          f"limit={limit or 'all'}", flush=True)

    items, cat_names, countries, lang_names, guides = load_iptv_org()
    items += load_extra_sources()

    # dedupe by url
    seen = set()
    uniq: list[Item] = []
    for it in items:
        if it.url and it.url not in seen:
            seen.add(it.url)
            uniq.append(it)
    items = uniq
    print(f"[*] {len(items)} unique streams.", flush=True)

    if limit and len(items) > limit:
        items = items[:limit]
        print(f"[*] Capped to {len(items)} (VALIDATE_LIMIT).", flush=True)

    if not args.no_validate:
        validate_all(items, workers, timeout)
    else:
        for it in items:
            it.status = "online"

    report = build_outputs(items, cat_names, lang_names, outdir)
    print(f"\n==============================", flush=True)
    print(f" DONE  online={report['online_streams']}/{report['total_streams']} "
          f"({report['online_percent']}%)", flush=True)
    print(f" categories={report['categories']} countries={report['countries']} "
          f"languages={report['languages']}", flush=True)
    print(f" output -> {outdir.resolve()}", flush=True)
    print(f"==============================", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
