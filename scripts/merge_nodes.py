#!/usr/bin/env python3
"""Merge duplicate / mis-resolved artist nodes into one canonical node.

The local-library importer sometimes creates two nodes for one artist: your
owned *tracks* land on one (matched by folder name) while the real *members,
influences and collaborations* live on its twin. Worse, a folder occasionally
resolves to the wrong MusicBrainz artist entirely (your 72 "Eagles" tracks
matched a defunct UK band, not the LA Eagles that has Henley/Frey/Walsh).

Two modes, chosen per cluster after fact-checking each by hand:

  merge   -- same real artist split across nodes (sub-projects, "The X" vs "X").
            Union everything into the canonical: tracks, weights, sources,
            metadata, and repoint every edge. The absorbed node is deleted.

  resolve -- the track-bearing node is the WRONG artist. Move only the track
            data (counts/weights/spotify_id) to the correct canonical node,
            then delete the wrong node and any members that existed solely to
            populate it (orphans with no tracks and no other band).

  python3 scripts/merge_nodes.py            # dry-run report
  python3 scripts/merge_nodes.py --write
"""
import json, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "data" / "graph"

# canonical_id -> (mode, [absorbed_ids])   -- every id verified against the graph
PLAN = {
    # --- sub-project / "The X" vs "X" splits: union everything ---
    "10000-maniacs":            ("merge", ["10-000-maniacs"]),
    "the-allman-brothers-band": ("merge", ["allman-brothers-band"]),
    "ben-harper":               ("merge", ["ben-harper-the-innocent-criminals"]),
    "bruce-hornsby":            ("merge", ["bruce-hornsby-the-range", "bruce-hornsby-the-noisemakers"]),
    "the-clancy-brothers":      ("merge", ["clancy-brothers-and-the-dubliners"]),
    "david-bowie":              ("merge", ["david-bowie-the-buzz", "david-bowie-the-hype"]),
    "don-walser":               ("merge", ["don-walser-and-the-pure-texas-band"]),
    "duke-ellington":           ("merge", ["duke-ellington-the-jungle-band"]),
    "elvis-costello":           ("merge", ["elvis-costello-the-attractions", "elvis-costello-the-imposters"]),
    "eric-clapton":             ("merge", ["eric-clapton-and-the-powerhouse"]),
    "gladys-knight":            ("merge", ["gladys-knight-and-the-saints-unified-voices", "gladys-knight-the-pips"]),
    "glenn-miller":             ("merge", ["glenn-miller-and-the-army-air-force-band"]),
    "huey-lewis-the-news":      ("merge", ["huey-lewis"]),
    "joe-cocker":               ("merge", ["joe-cocker-the-crusaders"]),
    "john-hiatt":               ("merge", ["john-hiatt-the-guilty-dogs", "john-hiatt-the-goners"]),
    "keith-richards":           ("merge", ["keith-richards-and-the-xpensive-winos"]),
    "nitty-gritty-dirt-band":   ("merge", ["the-nitty-gritty-dirt-band"]),
    "ray-lamontagne":           ("merge", ["ray-lamontagne-the-pariah-dogs"]),
    "robert-plant":             ("merge", ["robert-plant-the-strange-sensation"]),
    "tom-petty":                ("merge", ["tom-petty-and-the-heartbreakers"]),
    # --- mis-resolved to the wrong band: move tracks only, drop the wrong node ---
    "eagles":                   ("resolve", ["the-eagles"]),
    "outlaws":                  ("resolve", ["the-outlaws"]),
    # --- 2026-07-30 second pass: spelling/punctuation variants of one act ---
    "the-clancy-brothers":      ("merge", ["the-clancy-brothers-tommy-makem",
                                           "clancy-brothers-with-tommy-makem",
                                           "the-clancy-brothers-and-makem"]),
    "crosby-stills-and-nash":   ("merge", ["crosby-stills-nash"]),
}

TRACK_FIELDS = ["local_tracks", "play_weight", "local_weight"]


def uniq(seq):
    out = []
    for x in seq:
        if x not in out:
            out.append(x)
    return out


def main() -> int:
    write = "--write" in sys.argv
    nodes = {n["id"]: n for n in json.loads((GRAPH / "nodes.json").read_text())}
    edges = json.loads((GRAPH / "edges.json").read_text())

    remap = {}                 # absorbed_id -> canonical_id (merge mode only)
    drop = set()               # nodes to delete outright (resolve wrong nodes + orphans)
    report = []

    # who points at whom (to find orphan members of a wrong node)
    inbound = defaultdict(list)
    for e in edges:
        inbound[e["target"]].append(e["source"])

    for canon, (mode, absorbed) in PLAN.items():
        if canon not in nodes:
            report.append(f"  SKIP {canon}: canonical missing"); continue
        cn = nodes[canon]
        for aid in absorbed:
            an = nodes.get(aid)
            if not an:
                report.append(f"  skip {aid}: already gone"); continue

            # move track data in both modes (that part is genuinely yours)
            for f in TRACK_FIELDS:
                if an.get(f):
                    cn[f] = (cn.get(f) or 0) + an[f]
            if an.get("spotify_id") and not cn.get("spotify_id"):
                cn["spotify_id"] = an["spotify_id"]
            cn["sources"] = uniq((cn.get("sources") or []) + (an.get("sources") or []))
            if an.get("seed"):
                cn["seed"] = True

            if mode == "merge":
                # union descriptive metadata, preferring canonical
                for f in ["born", "died", "origin", "mbid"]:
                    if not cn.get(f) and an.get(f):
                        cn[f] = an[f]
                cn["genres"] = uniq((cn.get("genres") or []) + (an.get("genres") or []))
                cn["roles"] = uniq((cn.get("roles") or []) + (an.get("roles") or []))
                remap[aid] = canon
                report.append(f"  merge   {aid:42} -> {canon}  (+{an.get('local_tracks') or 0} tracks)")
            else:  # resolve: wrong artist. delete it + members that exist only for it
                drop.add(aid)
                orphans = [m for m in inbound.get(aid, [])
                           if not (nodes.get(m, {}).get("local_tracks"))
                           and all(t == aid for t in
                                   [e2["target"] for e2 in edges if e2["source"] == m])]
                for m in orphans:
                    drop.add(m)
                report.append(f"  resolve {aid:42} -> {canon}  (+{an.get('local_tracks') or 0} tracks, "
                              f"drop wrong node + {len(orphans)} orphan members)")

    # --- rewrite edges: repoint merges, drop edges touching deleted nodes, dedupe ---
    seen = {}
    new_edges = []
    dropped_edges = 0
    for e in edges:
        s = remap.get(e["source"], e["source"])
        t = remap.get(e["target"], e["target"])
        if s in drop or t in drop:
            dropped_edges += 1; continue
        if s == t:                      # self-loop from the merge
            dropped_edges += 1; continue
        key = (s, t, e["type"])
        if key in seen:                 # dedupe: fold intervals + sources together
            o = seen[key]
            o["intervals"] = uniq((o.get("intervals") or []) + (e.get("intervals") or []))
            o["sources"] = uniq((o.get("sources") or []) + (e.get("sources") or []))
            dropped_edges += 1; continue
        e = dict(e); e["source"] = s; e["target"] = t
        seen[key] = e; new_edges.append(e)

    # --- rewrite nodes: drop absorbed + deleted ---
    gone = set(remap) | drop
    new_nodes = [n for nid, n in nodes.items() if nid not in gone]

    print("\n".join(report))
    print(f"\n{len(gone)} nodes removed ({len(remap)} merged, {len(drop)} resolved/orphaned)")
    print(f"edges: {len(edges)} -> {len(new_edges)} ({dropped_edges} dropped/deduped)")
    print(f"nodes: {len(nodes)} -> {len(new_nodes)}")

    # spot-check the two headline fixes
    for cid in ["eagles", "outlaws"]:
        c = next((n for n in new_nodes if n["id"] == cid), None)
        if c:
            print(f"  {cid}: {c.get('local_tracks')} tracks, genres={c.get('genres')}, origin={c.get('origin')}")

    if not write:
        print("\n(dry run -- pass --write)")
        return 0
    (GRAPH / "nodes.json").write_text(json.dumps(new_nodes, indent=2) + "\n")
    (GRAPH / "edges.json").write_text(json.dumps(new_edges, indent=2) + "\n")
    print("\nwrote nodes.json + edges.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
