"""Read the publication time that each website printed on its own article.

    python scripts/web_published.py
    python scripts/web_published.py --offline   # only pages already saved in the cache
    python scripts/web_published.py --event elizabeth-2022

GDELT records when its crawler found an article, which can be hours after the site published
it. Most news pages also carry their own publication time in the page's metadata
(article:published_time, datePublished and similar). This script opens the first matching
article of every site in data/raw/web_sources.csv and adds that time as published_utc, with
the page's own headline as page_title.

Only the page's main publication time is read: the first time given by the most specific
tag the page has. It is kept only if it carries a time zone, falls after the moment the news
was public, and is no later than GDELT's own time, so a page that was updated later, or a live
blog started before the event, cannot move a site the wrong way. Pages that no longer exist, refuse
the request or carry no usable time keep GDELT's time. Responses are cached in
data/raw/cache/pages/, so the script can be stopped and restarted.
"""
import csv
import gzip
import hashlib
import html
import json
import re
import sys
import time
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "raw" / "web_sources.csv"
CACHE = ROOT / "data" / "raw" / "cache" / "pages"
EVENTS = {e["id"]: e for e in json.loads((ROOT / "data" / "reference" / "events.json").read_text())["web_events"]}
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"

PATTERNS = [
    r'<meta[^>]+(?:property|name|itemprop)=["\'](?:article:published_time|og:published_time|datePublished|pubdate|publishdate|publish-date|date|dc\.date\.issued|DC\.date\.issued|parsely-pub-date|sailthru\.date|article\.published|cXenseParse:recs:publishtime)["\'][^>]+content=["\']([^"\']+)["\']',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name|itemprop)=["\'](?:article:published_time|og:published_time|datePublished|pubdate|publishdate|date|parsely-pub-date)["\']',
    r'"datePublished"\s*:\s*"([^"]+)"',
    r'<time[^>]+datetime=["\']([^"\']+)["\']',
]


def parse_time(s):
    s = s.strip().replace(" ", "T", 1) if re.match(r"\d{4}-\d\d-\d\d \d", s.strip()) else s.strip()
    s = re.sub(r"Z$", "+00:00", s)
    s = re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", s)
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        return None
    return t.astimezone(timezone.utc) if t.tzinfo else None   # a time without a zone is not usable


OFFLINE = "--offline" in sys.argv   # use only pages already saved; never write an empty one


def page(url):
    path = CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".html")
    if path.exists():
        return path.read_text(errors="replace")
    if OFFLINE:
        return ""
    text = ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
        with urllib.request.urlopen(req, timeout=15) as r:
            # read in pieces with a hard limit of 20 seconds, so a site that sends its page
            # a trickle at a time cannot hold up the whole run
            raw, start = b"", time.monotonic()
            while len(raw) < 1_500_000 and time.monotonic() - start < 20:
                piece = r.read(65_536)
                if not piece:
                    break
                raw += piece
            enc = r.headers.get("Content-Encoding", "")
            if enc == "gzip":
                raw = gzip.decompress(raw)
            elif enc == "deflate":
                raw = zlib.decompress(raw)
            text = raw.decode(r.headers.get_content_charset() or "utf-8", errors="replace")
    except Exception:
        text = ""
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(text[:400_000])
    return text


def headline(text):
    """The article's headline as the page gives it, for articles GDELT stored without a title."""
    text = text[:300_000]
    found = {}
    for tag in re.findall(r"<meta\b[^>]{0,3000}>", text, re.I):
        attrs = {k.lower(): v for k, _, v in re.findall(r"([\w:.-]+)\s*=\s*([\"'])(.*?)\2", tag, re.S)}
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if key in ("og:title", "twitter:title") and attrs.get("content"):
            found.setdefault(key, attrs["content"])
    m = re.search(r"<title[^>]*>([^<]{1,1000})</title>", text, re.I)
    t = found.get("og:title") or found.get("twitter:title") or (m.group(1) if m else "")
    return re.sub(r"\s+", " ", html.unescape(t)).strip()[:300]


def published(row):
    ev = EVENTS[row["event"]]
    public = datetime.fromisoformat(ev["public_utc"].replace("Z", "+00:00"))
    found = datetime.fromisoformat(row["first_seen_utc"].replace("Z", "+00:00"))
    text = page(row["url"])
    if not text:
        return "", "unreachable"
    row["page_title"] = headline(text)
    # The patterns run from the most specific (the article's own published_time) to the least
    # (any <time> tag). Only the first time found by the most specific pattern that finds one
    # is used, so a time printed beside another article in a sidebar is never picked instead.
    for p in PATTERNS:
        times = [t for m in re.findall(p, text[:300_000], re.I) if (t := parse_time(m))]
        if times:
            t = times[0]
            if public <= t <= found + timedelta(minutes=5):
                return t.strftime("%Y-%m-%dT%H:%M:%SZ"), "page"
            return "", "time outside window"
    return "", "no usable time"


def main():
    only = sys.argv[sys.argv.index("--event") + 1] if "--event" in sys.argv else None
    with SRC.open() as f:
        rows = list(csv.DictReader(f))
    todo = [r for r in rows if not only or r["event"] == only]
    print(f"{len(todo)} articles to open", flush=True)
    tally = {}
    with ThreadPoolExecutor(max_workers=16) as pool:
        for i, (row, (t, how)) in enumerate(zip(todo, pool.map(published, todo)), 1):
            row["published_utc"], row["published_from"] = t, how
            tally[how] = tally.get(how, 0) + 1
            if i % 500 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)}  {tally}", flush=True)
    out = [{**{"published_utc": "", "published_from": "", "page_title": ""}, **r} for r in rows]
    with SRC.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["event", "site", "first_seen_utc", "url", "title", "lang", "published_utc", "published_from", "page_title"])
        w.writeheader()
        w.writerows(out)
    print(f"Wrote {SRC.relative_to(ROOT)}")


if __name__ == "__main__":
    if sys.version_info < (3, 8):
        sys.exit("Python 3.8 or later is needed")
    main()
