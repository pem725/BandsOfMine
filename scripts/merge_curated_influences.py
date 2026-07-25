#!/usr/bin/env python3
"""Merge hand-researched influence edges from data/graph/curated_influences.json.

Wikidata's P737 is thin for folk, bluegrass, and jam artists, so we fill those
gaps from the web by hand. Each entry is {artist, influences[], source}; this
matches influencer names to existing nodes (adding foundational figures we lack,
e.g. Doc Watson, Robert Johnson) and adds influenced_by edges tagged
"web-curated" so their provenance is clear.

  python3 scripts/merge_curated_influences.py            # preview
  python3 scripts/merge_curated_influences.py --write
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "data" / "graph"
CUR = GRAPH / "curated_influences.json"

# Influencer names that are clearly groups (so the new node gets kind=band).
BAND_HINT = re.compile(r"\b(band|brothers|revival|stringband|dead|crimson|feat|"
                       r"gang|skynyrd|talking heads|beach boys|flies|genesis|cream|free|mountain)\b", re.I)


def slugify(name: str) -> str:
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    n = re.sub(r"[^a-zA-Z0-9]+", "-", n).strip("-").lower()
    return n or "unknown"


def main() -> int:
    write = "--write" in sys.argv
    nodes = json.loads((GRAPH / "nodes.json").read_text())
    edges = json.loads((GRAPH / "edges.json").read_text())
    curated = json.loads(CUR.read_text())

    by_slug = {n["id"]: n for n in nodes}
    by_name = {n["name"].lower(): n for n in nodes}
    # Match despite a leading "The" ("The James Gang" == "james-gang").
    denorm = lambda s: re.sub(r"^the-", "", s)
    by_norm = {denorm(n["id"]): n for n in nodes}
    existing = {(e["source"], e["target"], e["type"]) for e in edges}

    new_nodes, new_edges, missing_artists = [], [], []
    added_nodes = 0

    for entry in curated:
        src = by_slug.get(entry["artist"]) or by_name.get(entry["artist"].lower())
        if not src:
            missing_artists.append(entry["artist"])
            continue
        for name in entry["influences"]:
            tgt = (by_name.get(name.lower()) or by_slug.get(slugify(name))
                   or by_norm.get(denorm(slugify(name))))
            if tgt:
                tid = tgt["id"]
            else:
                tid = slugify(name)
                if tid not in by_slug and not any(m["id"] == tid for m in new_nodes):
                    new_nodes.append({
                        "id": tid, "name": name,
                        "kind": "band" if BAND_HINT.search(name) else "person",
                        "born": None, "died": None, "origin": None,
                        "roles": [], "genres": [],
                        "mbid": None, "spotify_id": None, "seed": False,
                        "sources": ["web-curated"],
                    })
                    by_slug[tid] = new_nodes[-1]
                    added_nodes += 1
            if src["id"] == tid or (src["id"], tid, "influenced_by") in existing:
                continue
            existing.add((src["id"], tid, "influenced_by"))
            new_edges.append({
                "source": src["id"], "target": tid, "type": "influenced_by",
                "intervals": [], "weight": 0.65,
                "note": f"{src['name']} influenced by {name}. Source: {entry['source']}",
                "sources": ["web-curated"],
            })
            print(f"  {src['name']} --influenced by--> {name}"
                  + ("" if tgt else "  (new)"))

    print(f"\n{len(new_edges)} influence edges, {added_nodes} new influencer nodes.")
    if missing_artists:
        print(f"  (artists not found in graph, skipped: {', '.join(missing_artists)})")
    if not write:
        print("(dry run -- pass --write to apply)")
        return 0
    (GRAPH / "nodes.json").write_text(json.dumps(nodes + new_nodes, indent=2) + "\n")
    (GRAPH / "edges.json").write_text(json.dumps(edges + new_edges, indent=2) + "\n")
    print("written. now run: python3 scripts/validate_graph.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
