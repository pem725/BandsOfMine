#!/usr/bin/env python3
"""Does an artist belong in YOUR constellation? Descriptive + prescriptive.

The idea: your true taste is the music you OWN (the local library). An artist
"fits" if it shares taproots with that owned core -- the same influences,
producers, and sonic neighbourhood. Music your son streamed on your account
(hip-hop/trap) shares none of those roots, so it scores ~0 and falls out.

  DESCRIPTIVE (default): rank every artist by fit; surface the misfits.
    python3 scripts/fit_score.py
    python3 scripts/fit_score.py --misfits      # just the low-fit stream-only outliers

  PRESCRIPTIVE: would a specific artist fit, and where?
    python3 scripts/fit_score.py --artist billy-strings
    python3 scripts/fit_score.py --artist "St. Vincent"   # by name too

Fit blends two signals: shared influence-taproots with your owned core, and
(when available) sonic proximity from data/graph/sonic.json.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "data" / "graph"
OWN_MIN = 40  # local tracks to count as "owned core"


def slugify(name: str) -> str:
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", n).strip("-").lower() or "unknown"


def load():
    nodes = {n["id"]: n for n in json.loads((GRAPH / "nodes.json").read_text())}
    edges = json.loads((GRAPH / "edges.json").read_text())
    sonic = {}
    f = GRAPH / "sonic.json"
    if f.exists():
        sonic = json.loads(f.read_text()).get("artists", {})
    return nodes, edges, sonic


def build(nodes, edges):
    inf_out = defaultdict(set)   # artist -> taproots it draws on
    inf_in = defaultdict(set)    # artist -> who draws on it
    for e in edges:
        if e["type"] == "influenced_by":
            inf_out[e["source"]].add(e["target"])
            inf_in[e["target"]].add(e["source"])
    return inf_out, inf_in


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artist", help="score one artist (prescriptive)")
    ap.add_argument("--misfits", action="store_true", help="list low-fit stream-only outliers")
    ap.add_argument("--top", type=int, default=30)
    args = ap.parse_args()

    nodes, edges, sonic = load()
    inf_out, inf_in = build(nodes, edges)

    owned = [nid for nid, n in nodes.items() if (n.get("local_tracks") or 0) >= OWN_MIN]
    core_roots = set()
    for c in owned:
        core_roots |= inf_out.get(c, set())
    owned_set = set(owned)

    def fit(nid):
        roots = inf_out.get(nid, set())
        if not roots:
            return None, 0, 0, []
        shared = roots & core_roots
        return round(len(shared) / len(roots), 2), len(shared), len(roots), sorted(shared)

    def nearest_owned(nid, k=6):
        """owned artists that share the most taproots — where it would sit"""
        roots = inf_out.get(nid, set())
        scored = []
        for o in owned:
            common = roots & inf_out.get(o, set())
            if common:
                scored.append((len(common), o, sorted(common)))
        scored.sort(reverse=True)
        return scored[:k]

    # -------- prescriptive: one artist --------
    if args.artist:
        nid = args.artist if args.artist in nodes else slugify(args.artist)
        if nid not in nodes:
            print(f"'{args.artist}' is not in the graph yet.\n"
                  f"Add its influences to curated_influences.json (artist slug '{nid}'),\n"
                  f"then re-run to see where it fits.")
            return 1
        n = nodes[nid]
        f, sh, tot, shared = fit(nid)
        print(f"\n{n['name']}")
        owns = n.get("local_tracks")
        print(f"  owned: {('yes, %d tracks' % owns) if owns else 'no (stream-only)'}"
              f"   play_weight={n.get('play_weight')}")
        if f is None:
            print("  no influence data yet — can't score fit. Research its influences first.")
            return 0
        verdict = ("STRONG fit — right in your wheelhouse" if f >= 0.6 else
                   "PARTIAL fit — bridges in on a few roots" if f >= 0.25 else
                   "POOR fit — an outlier in your constellation")
        print(f"  fit score: {f}  ({sh}/{tot} taproots shared with your collection)  -> {verdict}")
        if shared:
            print(f"  shared roots: {', '.join(nodes[r]['name'] for r in shared)}")
        near = nearest_owned(nid)
        if near:
            print("  sits nearest (would cluster with):")
            for cnt, o, common in near:
                print(f"    {nodes[o]['name']:22} via {', '.join(nodes[r]['name'] for r in common[:3])}")
        if nid in sonic and sonic[nid].get("neighbors"):
            sn = [f"{nodes.get(s,{}).get('name',s)} {int(v*100)}%"
                  for s, v in sonic[nid]["neighbors"][:4] if s in nodes]
            print(f"  sounds most like: {', '.join(sn)}")
        return 0

    # -------- descriptive: rank everyone --------
    rows = []
    for nid, n in nodes.items():
        if n.get("kind") not in ("person", "band"):
            continue
        f, sh, tot, _ = fit(nid)
        if f is None:
            continue
        depended = bool(inf_in.get(nid))  # does anyone you own draw on them?
        owned_by_you = (n.get("local_tracks") or 0) > 0
        rows.append((f, nid, n["name"], tot, owned_by_you, depended, n.get("play_weight")))

    if args.misfits:
        # outliers: low fit, stream-only, and NOT a root your owned artists rely on
        out = [r for r in rows if r[0] < 0.2 and not r[4] and not r[5]]
        out.sort(key=lambda r: (r[0], -(r[6] or 0)))
        print(f"{len(out)} stream-only outliers (fit<0.2, you don't own them, "
              f"none of your artists draw on them):\n")
        for f, nid, name, tot, _, _, pw in out[:60]:
            print(f"  fit {f}  play {pw}  {name}   [{nid}]")
        return 0

    rows.sort()
    print("LOWEST-FIT artists (candidates that don't belong):")
    for f, nid, name, tot, owned_by, dep, pw in rows[:args.top]:
        flags = ("owned " if owned_by else "") + ("root " if dep else "")
        print(f"  {f:>4}  {name:28} {flags}")
    print("\nHIGHEST-FIT (your core taste, densely rooted):")
    for f, nid, name, tot, owned_by, dep, pw in rows[-12:][::-1]:
        print(f"  {f:>4}  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
