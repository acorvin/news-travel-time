"""Collect the raw data for the study: when did each newspaper first print the news?

Two steps, each safe to stop and restart (every response is cached on disk):

  1. Census gazetteer files, used later to place each newspaper's town on a map.
  2. Library of Congress, Chronicling America: for each historical event, every
     digitized newspaper page in the window that matches the event's search terms.
     The earliest pages of each newspaper are then read (OCR text) and kept only
     if the text actually reports the event.

    python scripts/collect.py                # step 1
    python scripts/collect.py --only papers  # step 2, see the note below
    python scripts/collect.py --only papers --event lincoln-1865

Step 2 needs the Library of Congress search, which now sits behind a bot check that
scripts cannot pass. The published data was collected with the browser version of the
same procedure, scripts/browser/collect-newspapers.js. This step is kept for when the
search is reachable from a script again.

Standard library only. The Library of Congress allows about 20 search requests a
minute and blocks for an hour if that is exceeded, so requests are paced and the
script waits out any block rather than failing.
"""
import argparse
import csv
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
CACHE = RAW / "cache"
EVENTS = json.loads((ROOT / "data" / "reference" / "events.json").read_text(encoding="utf-8"))

LOC_SEARCH = "https://www.loc.gov/collections/chronicling-america/"
MAX_CANDIDATES = 10      # earliest pages read per newspaper before giving up
MAX_DATES = 6            # ...spread over at most this many issue dates

# Minimum seconds between requests, per host. Raised automatically after a 429.
PACE = {"www.loc.gov": 3.3, "tile.loc.gov": 1.0, "default": 0.25}
_last = {}


LOGFILE = RAW / "collect.log"


def log(*a):
    msg = " ".join(str(x) for x in a)
    print(msg, flush=True)
    RAW.mkdir(parents=True, exist_ok=True)
    with LOGFILE.open("a", encoding="utf-8") as f:
        f.write(time.strftime("%H:%M:%S ") + msg + "\n")


def _cache_path(url, ext):
    return CACHE / (hashlib.sha1(url.encode()).hexdigest() + ext)


BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/128.0 Safari/537.36 NewsTravelTime/1.0 (+https://github.com/acorvin)",
    "Accept": "application/json, text/xml, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
CURL = shutil.which("curl")
LAST_ERROR = {}


def http_get(url):
    """-> (status, body bytes, retry_after seconds). Uses curl when present (its TLS setup is
    accepted by the Library of Congress more reliably than Python's), else urllib."""
    if CURL:
        args = [CURL, "-sS", "-L", "--compressed", "--max-time", "90", "-o", "-", "-w", "\n%{http_code}",
                "-D", "-"]
        for k, v in BROWSER_HEADERS.items():
            args += ["-H", f"{k}: {v}"]
        r = subprocess.run(args + [url], capture_output=True)
        if r.returncode != 0 and not r.stdout:
            raise OSError(r.stderr.decode("utf-8", "replace").strip() or f"curl exit {r.returncode}")
        out = r.stdout
        body, _, code = out.rpartition(b"\n")
        retry_after = 0
        while body.startswith(b"HTTP/"):          # strip header blocks (one per redirect)
            head, sep, rest = body.partition(b"\r\n\r\n")
            if not sep:
                break
            m = re.search(rb"(?im)^retry-after:\s*(\d+)", head)
            retry_after = int(m.group(1)) if m else retry_after
            body = rest
        return int(code or 0), body, retry_after
    req = urllib.request.Request(url, headers=BROWSER_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, r.read(), 0
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b"", int(e.headers.get("Retry-After") or 0)


def fetch(url, ext=".json", binary=False, allow_404=True):
    """GET with an on-disk cache, pacing per host, and retries."""
    p = _cache_path(url, ext)
    if p.exists():
        data = p.read_bytes()
        if data == b"__404__":
            return None
        return data if binary else data.decode("utf-8", "replace")
    host = urllib.parse.urlparse(url).netloc
    pace = PACE.get(host, PACE["default"])
    for attempt in range(12):
        wait = _last.get(host, 0) + pace - time.time()
        if wait > 0:
            time.sleep(wait)
        _last[host] = time.time()
        try:
            status, data, retry = http_get(url)
        except Exception as e:  # timeouts, resets, certificate problems
            LAST_ERROR.update(url=url, error=f"{type(e).__name__}: {e}")
            pause = min(300, 10 * 2 ** attempt)
            log(f"  {type(e).__name__}: {e}; retrying in {pause}s")
            time.sleep(pause)
            continue
        LAST_ERROR.update(url=url, status=status, body=data[:400].decode("utf-8", "replace"))
        if status == 200:
            CACHE.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            return data if binary else data.decode("utf-8", "replace")
        if status == 404 and allow_404:
            CACHE.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"__404__")
            return None
        if status == 429:
            pause = max(retry, 120 * (attempt + 1))
            PACE[host] = pace = pace * 1.5
            log(f"  rate limited by {host}; pausing {pause // 60} min (pace now {pace:.1f}s)")
            time.sleep(pause)
            continue
        if status in (400, 403, 410) and attempt >= 1:
            log(f"  HTTP {status} for {url[:120]}")
            return None
        pause = min(300, 10 * 2 ** attempt)
        log(f"  HTTP {status}; retrying in {pause}s")
        time.sleep(pause)
    log(f"  giving up on {url[:120]}")
    return None


def fetch_json(url):
    t = fetch(url, ".json")
    if t is None:
        return None
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        _cache_path(url, ".json").unlink(missing_ok=True)
        LAST_ERROR.update(url=url, note="response was not JSON", body=t[:400])
        return None


GAZ = [
    ("places", "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/{y}_Gazetteer/{y}_Gaz_place_national.zip"),
    ("counties", "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/{y}_Gazetteer/{y}_Gaz_counties_national.zip"),
]


def step_gazetteer():
    out = RAW / "gazetteer"
    out.mkdir(parents=True, exist_ok=True)
    for name, pattern in GAZ:
        target = out / f"{name}.tsv"
        if target.exists():
            log(f"gazetteer {name}: already downloaded")
            continue
        for y in (2024, 2023, 2022, 2020):
            data = fetch(pattern.format(y=y), ".zip", binary=True)
            if not data:
                continue
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                member = [n for n in z.namelist() if n.endswith(".txt")][0]
                target.write_bytes(z.read(member))
            log(f"gazetteer {name}: {y} edition saved")
            break
        else:
            log(f"gazetteer {name}: could not download (towns will fall back to state centres)")


def first(v, default=""):
    if isinstance(v, list):
        return v[0] if v else default
    return v if v is not None else default


def search_pages(query, d1, d2, front_pages_only=False):
    """All page-level hits for a query inside a date window."""
    hits, sp = [], 1
    while True:
        params = {"q": query, "dates": f"{d1}/{d2}", "dl": "page", "fo": "json",
                  "c": 160, "sp": sp, "at": "results,pagination"}
        if front_pages_only:
            params["front_pages_only"] = "true"
        data = fetch_json(LOC_SEARCH + "?" + urllib.parse.urlencode(params))
        if not data:
            log(f"    search failed at page {sp}; keeping {len(hits)} hits")
            break
        for r in data.get("results", []):
            img = [u for u in (r.get("image_url") or []) if "image-services/iiif/" in u]
            hits.append({
                "date": first(r.get("date"))[:10],
                "lccn": first(r.get("number_lccn")),
                "paper": first(r.get("partof_title")),
                "city": first(r.get("location_city")),
                "county": first(r.get("location_county")),
                "state": first(r.get("location_state")),
                "frequency": first(r.get("publication_frequency")),
                "page": first(r.get("number_page")).lstrip("0") or "1",
                "url": r.get("url") or r.get("id", ""),
                "iiif": img[0].split("/full/")[0] if img else "",
            })
        pag = data.get("pagination") or {}
        if sp == 1:
            log(f"    '{query}': {pag.get('of', 0)} pages")
        if not pag.get("next"):
            break
        sp += 1
    return hits


# Page text is stored as ALTO XML next to the page image, under the image's service id.
# Two URL forms are tried, then the page's own JSON record.

def alto_urls(hit):
    urls = []
    if hit["iiif"]:
        sid = hit["iiif"].split("image-services/iiif/")[1]
        path = urllib.parse.unquote(sid).replace("service:", "", 1).replace(":", "/")
        urls.append(f"https://tile.loc.gov/storage-services/service/{path}.xml")
        urls.append("https://tile.loc.gov/text-services/word-coordinates-service?"
                    + urllib.parse.urlencode({"segment": f"/service/{path}.xml", "format": "alto_xml"}))
    return urls


def _find_text_links(obj, found):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and ("text-services" in v or v.endswith(".xml") or v.endswith("ocr.txt")
                                      or "fulltext" in k.lower() or "full_text" in k.lower()):
                found.append((k, v))
            else:
                _find_text_links(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _find_text_links(v, found)


STRATEGY = {"name": None}


def parse_alto(xml_text):
    """-> (words, page_w, page_h); words = [(text, hpos, vpos, w, h, block_id)]"""
    root = ET.fromstring(xml_text.encode("utf-8"))
    for el in root.iter():
        if isinstance(el.tag, str):
            el.tag = el.tag.split("}", 1)[-1]
    page = root.find(".//Page")
    pw = float(page.get("WIDTH", 0) or 0) if page is not None else 0
    ph = float(page.get("HEIGHT", 0) or 0) if page is not None else 0
    words = []
    for bi, block in enumerate(root.iter("TextBlock")):
        bid = block.get("ID") or f"b{bi}"
        for st in block.iter("String"):
            c = st.get("CONTENT")
            if c:
                words.append((c, float(st.get("HPOS", 0) or 0), float(st.get("VPOS", 0) or 0),
                              float(st.get("WIDTH", 0) or 0), float(st.get("HEIGHT", 0) or 0), bid))
    return words, pw, ph


def get_page_words(hit):
    tries = alto_urls(hit)
    if STRATEGY["name"] == "json":
        tries = []
    for u in tries:
        t = fetch(u, ".xml")
        if t and "<String" in t:
            try:
                STRATEGY["name"] = STRATEGY["name"] or ("storage" if "storage-services" in u else "wordcoords")
                return parse_alto(t)
            except ET.ParseError:
                continue
    # otherwise look for a text link in the page's JSON record
    page_json = fetch_json(hit["url"] + ("&" if "?" in hit["url"] else "?") + "fo=json")
    links = []
    _find_text_links(page_json, links)
    for k, v in links:
        if v.startswith("http") and ("text-services" in v or v.endswith(".xml")):
            v = re.sub(r"([?&])q=[^&]*&?", r"\1", v).rstrip("&?")
            t = fetch(v, ".xml")
            if t and "<String" in t:
                STRATEGY["name"] = STRATEGY["name"] or "json"
                try:
                    return parse_alto(t)
                except ET.ParseError:
                    pass
        elif isinstance(v, str) and len(v) > 200 and not v.startswith("http"):
            return [(w, 0, 0, 0, 0, "b0") for w in v.split()], 0, 0
    return None


def normalise(words):
    """Join OCR words into searchable text, keeping each word's character offset.
    Long s is read as s; words split by a hyphen at a line end are rejoined."""
    buf, offsets = "", []
    for w in words:
        t = w[0].lower().replace("\u017f", "s")
        if buf.endswith("-") and len(buf) > 1 and buf[-2].isalpha():
            buf = buf[:-1]
            offsets.append(len(buf))
            buf += t
        else:
            if buf:
                buf += " "
            offsets.append(len(buf))
            buf += t
    return buf, offsets


def match_in_page(words, pw, ph, rx):
    text, offsets = normalise(words)
    m = rx.search(text)
    if not m:
        return None
    # word index of the match start
    wi = max(0, next((i for i, o in enumerate(offsets) if o > m.start()), len(offsets)) - 1)
    a, b = max(0, m.start() - 220), min(len(text), m.end() + 220)
    snippet = ("…" if a else "") + text[a:b].strip() + ("…" if b < len(text) else "")
    box = ""
    if pw and ph and words[wi][3]:
        bid = words[wi][5]
        blk = [w for w in words if w[5] == bid]
        x0 = min(w[1] for w in blk); y0 = min(w[2] for w in blk)
        x1 = max(w[1] + w[3] for w in blk); y1 = max(w[2] + w[4] for w in blk)
        # keep the crop to roughly 12 lines around the match
        line_h = max(words[wi][4], 1)
        y0 = max(y0, words[wi][2] - 5 * line_h); y1 = min(y1, words[wi][2] + 7 * line_h)
        pad = line_h * 0.8
        box = ",".join(f"{v:.2f}" for v in (
            max(0, (x0 - pad) / pw * 100), max(0, (y0 - pad) / ph * 100),
            min(100, (x1 - x0 + 2 * pad) / pw * 100), min(100, (y1 - y0 + 2 * pad) / ph * 100)))
    return {"match": m.group(0)[:120], "snippet": snippet, "box_pct": box}


def step_papers(only_event=None):
    out_dir = RAW / "newspapers"
    out_dir.mkdir(parents=True, exist_ok=True)
    for ev in EVENTS["newspaper_events"]:
        if only_event and ev["id"] != only_event:
            continue
        log(f"\nNewspapers: {ev['label']} ({ev['date']})")
        rx = re.compile(ev["verify"])
        hits = {}
        for q in ev["queries"]:
            for h in search_pages(q, ev["first_valid"], ev["window_end"], ev.get("front_pages_only", False)):
                if h["lccn"] and h["date"]:
                    hits.setdefault(h["url"], h)
        log(f"  {len(hits)} distinct candidate pages")
        with (out_dir / f"{ev['id']}_candidates.csv").open("w", newline="", encoding="utf-8") as f:
            fields = ["date", "lccn", "paper", "city", "county", "state", "frequency", "page", "url", "iiif"]
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(hits.values())

        by_paper = {}
        for h in hits.values():
            by_paper.setdefault(h["lccn"], []).append(h)
        rows = []
        for n, (lccn, pages) in enumerate(sorted(by_paper.items()), 1):
            pages.sort(key=lambda h: (h["date"], int(h["page"]) if h["page"].isdigit() else 99))
            row = {**{k: pages[0][k] for k in ("lccn", "paper", "city", "county", "state", "frequency")},
                   "event": ev["id"], "first_hit_date": pages[0]["date"], "candidates": len(pages),
                   "verified": "no", "date": "", "page": "", "url": "", "iiif": "",
                   "match": "", "snippet": "", "box_pct": "", "pages_read": 0}
            dates_seen, text_found = set(), False
            for h in pages[:MAX_CANDIDATES]:
                dates_seen.add(h["date"])
                if len(dates_seen) > MAX_DATES:
                    break
                got = get_page_words(h)
                row["pages_read"] += 1
                if not got:
                    continue
                text_found = True
                words, pw, ph = got
                m = match_in_page(words, pw, ph, rx)
                if m:
                    row.update(verified="yes", date=h["date"], page=h["page"], url=h["url"], iiif=h["iiif"], **m)
                    break
            if not text_found:
                row.update(verified="no_text", date=pages[0]["date"], page=pages[0]["page"],
                           url=pages[0]["url"], iiif=pages[0]["iiif"])
            rows.append(row)
            if n % 20 == 0 or n == len(by_paper):
                ok = sum(r["verified"] == "yes" for r in rows)
                log(f"  {n}/{len(by_paper)} newspapers read, {ok} confirmed (text via {STRATEGY['name']})")
        path = out_dir / f"{ev['id']}.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["lccn"])
            w.writeheader()
            w.writerows(rows)
        log(f"  wrote {path.relative_to(ROOT)}")


def probe():
    log(f"Probe: one search, one page of text (log: {LOGFILE})")
    params = {"q": "lincoln assassinated", "dates": "1865-04-15/1865-04-16", "dl": "page", "fo": "json",
              "c": 20, "at": "results,pagination"}
    data = fetch_json(LOC_SEARCH + "?" + urllib.parse.urlencode(params))
    if not data or not data.get("results"):
        log("  FAILED: Library of Congress search returned nothing")
        log(f"  transport: {'curl' if CURL else 'python urllib'}; python {sys.version.split()[0]}")
        for k, v in LAST_ERROR.items():
            log(f"  {k}: {str(v)[:400]}")
        log(f"  log file: {LOGFILE}")
        sys.exit(1)
    log(f"  search ok: {data['pagination'].get('of')} pages for 15-16 April 1865")
    r = data["results"][0]
    hit = {"url": r.get("url", ""), "iiif": "", "page": "1"}
    img = [u for u in (r.get("image_url") or []) if "image-services/iiif/" in u]
    if img:
        hit["iiif"] = img[0].split("/full/")[0]
    log(f"  first hit: {first(r.get('partof_title'))}, {first(r.get('date'))}")
    got = get_page_words(hit)
    if not got:
        log("  FAILED: could not read the page text.")
        log("  image_url:", r.get("image_url"))
        sys.exit(1)
    words, pw, ph = got
    m = match_in_page(words, pw, ph, re.compile(EVENTS["newspaper_events"][4]["verify"]))
    log(f"  page text ok via {STRATEGY['name']}: {len(words)} words, page {pw:.0f}x{ph:.0f}")
    log(f"  match: {m['match'] if m else None} | crop: {m['box_pct'] if m else ''}")
    log("Probe passed.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["gazetteer", "papers"])
    ap.add_argument("--event")
    ap.add_argument("--probe", action="store_true", help="quick check that search and page text both work")
    a = ap.parse_args()
    if a.probe:
        return probe()
    started = time.time()
    if a.only in (None, "gazetteer"):
        step_gazetteer()
    if a.only == "papers":
        step_papers(a.event)
    log(f"\nDone in {(time.time() - started) / 60:.0f} min. Cached responses are in data/raw/cache/.")


if __name__ == "__main__":
    main()
