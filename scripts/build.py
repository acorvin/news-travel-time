"""Turn the collected records into the page's data and build the page.

    python scripts/build.py

Reads   data/raw/newspapers/<event>.csv, data/raw/web_sources.csv,
        data/raw/gazetteer/*.tsv (optional), data/reference/events.json
Writes  data/processed/arrivals.csv          one row per newspaper or news website
        data/processed/events_summary.csv    one row per event
        docs/data.json                       what the page draws
        docs/index.html                      the page (loads data.json)
        docs/standalone.html                 the same page with the data inlined

How a newspaper's delay is measured
  A newspaper page carries a date, not a time. So each arrival is a window: from the
  start of the day printed on the masthead (or the moment of the event, if later) to
  midnight at the end of that day, in the paper's local solar time. The page draws
  the window; summaries use its end, the latest moment the paper can have had the
  news, so every speed quoted is a lower bound.
"""
import csv
import html
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROC = ROOT / "data" / "processed"
DOCS = ROOT / "docs"
TEMPLATE = ROOT / "src" / "page.template.html"
EVENTS = json.loads((ROOT / "data" / "reference" / "events.json").read_text(encoding="utf-8"))

MAX_DAYS = 120          # outer edge of the figure

STATE_USPS = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "district of columbia": "DC", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY", "puerto rico": "PR", "virgin islands": "VI",
}
# Approximate centre of each state, used when neither the town nor the county can be matched.
STATE_CENTRE = {
    "AL": (32.8, -86.8), "AK": (61.4, -152.3), "AZ": (34.2, -111.6), "AR": (34.9, -92.4), "CA": (37.2, -119.5),
    "CO": (39.0, -105.5), "CT": (41.6, -72.7), "DE": (39.0, -75.5), "DC": (38.9, -77.0), "FL": (28.6, -82.4),
    "GA": (32.7, -83.4), "HI": (20.8, -156.3), "ID": (44.4, -114.6), "IL": (40.0, -89.2), "IN": (39.9, -86.3),
    "IA": (42.1, -93.5), "KS": (38.5, -98.4), "KY": (37.5, -85.3), "LA": (31.1, -92.0), "ME": (45.4, -69.2),
    "MD": (39.0, -76.8), "MA": (42.3, -71.8), "MI": (44.3, -85.4), "MN": (46.3, -94.3), "MS": (32.7, -89.7),
    "MO": (38.4, -92.5), "MT": (47.0, -109.6), "NE": (41.5, -99.8), "NV": (39.3, -116.6), "NH": (43.7, -71.6),
    "NJ": (40.2, -74.7), "NM": (34.4, -106.1), "NY": (42.9, -75.5), "NC": (35.6, -79.4), "ND": (47.5, -100.5),
    "OH": (40.3, -82.8), "OK": (35.6, -97.5), "OR": (43.9, -120.6), "PA": (40.9, -77.8), "RI": (41.7, -71.5),
    "SC": (33.9, -80.9), "SD": (44.4, -100.2), "TN": (35.9, -86.4), "TX": (31.5, -99.3), "UT": (39.3, -111.7),
    "VT": (44.1, -72.7), "VA": (37.5, -78.9), "WA": (47.4, -120.5), "WV": (38.6, -80.6), "WI": (44.6, -89.9),
    "WY": (43.0, -107.5), "PR": (18.2, -66.5), "VI": (18.3, -64.9),
}


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(a))


def _clean_place(s):
    s = (s or "").lower().strip()
    s = re.sub(r"\s*\(.*?\)", "", s)
    s = re.sub(r"\b(city|town|village|borough|cdp|municipality|consolidated government.*|"
               r"metropolitan government.*|unified government.*|urban county)\b", "", s)
    s = s.replace("saint ", "st. ").replace("st ", "st. ").replace("ft. ", "fort ").replace("mt. ", "mount ")
    return re.sub(r"\s+", " ", s).strip(" .,'")


def load_gazetteer():
    places, counties = {}, {}
    gp, gc = RAW / "gazetteer" / "places.tsv", RAW / "gazetteer" / "counties.tsv"
    if gp.exists():
        df = pd.read_csv(gp, sep="\t", dtype=str)
        df.columns = [c.strip() for c in df.columns]
        df["ALAND"] = pd.to_numeric(df["ALAND"], errors="coerce").fillna(0)
        for r in df.sort_values("ALAND").itertuples():   # larger places win ties
            places[(r.USPS, _clean_place(r.NAME))] = (float(r.INTPTLAT), float(r.INTPTLONG))
    if gc.exists():
        df = pd.read_csv(gc, sep="\t", dtype=str)
        df.columns = [c.strip() for c in df.columns]
        for r in df.itertuples():
            name = re.sub(r"\s+(county|parish|borough|census area|city and borough|municipality)$", "",
                          r.NAME.lower()).strip()
            counties[(r.USPS, name)] = (float(r.INTPTLAT), float(r.INTPTLONG))
    return places, counties


ALIASES = {("DC", "washington"): (38.8951, -77.0364), ("DC", "georgetown"): (38.9097, -77.0654),
           ("DC", "city of washington"): (38.8951, -77.0364)}


def geocode(city, county, state, places, counties):
    st = STATE_USPS.get((state or "").lower().strip())
    if not st:
        return None, None, "unmatched"
    c = _clean_place(city)
    if (st, c) in ALIASES:
        return (*ALIASES[(st, c)], "town")
    if (st, c) in places:
        return (*places[(st, c)], "town")
    cty = re.sub(r"\s+(county|parish)$", "", (county or "").lower().strip())
    if (st, cty) in counties:
        return (*counties[(st, cty)], "county")
    if st in STATE_CENTRE:
        return (*STATE_CENTRE[st], "state")
    return None, None, "unmatched"


def event_utc(ev):
    if "utc" in ev:
        return datetime.fromisoformat(ev["utc"].replace("Z", "+00:00"))
    local = datetime.fromisoformat(f"{ev['date']}T{ev['time_local']}:00")
    return (local - timedelta(hours=ev["utc_offset"])).replace(tzinfo=timezone.utc)


def day_window_utc(issue_date, lon):
    """Start and end (UTC) of a printed date in the paper's local solar time."""
    d = datetime.fromisoformat(issue_date).replace(tzinfo=timezone.utc)
    off = timedelta(hours=lon / 15.0) if lon is not None else timedelta(hours=-5)
    return d - off, d + timedelta(days=1) - off


SMALL = {"a", "an", "and", "the", "of", "in", "on", "at", "for", "to", "de", "la", "le", "und", "der"}


def paper_name(raw):
    """'the kentucky gazette (lexington [ky.]) 1789-1803' -> 'The Kentucky Gazette'."""
    name = re.split(r"\s*[\(\[]", raw or "", maxsplit=1)[0]
    name = re.sub(r"\s+\d{4}-(\d{4}|\d{2}\?\?|current)?.*$", "", name).strip(" .,:;")
    words = name.split()
    return " ".join(w if any(ch.isupper() for ch in w) else
                    (w if i and w in SMALL else w[:1].upper() + w[1:]) for i, w in enumerate(words))


def newspapers(places, counties):
    rows = []
    ex = ROOT / "data" / "reference" / "newspaper_exclusions.csv"
    skip = set(zip(*[pd.read_csv(ex, dtype=str)[c] for c in ("event", "lccn")])) if ex.exists() else set()
    for ev in EVENTS["newspaper_events"]:
        p = RAW / "newspapers" / f"{ev['id']}.csv"
        if not p.exists():
            print(f"  missing {p.relative_to(ROOT)}")
            continue
        t0 = event_utc(ev)
        df = pd.read_csv(p, dtype=str).fillna("")
        for r in df.itertuples():
            if r.verified not in ("yes", "search") or (ev["id"], r.lccn) in skip:
                continue
            lat, lon, prec = geocode(r.city, r.county, r.state, places, counties)
            start, end = day_window_utc(r.date, lon)
            start = max(start, t0)
            if end <= t0:
                continue
            h_lo = (start - t0).total_seconds() / 3600
            h_hi = (end - t0).total_seconds() / 3600
            if h_hi / 24 > MAX_DAYS:
                continue
            km = haversine_km(ev["lat"], ev["lon"], lat, lon) if lat is not None else None
            rows.append({
                "event": ev["id"], "kind": "newspaper", "name": paper_name(r.paper), "place": f"{r.city.title()}, {r.state.title()}",
                "lat": lat, "lon": lon, "geo_precision": prec, "km": km, "frequency": r.frequency,
                "printed": r.date, "h_lo": h_lo, "h_hi": h_hi, "url": r.url, "iiif": r.iiif,
                "box_pct": r.box_pct, "match": r.match, "snippet": r.snippet, "lang": "en",
            })
    return rows


BOILERPLATE = re.compile(r"security check|just a moment|checking your browser|captcha|access denied|log ?in|inloggen|"
                         r"sign in|subscribe|not found|\b404\b|page unavailable|^home\b|^news$|^world news$|^local news$", re.I)


def headline_ok(t):
    """A page title that reads as a headline, not the site's name or a bot check."""
    if not t or "\ufffd" in t or BOILERPLATE.search(t):
        return False
    core = re.split(r"\s+[|\u2013\u2014-]\s+[^|\u2013\u2014-]*$", t.strip(" |"))[0]   # drop a trailing " | Site name"
    return len(core.split()) >= 4 or len(re.findall(r"[\u3040-\u9fff\uac00-\ud7af\u0e00-\u0e7f]", core)) >= 8


def slug_words(url):
    """The words in an article's address, which name the article even when the page has moved."""
    parts = [p for p in re.split(r"[/?#]", url.split("://", 1)[-1])[1:] if re.search(r"[a-z]{3,}-[a-z]{3,}", p, re.I)]
    if not parts:
        return []
    words = re.sub(r"\.(s?html?|php|aspx?)$", "", max(parts, key=len))
    words = re.sub(r"^\d+\.", "", words).replace("_", "-").split("-")
    return [w for w in words if w and not re.fullmatch(r"\d+|[a-z]?\d[\da-z]*", w, re.I)]


def readable_title(title, url, page_title="", page_timed=False):
    """The article's title from GDELT, or the headline on the page, or failing that the words in its address."""
    t = html.unescape(str(title or "")).strip()
    if t and t.lower() != "nan" and "\ufffd" not in t and not BOILERPLATE.search(t):
        return t
    words = slug_words(url)
    if headline_ok(page_title):
        # a moved article often redirects to the front page, so the page title is used only when the page
        # also gave a publication time, shares a word with the address, or the address has no words
        shared = {w.lower() for w in words if len(w) >= 4} & {w.lower() for w in re.findall(r"\w{4,}", page_title)}
        if page_timed or shared or len(words) < 3 or not re.search(r"[A-Za-z]", page_title):
            return page_title.strip(" |")
    t = " ".join(words)
    return "" if not t or BOILERPLATE.search(t) else t[:1].upper() + t[1:]


def web():
    p = RAW / "web_sources.csv"
    if not p.exists():
        print("  missing data/raw/web_sources.csv (run scripts/collect_web.py)")
        return []
    df = pd.read_csv(p, dtype=str).fillna("")
    rows = []
    for ev in EVENTS["web_events"]:
        t0 = event_utc(ev)
        for r in df[df.event == ev["id"]].itertuples():
            # the page's own publication time if it has one, otherwise the later time GDELT found it
            stamp = (getattr(r, "published_utc", "") or "") or r.first_seen_utc
            src = "page" if getattr(r, "published_utc", "") else "gdelt"
            t = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            h = (t - t0).total_seconds() / 3600
            if h < 0 or h / 24 > MAX_DAYS:
                continue
            rows.append({
                "event": ev["id"], "kind": "web", "name": r.site, "place": "",
                "lat": None, "lon": None, "geo_precision": src, "km": None,
                "frequency": "", "printed": stamp, "h_lo": h if src == "page" else max(0.0, h - 0.25), "h_hi": h,
                "url": r.url, "iiif": "", "box_pct": "", "match": "", "snippet": readable_title(r.title, r.url, getattr(r, "page_title", ""), src == "page"), "lang": r.lang,
            })
    return rows


WEB_DRAWN = 600   # sites drawn per web event
DAILY_FROM = 1845   # from here on, the half-way figures count daily papers only
MIN_DAILIES = 5     # and an event needs at least this many of them


def is_daily(freq):
    return "daily" in str(freq or "").lower()


def summarise(arr, ev):
    # A weekly prints the news on its next issue day, whatever the wire did, so where an event has
    # enough daily papers the headline figures count those; every paper is still drawn.
    all_h = arr["h_hi"]
    basis = "all"
    full = arr
    if "utc" in ev:
        # websites: count those whose own page gives a publication time; the rest carry
        # GDELT's later time and are drawn but not counted
        timed = arr[arr["geo_precision"] == "page"]
        if len(timed) >= 30:
            arr, basis = timed, "page"
    else:
        daily = arr[arr["frequency"].map(is_daily)]
        if event_utc(ev).year >= DAILY_FROM and len(daily) >= MIN_DAILIES:
            arr, basis = daily, "daily"
    h = arr["h_hi"].sort_values().to_numpy()
    n = len(h)
    q = lambda p: float(pd.Series(h).quantile(p)) if n else None
    far = arr[arr["km"].fillna(0) >= 300]
    speed = None
    if len(far) >= 5:   # km per day, lower bound, median over papers 300 km or more away
        speed = float((far["km"] / (far["h_hi"] / 24)).median())
    first = full.sort_values(["h_hi", "h_lo"]).iloc[0] if n else None
    last = full.sort_values("h_hi").iloc[-1] if n else None
    return {
        "id": ev["id"], "n": int(len(all_h)), "n_basis": n, "basis": basis,
        "median_all_h": float(all_h.median()) if len(all_h) else None, "median_h": q(0.5), "p10_h": q(0.1), "p90_h": q(0.9),
        "min_h": float(full["h_hi"].min()) if n else None, "max_h": float(full["h_hi"].max()) if n else None,
        "speed_kmd": speed,
        "first_name": first["name"] if n else "", "first_place": first["place"] if n else "",
        "last_name": last["name"] if n else "", "last_place": last["place"] if n else "",
    }


def survival(arr, grid):
    """Share of the event's papers or sites that had not yet carried it, at each grid time."""
    h = arr["h_hi"].to_numpy()
    n = len(h)
    return [round(float((h > g).sum()) / n, 4) if n else None for g in grid]


def main():
    PROC.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
    places, counties = load_gazetteer()
    print(f"Gazetteer: {len(places)} places, {len(counties)} counties")
    rows = newspapers(places, counties)
    wrows = web()
    df = pd.DataFrame(rows + wrows)
    df.to_csv(PROC / "arrivals.csv", index=False)
    print(f"Arrivals: {len(df)} ({df.groupby('event').size().to_dict()})")
    if len(df):
        print("Geocoding precision:", df[df.kind == "newspaper"].geo_precision.value_counts().to_dict())

    grid = [round(10 ** (x / 20), 4) for x in range(-40, int(20 * math.log10(MAX_DAYS * 24)) + 2)]  # hours, log-spaced
    by_id = {e["id"]: e for e in EVENTS["newspaper_events"] + EVENTS["web_events"]}
    group_of = {i: era["group"] for era in EVENTS["eras"] for i in era["events"]}
    era_of = {i: n for n, era in enumerate(EVENTS["eras"]) for i in era["events"]}
    all_events = [by_id[i] for era in EVENTS["eras"] for i in era["events"] if i in by_id]
    events_out, summ = [], []
    for ev in all_events:
        arr = df[df.event == ev["id"]] if len(df) else df
        s = summarise(arr, ev) if len(arr) else {"id": ev["id"], "n": 0}
        summ.append(s)
        t0 = event_utc(ev)
        events_out.append({
            **{k: ev[k] for k in ("id", "label", "short", "origin", "lat", "lon", "carrier")},
            "kind": "web" if "utc" in ev else "newspaper", "era": era_of[ev["id"]],
            "group": group_of[ev["id"]],
            "utc": t0.isoformat().replace("+00:00", "Z"), "year": t0.year,
            "date": ev.get("date") or t0.date().isoformat(),
            "time_local": ev.get("time_local", ""),
            "time_text": ev.get("time_text", ""), "context": ev.get("context", ""), "phrase": ev.get("phrase", ""), "q": sorted({w for q in ev.get("queries", []) for w in q.split() if len(w) > 3}),
            "announced_late": bool(ev.get("public_utc") and ev["public_utc"] != ev.get("utc")),
            **{k: v for k, v in s.items() if k != "id"},
            "survival": survival(arr, grid) if len(arr) else [],
            **public_figures(ev, arr),
        })
    pd.DataFrame(summ).to_csv(PROC / "events_summary.csv", index=False)

    ev_index = {e["id"]: i for i, e in enumerate(events_out)}
    pts = []
    # each web plume draws a fixed random sample of its sites; the figures are computed from all of them
    if len(df):
        web_rows = df[df.kind == "web"]
        drawn = pd.concat([g.sample(n=min(len(g), WEB_DRAWN), random_state=1)
                           for _, g in web_rows.groupby("event")]) if len(web_rows) else web_rows
        df_drawn = pd.concat([df[df.kind != "web"], drawn])
    else:
        df_drawn = df
    order = df_drawn.sort_values(["event", "km", "h_hi"], na_position="last") if len(df_drawn) else df_drawn
    for r in order.itertuples():
        pts.append([
            ev_index[r.event], round(r.h_lo, 3), round(r.h_hi, 3),
            None if pd.isna(r.km) else round(r.km), r.name, r.place, r.printed,
            r.url, r.iiif, r.box_pct, (r.snippet or "")[:300], r.match, r.frequency, r.lang,
            r.geo_precision,
        ])
    payload = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "max_days": MAX_DAYS,
        "grid_h": grid,
        "eras": [{"name": era["name"], "group": era["group"]} for era in EVENTS["eras"]],
        "events": events_out,
        "fields": ["e", "h_lo", "h_hi", "km", "name", "place", "printed", "url", "iiif", "box", "snippet",
                   "match", "freq", "lang", "geo"],
        "points": pts,
    }
    js = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=str)
    (DOCS / "data.json").write_text(js, encoding="utf-8")
    page = TEMPLATE.read_text(encoding="utf-8")
    (DOCS / "index.html").write_text(page, encoding="utf-8")
    inline = page.replace("/*INLINE_DATA*/null", js.replace("</", "<\\/"))
    (DOCS / "standalone.html").write_text(inline, encoding="utf-8")
    print(f"Wrote docs/index.html, docs/standalone.html, docs/data.json "
          f"({(DOCS / 'data.json').stat().st_size / 1e6:.2f} MB)")
    write_readme_table(events_out)
    for s in summ:
        if s.get("n"):
            print(f"  {s['id']:<20} n={s['n']:<4} median {s['median_h'] / 24:7.2f} d"
                  f"  p90 {s['p90_h'] / 24:7.2f} d  speed {s['speed_kmd'] or 0:8.0f} km/d")


def public_figures(ev, arr):
    """For an event made public later than it happened: the delay, and how many sites had it two hours after."""
    if not ev.get("public_utc") or ev["public_utc"] == ev.get("utc"):
        return {}
    lag = (datetime.fromisoformat(ev["public_utc"].replace("Z", "+00:00")) - event_utc(ev)).total_seconds() / 3600
    return {"public_lag_h": round(lag, 3), "n_2h_after_public": int((arr["h_hi"] <= lag + 2).sum()) if len(arr) else 0}


def dur_text(h):
    if h < 1:
        return f"{max(1, round(h * 60))} minutes"
    if h < 36:
        return f"{round(h)} hours"
    return f"{round(h / 24)} days"


def write_readme_table(events_out):
    """The figures table in README.md, between the SUMMARY markers."""
    readme = ROOT / "README.md"
    if not readme.exists():
        return
    lines = ["<!--SUMMARY-->", "| Event | Year | Sources | Half had it within |", "|---|---|---|---|"]
    for e in events_out:
        if not e.get("n"):
            continue
        if e["kind"] == "web":
            src = f"{e['n']:,} news websites" + (f", {e['n_basis']:,} with a page time" if e["basis"] == "page" else "")
        else:
            src = f"{e['n']:,} newspapers" + (f", {e['n_basis']} daily" if e["basis"] == "daily" else "")
        half = dur_text(e["median_h"]) + {"daily": " (dailies)", "page": " (page times)"}.get(e["basis"], "")
        lines.append(f"| {e['label']} | {e['year']} | {src} | {half} |")
    lines.append("<!--/SUMMARY-->")
    text = readme.read_text(encoding="utf-8")
    text = re.sub(r"<!--SUMMARY-->.*?(<!--/SUMMARY-->|$)", "\n".join(lines).replace("\\", "\\\\"), text, count=1, flags=re.S) \
        if "<!--/SUMMARY-->" in text else text.replace("<!--SUMMARY-->", "\n".join(lines))
    readme.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
