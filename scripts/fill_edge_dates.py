#!/usr/bin/env python3
"""Give dateless edges a sensible time window from the band's lifespan.

MusicBrainz memberships often lack begin/end years. Rather than let those edges
be "always on" (which flattens the time-scrubbing that makes this graph worth
building), we bound them to the band's own [formed, dissolved] years when known.

Idempotent and conservative: only touches edges whose intervals are empty, and
only when a band endpoint has a known start year. Dry-run by default.

  python3 scripts/fill_edge_dates.py           # preview
  python3 scripts/fill_edge_dates.py --write
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "data" / "graph"


def main() -> int:
    write = "--write" in sys.argv
    nodes = json.loads((GRAPH / "nodes.json").read_text())
    edges = json.loads((GRAPH / "edges.json").read_text())
    by_id = {n["id"]: n for n in nodes}

    filled = still_empty = 0
    for e in edges:
        if e.get("intervals"):
            continue
        # Prefer the band endpoint's lifespan; fall back to either endpoint.
        band = None
        for side in (e["target"], e["source"]):
            n = by_id.get(side)
            if n and n.get("kind") == "band" and n.get("born"):
                band = n
                break
        if not band:
            for side in (e["source"], e["target"]):
                n = by_id.get(side)
                if n and n.get("born"):
                    band = n
                    break
        if band and band.get("born"):
            e["intervals"] = [[band["born"], band.get("died")]]
            filled += 1
        else:
            still_empty += 1

    print(f"{filled} edges dated from band lifespan, {still_empty} left always-on "
          f"(no year anywhere).")
    if not write:
        print("(dry run -- pass --write to apply)")
        return 0
    (GRAPH / "edges.json").write_text(json.dumps(edges, indent=2) + "\n")
    print("written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
