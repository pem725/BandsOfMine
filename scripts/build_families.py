#!/usr/bin/env python3
"""Group your OWNED artists into families by the taproot they most share.

Each owned artist is filed under the influence-root it shares with the most of
your other owned artists — the gravitational center of its little cluster. The
result (data/graph/families.json) drives the families constellation view.

  python3 scripts/build_families.py            # preview
  python3 scripts/build_families.py --write
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "data" / "graph"
OWN_MIN = 15          # local tracks to count as owned enough to place
MIN_FAMILY = 2        # families smaller than this are folded into "Outliers"


def main() -> int:
    write = "--write" in sys.argv
    nodes = {n["id"]: n for n in json.loads((GRAPH / "nodes.json").read_text())}
    edges = json.loads((GRAPH / "edges.json").read_text())

    inf_out = defaultdict(set)
    for e in edges:
        if e["type"] == "influenced_by":
            inf_out[e["source"]].add(e["target"])

    owned = [nid for nid, n in nodes.items()
             if (n.get("local_tracks") or 0) >= OWN_MIN and inf_out.get(nid)]

    # popularity of each root among owned artists
    rootpop = defaultdict(int)
    for o in owned:
        for r in inf_out[o]:
            rootpop[r] += 1

    fam = defaultdict(list)
    for o in owned:
        anchor = max(inf_out[o], key=lambda r: (rootpop[r], r))
        shared = sorted(inf_out[o], key=lambda r: -rootpop[r])
        genres = nodes[o].get("genres") or []
        fam[anchor].append({
            "id": o, "name": nodes[o]["name"],
            "tracks": nodes[o].get("local_tracks") or 0,
            "roots": [nodes[r]["name"] for r in shared[:5] if r in nodes],
            "genre": genres[0] if genres else None,     # primary genre for colouring
            "genres": genres[:4],
        })

    families, outliers = [], []
    for anchor, members in fam.items():
        entry = {
            "anchor": anchor,
            "name": nodes.get(anchor, {}).get("name", anchor),
            "members": sorted(members, key=lambda m: -m["tracks"]),
        }
        (families if len(members) >= MIN_FAMILY else outliers).append(entry)

    families.sort(key=lambda f: -len(f["members"]))
    # fold singleton families into one Outliers cluster
    if outliers:
        merged = [m for f in outliers for m in f["members"]]
        families.append({"anchor": "_outliers", "name": "Lone stars",
                          "members": sorted(merged, key=lambda m: -m["tracks"])})

    total = sum(len(f["members"]) for f in families)
    print(f"{len(families)} families covering {total} owned artists "
          f"(>= {OWN_MIN} local tracks)\n")
    for f in families:
        print(f"  {len(f['members']):>3}  {f['name']} family")

    if not write:
        print("\n(preview -- pass --write to save data/graph/families.json)")
        return 0
    (GRAPH / "families.json").write_text(
        json.dumps({"families": families}, indent=2) + "\n")
    print("\nwrote data/graph/families.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
