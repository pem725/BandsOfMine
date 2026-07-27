#!/usr/bin/env python3
"""Surgically remove artists that aren't yours (e.g. streamed by someone else).

Removes a given set of artists and every edge touching them, then cascade-prunes
ONLY the influencer nodes that become true orphans — i.e. nodes that existed
solely to be a root of the removed artists. Shared roots that your real music
still cites (Stevie Wonder, Curtis Mayfield, Muddy Waters...) are preserved.

The removed slugs are appended to data/graph/not_mine.txt so importers won't
re-add them. Reversible in spirit: the list is the record of what was excised.

  python3 scripts/excise_artists.py slug1 slug2 ...          # preview
  python3 scripts/excise_artists.py --file data/graph/excise_hiphop.txt --write
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "data" / "graph"
NOTMINE = GRAPH / "not_mine.txt"


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--write"]
    write = "--write" in sys.argv
    targets = []
    if args and args[0] == "--file":
        targets = [ln.split("#", 1)[0].strip() for ln in Path(args[1]).read_text().splitlines()
                   if ln.split("#", 1)[0].strip()]
    else:
        targets = args
    targets = set(targets)

    nodes = json.loads((GRAPH / "nodes.json").read_text())
    edges = json.loads((GRAPH / "edges.json").read_text())
    by_id = {n["id"]: n for n in nodes}

    missing = [t for t in targets if t not in by_id]
    if missing:
        print(f"  (not in graph, ignored: {', '.join(missing)})")
    targets = {t for t in targets if t in by_id}

    # 1. drop the target artists and every edge touching them
    kept_edges = [e for e in edges if e["source"] not in targets and e["target"] not in targets]

    # 2. cascade: a node that HAD edges before but has ZERO after (all its ties
    #    went to removed artists), isn't owned, and is only an influencer node
    #    -> it existed solely for them, so prune it. Pre-existing edgeless
    #    orphans (stray classical/compilation nodes) are left untouched.
    pre = defaultdict(int)
    for e in edges:
        pre[e["source"]] += 1
        pre[e["target"]] += 1
    post = defaultdict(int)
    for e in kept_edges:
        post[e["source"]] += 1
        post[e["target"]] += 1
    orphaned = []
    for n in nodes:
        nid = n["id"]
        if nid in targets:
            continue
        if pre[nid] > 0 and post[nid] == 0 and not (n.get("local_tracks") or n.get("play_weight")) \
                and n.get("sources", []) and set(n["sources"]) <= {"wikidata", "web-curated", "musicbrainz"}:
            orphaned.append(nid)
    orphan_set = set(orphaned)

    kept_nodes = [n for n in nodes if n["id"] not in targets and n["id"] not in orphan_set]

    print(f"removing {len(targets)} artists + {len(orphaned)} cascaded orphan roots")
    print(f"  nodes {len(nodes)} -> {len(kept_nodes)}   edges {len(edges)} -> {len(kept_edges)}")
    print("  removed artists: " + ", ".join(sorted(by_id[t]['name'] for t in targets)))
    if orphaned:
        print("  cascaded orphans: " + ", ".join(sorted(by_id[o]['name'] for o in orphaned)[:30])
              + (" ..." if len(orphaned) > 30 else ""))

    if not write:
        print("\n(preview -- pass --write to apply)")
        return 0

    (GRAPH / "nodes.json").write_text(json.dumps(kept_nodes, indent=2) + "\n")
    (GRAPH / "edges.json").write_text(json.dumps(kept_edges, indent=2) + "\n")
    existing = set()
    if NOTMINE.exists():
        existing = {ln.strip() for ln in NOTMINE.read_text().splitlines() if ln.strip() and not ln.startswith("#")}
    with NOTMINE.open("a") as f:
        if not NOTMINE.exists() or NOTMINE.stat().st_size == 0:
            f.write("# Artists excised as not-mine (streamed by others). Importers skip these.\n")
        for t in sorted(targets):
            if t not in existing:
                f.write(t + "\n")
    print(f"\nwritten. removed slugs appended to {NOTMINE.relative_to(ROOT)}")
    print("next: python3 scripts/validate_graph.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
