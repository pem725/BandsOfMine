#!/usr/bin/env python3
"""Remove non-artist nodes and rename compilations that ARE a real artist.

The local-library importer treats every top-level folder as an artist, so
compilation folders ("Various Artists", "The Rolling Stone Collection 1969")
and rip artifacts ("RP85G1~F", "Unknown Album") became fake artist nodes.
Most have no artist behind them -> delete and add to seed_exclude so a re-scan
won't recreate them. A few are the ONLY copy of a real artist the user owns
(the "Segovia Collection" is Andrés Segovia; a Mangione "best of" is Chuck
Mangione) -> rename to the real artist instead of deleting.

  python3 scripts/prune_and_fix.py            # dry run
  python3 scripts/prune_and_fix.py --write
"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "data" / "graph"
EXCLUDE = GRAPH.parent / "seed_exclude.txt"

# pure junk: no real artist behind them -> delete + exclude from future imports
PRUNE = [
    "various-artists", "unknown-album", "kidmisc", "rp85g1-f", "hr5mb4-8", "pvdo1-9",
    "new-music-sampler-harp-magazine-2004",
    "the-rolling-stone-collection-1977-1982", "the-rolling-stone-collection-1967-1969",
    "the-rolling-stone-collection-1973-1977", "rolling-stone-collection1986-1992",
    "rolling-stone-collection-1969-1970",
]

# compilation node -> the real artist it actually represents.
# {old_id: (new_id, name, kind, [genres], [also-merge these ids into it])}
RENAME = {
    "the-segovia-collection-vol-1": ("andres-segovia", "Andrés Segovia", "person", ["classical"],
        ["the-segovia-collection-vol-5-five-centuries-of-the-spanish-guitar"]),
    "20th-century-masters-the-millennium-collection-best-of-chuck-mangione":
        ("chuck-mangione", "Chuck Mangione", "person", ["jazz"], []),
}

TRACK_FIELDS = ["local_tracks", "play_weight", "local_weight"]


def main() -> int:
    write = "--write" in sys.argv
    nodes = {n["id"]: n for n in json.loads((GRAPH / "nodes.json").read_text())}
    edges = json.loads((GRAPH / "edges.json").read_text())

    drop = {j for j in PRUNE if j in nodes}
    remap = {}          # old_id -> new_id (rename + secondary merges)

    for old, (new, name, kind, genres, extra) in RENAME.items():
        if old not in nodes:
            continue
        n = nodes[old]
        n["id"], n["name"], n["kind"] = new, name, kind
        n["genres"] = genres
        for f in ("mbid",):
            n.pop(f, None)          # was the compilation's, not the artist's
        for sec in extra:           # fold sibling volumes in
            s = nodes.get(sec)
            if s:
                for f in TRACK_FIELDS:
                    if s.get(f):
                        n[f] = (n.get(f) or 0) + s[f]
                remap[sec] = new
        nodes.pop(old)
        nodes[new] = n
        remap[old] = new

    # rewrite edges: repoint renames, drop anything touching a pruned node
    new_edges, dropped = [], 0
    for e in edges:
        if e["source"] in drop or e["target"] in drop:
            dropped += 1; continue
        e = dict(e)
        e["source"] = remap.get(e["source"], e["source"])
        e["target"] = remap.get(e["target"], e["target"])
        if e["source"] == e["target"]:
            dropped += 1; continue
        new_edges.append(e)

    gone = drop | set(remap) - set(remap.values())
    new_nodes = [n for nid, n in nodes.items() if nid not in drop and nid not in (set(remap) - set(remap.values()))]

    print(f"pruned {len(drop)} junk nodes:")
    for j in sorted(drop):
        print(f"    - {j}")
    print(f"renamed {len(RENAME)} compilations to real artists:")
    for old, (new, name, *_ ) in RENAME.items():
        print(f"    {old}  ->  {new} ({name})")
    print(f"nodes: {len(nodes)+len(drop)-len(new_nodes)+len(new_nodes)} -> {len(new_nodes)}  edges: {len(edges)} -> {len(new_edges)} ({dropped} dropped)")

    if not write:
        print("\n(dry run -- pass --write)")
        return 0

    (GRAPH / "nodes.json").write_text(json.dumps(new_nodes, indent=2) + "\n")
    (GRAPH / "edges.json").write_text(json.dumps(new_edges, indent=2) + "\n")
    # keep future imports from recreating the junk
    existing = set()
    if EXCLUDE.exists():
        existing = {l.split("#", 1)[0].strip() for l in EXCLUDE.read_text().splitlines()}
    add = [j for j in PRUNE if j not in existing]
    if add:
        with EXCLUDE.open("a") as f:
            f.write("\n# 2026-07-30 compilation / rip-artifact folders (not artists)\n")
            f.write("\n".join(add) + "\n")
    print(f"\nwrote nodes.json + edges.json; added {len(add)} ids to seed_exclude.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
