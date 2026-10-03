"""When did each news website first publish the story? (the web era)

    python scripts/collect_web.py              # all web events, 48 hours each
    python scripts/collect_web.py --event elizabeth-2022 --hours 24
    python scripts/collect_web.py --probe      # one file, to check the connection

GDELT (gdeltproject.org) reads news sites around the world and publishes what it found every
15 minutes, as Global Knowledge Graph files: one row per article, with its address, title, the
names and places in it, and for non-English articles a machine translation of those fields.
This script reads the English and the translated files for the hours after each event, keeps
the articles that report the event, and records, for every news site, the first 15-minute
batch in which one of its articles appeared.

GDELT's time is when its crawler found the article, which for some sites is hours after it was
published, so every arrival here is an upper bound. scripts/web_published.py then reads the
publication time printed in each article's own page where there is one.

Standard library only. Every article that names the event's subject (the first rule) is cached
in data/raw/cache/gdelt2/ with its title, names, places and themes, so the rules can be changed
and the script re-run without downloading again.
"""
import argparse
import csv
import html
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
CACHE = RAW / "cache" / "gdelt2"
OUT = RAW / "web_sources.csv"
EVENTS = json.loads((ROOT / "data" / "reference" / "events.json").read_text())["web_events"]
BASE = "http://data.gdeltproject.org/gdeltv2/"
csv.field_size_limit(sys.maxsize)

# GKG 2.1 columns used here
C_DATE, C_SOURCE, C_URL, C_THEMES, C_LOCS, C_NAMES, C_TRANS, C_EXTRAS = 1, 3, 4, 8, 10, 23, 25, 26


def log(*a):
    print(*a, flush=True)


def fetch(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "news-travel-time study"}), timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(5 * (i + 1))
        except Exception:
            time.sleep(5 * (i + 1))
    return None


def stamps(start, hours):
    t = start.replace(second=0, microsecond=0, minute=start.minute - start.minute % 15)
    end = start + timedelta(hours=hours)
    while t <= end:
        yield t.strftime("%Y%m%d%H%M%S")
        t += timedelta(minutes=15)


def title_of(extras):
    m = re.search(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", extras or "")
    return m.group(1).strip() if m else ""


def lang_of(trans):
    m = re.search(r"srclc:(\w+)", trans or "")
    return m.group(1) if m else "eng"


FIELDS = ("date", "site", "url", "title", "lang", "names", "places", "themes")


def compile_rules(ev):
    return [(re.compile(r["pattern"], re.I), r["fields"]) for r in ev["match"]]


def passes(rules, rec):
    return all(rx.search(" ".join(rec[f] for f in fs)) for rx, fs in rules)


def scan(ev, stamp, kind):
    """Articles in one 15-minute file that name the event's subject, cached with their fields."""
    cache = CACHE / ev["id"] / f"{stamp}-{kind}.tsv"
    if cache.exists():
        # split on newlines only: titles can hold other characters that splitlines() treats as breaks
        lines = cache.read_text(encoding="utf-8").split("\n")
        return [dict(zip(FIELDS, parts)) for parts in (l.split("\t") for l in lines) if len(parts) == len(FIELDS)]
    name = f"{stamp}.translation.gkg.csv.zip" if kind == "tr" else f"{stamp}.gkg.csv.zip"
    blob = fetch(BASE + name)
    subject = compile_rules(ev)[:1]
    recs = []
    if blob:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            with z.open(z.namelist()[0]) as f:
                for line in io.TextIOWrapper(f, encoding="utf-8", errors="replace"):
                    row = line.rstrip("\n").split("\t")
                    if len(row) < 27:
                        continue
                    rec = {"date": row[C_DATE], "site": row[C_SOURCE], "url": row[C_URL],
                           "title": html.unescape(title_of(row[C_EXTRAS])), "lang": lang_of(row[C_TRANS]),
                           "names": row[C_NAMES][:1500], "places": row[C_LOCS][:1500], "themes": row[C_THEMES][:3000]}
                    rec = {k: re.sub(r"[\t\r\n\x0b\x0c\x1c-\x1e\x85\u2028\u2029]", " ", v) for k, v in rec.items()}
                    if passes(subject, rec):
                        recs.append(rec)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("".join("\t".join(r[k] for k in FIELDS) + "\n" for r in recs) if blob else "", encoding="utf-8")
    return recs


def collect(ev, hours):
    start = datetime.fromisoformat(ev["public_utc"].replace("Z", "+00:00"))
    jobs = [(s, k) for s in stamps(start, hours) for k in ("en", "tr")]
    log(f"\n{ev['label']}: {len(jobs)} files from {start:%Y-%m-%d %H:%M} UTC, {hours} hours")
    rules = compile_rules(ev)
    reject = re.compile(ev["reject"], re.I) if ev.get("reject") else None
    first = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for i, recs in enumerate(pool.map(lambda j: scan(ev, *j), jobs), 1):
            for r in recs:
                t = datetime.strptime(r["date"], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
                # a batch holds what GDELT found in the 15 minutes before its time stamp,
                # so only batches stamped after the event can hold reports of it
                if t <= start or not r["site"] or not passes(rules, r):
                    continue
                if reject and reject.search(f"{r['url']} {r['title']}"):
                    continue
                if r["site"] not in first or t < first[r["site"]][0]:
                    first[r["site"]] = (t, r)
            if i % 40 == 0 or i == len(jobs):
                log(f"  {i}/{len(jobs)} files, {len(first)} sites so far")
    return [{"event": ev["id"], "site": s, "first_seen_utc": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
             "url": r["url"], "title": r["title"], "lang": r["lang"]}
            for s, (t, r) in sorted(first.items(), key=lambda x: x[1][0])]


def probe():
    ev = EVENTS[0]
    start = datetime.fromisoformat(ev["public_utc"].replace("Z", "+00:00")) + timedelta(hours=2)
    s = next(stamps(start, 0))
    blob = fetch(BASE + f"{s}.gkg.csv.zip")
    if not blob:
        sys.exit("FAILED: could not download a GDELT file. Is data.gdeltproject.org reachable?")
    log(f"Downloaded {s}.gkg.csv.zip, {len(blob) / 1e6:.1f} MB")
    log(f"Articles naming the subject of {ev['id']}: {len(scan(ev, s, 'en'))}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--event")
    ap.add_argument("--hours", type=int, default=48)
    ap.add_argument("--probe", action="store_true")
    a = ap.parse_args()
    if a.probe:
        return probe()
    rows = []
    if OUT.exists() and a.event:   # keep the other events when one is re-run
        with OUT.open() as f:
            rows = [r for r in csv.DictReader(f) if r["event"] != a.event]
    for ev in EVENTS:
        if a.event and ev["id"] != a.event:
            continue
        rows += collect(ev, a.hours)
    with OUT.open("w", newline="") as f:
        # publication times are added later by web_published.py; rows kept from other events keep theirs
        w = csv.DictWriter(f, fieldnames=["event", "site", "first_seen_utc", "url", "title", "lang", "published_utc", "published_from", "page_title"], restval="")
        w.writeheader()
        w.writerows(rows)
    log(f"\nWrote {OUT.relative_to(ROOT)}: {len(rows)} sites")


if __name__ == "__main__":
    main()
