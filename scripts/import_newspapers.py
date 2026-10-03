"""Turn the browser collector's download into one table per event.

    python scripts/import_newspapers.py newspapers.json [more.json ...]

Each file holds {"rows": {event_id: [paper, ...]}}. When several files are given, an event
found in a later file replaces the same event from an earlier one, so a re-run of a single
event can be layered over the full collection.
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "raw" / "newspapers"
COLS = ["lccn", "paper", "city", "county", "state", "frequency", "event", "first_hit_date", "candidates",
        "verified", "date", "page", "url", "iiif", "match", "snippet", "box_pct", "pages_read"]


def main(paths):
    events = {}
    for p in paths:
        rows = json.loads(Path(p).read_text())["rows"]
        for ev, papers in rows.items():
            events[ev] = (papers, p)
    OUT.mkdir(parents=True, exist_ok=True)
    for ev, (papers, src) in sorted(events.items()):
        df = pd.DataFrame(papers).reindex(columns=COLS).fillna("")
        df.to_csv(OUT / f"{ev}.csv", index=False)
        counts = df["verified"].value_counts().to_dict()
        print(f"{ev:<20} {len(df):>4} papers  {counts}  from {Path(src).name}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
