#!/usr/bin/env python3
"""Tidy up structural quirks that MusicBrainz expansion introduces.

MusicBrainz models some relations in ways that don't fit our person->band
membership model: artists related to themselves (aliases), band-to-band
"member of" links (offshoots/aliases), and the occasional solo artist typed as
a group. This normalizes them so the graph validates and reads cleanly.

Idempotent. Dry-run by default.
  python3 scripts/clean_graph.py
  python3 scripts/clean_graph.py --write
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "data" / "graph"

# Well-known solo artists MusicBrainz/our resolver mis-typed as bands.
FORCE_PERSON = {"dave-matthews", "bob-marley", "george-thorogood"}


def main() -> int:
    write = "--write" in sys.argv
    nodes = json.loads((GRAPH / "nodes.json").read_text())
    edges = json.loads((GRAPH / "edges.json").read_text())
    by_id = {n["id"]: n for n in nodes}

    kind_fixed = 0
    for nid in FORCE_PERSON:
        n = by_id.get(nid)
        if n and n.get("kind") != "person":
            n["kind"] = "person"
            kind_fixed += 1

    out = []
    dropped_selfloop = reclassified = dropped_bad = 0
    seen = set()
    for e in edges:
        src, tgt, etype = e["source"], e["target"], e["type"]
        s, t = by_id.get(src), by_id.get(tgt)

        if src == tgt:                      # self-loop (alias relation)
            dropped_selfloop += 1
            continue

        # Band "member of" band -> that's an offshoot/alias lineage.
        if etype == "member_of" and s and s.get("kind") == "band":
            if t and t.get("kind") == "band":
                etype = "spun_off_from"
                reclassified += 1
            else:
                dropped_bad += 1
                continue

        # "member of" a PERSON (e.g. a backing musician linked to the frontman
        # rather than the band) really means they played together.
        if etype == "member_of" and t and t.get("kind") == "person":
            etype = "collaborated_with"
            reclassified += 1

        # spun_off_from must point at a band; drop if not.
        if etype == "spun_off_from" and (not t or t.get("kind") != "band"):
            dropped_bad += 1
            continue

        key = (src, tgt, etype)
        if key in seen:                     # dedupe after reclassification
            continue
        seen.add(key)
        e["type"] = etype
        out.append(e)

    print(f"kinds corrected: {kind_fixed}  "
          f"self-loops dropped: {dropped_selfloop}  "
          f"band->band member_of reclassified as spun_off_from: {reclassified}  "
          f"invalid-endpoint edges dropped: {dropped_bad}")
    print(f"edges: {len(edges)} -> {len(out)}")

    if not write:
        print("(dry run -- pass --write to apply)")
        return 0
    (GRAPH / "nodes.json").write_text(json.dumps(nodes, indent=2) + "\n")
    (GRAPH / "edges.json").write_text(json.dumps(out, indent=2) + "\n")
    print("written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
