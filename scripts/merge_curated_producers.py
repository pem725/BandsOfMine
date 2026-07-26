#!/usr/bin/env python3
"""Merge hand-researched producer relationships from data/graph/curated_producers.json.

Producers are the "fifth Beatle" layer — the people whose judgment shaped a
record without being in the band or influencing the artist from afar (George
Martin on the Beatles, Rick Rubin's analog-tape Neil Young, Zaytoven's Atlanta
trap). MusicBrainz barely captures this; we fill it from research.

Each entry is {artist, producers[], source}. Producers become nodes (kind
person, role producer) and get a `produced` edge (producer -> artist), tagged
"web-curated". Matches existing nodes by name/slug (handles leading "The").

  python3 scripts/merge_curated_producers.py            # preview
  python3 scripts/merge_curated_producers.py --write
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "data" / "graph"
CUR = GRAPH / "curated_producers.json"


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
    denorm = lambda s: re.sub(r"^the-", "", s)
    by_norm = {denorm(n["id"]): n for n in nodes}
    existing = {(e["source"], e["target"], e["type"]) for e in edges}

    new_nodes, new_edges, missing = [], [], []
    added_nodes = 0

    for entry in curated:
        art = by_slug.get(entry["artist"]) or by_name.get(entry["artist"].lower())
        if not art:
            missing.append(entry["artist"])
            continue
        for name in entry["producers"]:
            prod = (by_name.get(name.lower()) or by_slug.get(slugify(name))
                    or by_norm.get(denorm(slugify(name))))
            if prod:
                pid = prod["id"]
                # make sure the existing node is tagged as a producer
                if "producer" not in (prod.get("roles") or []):
                    prod.setdefault("roles", []).append("producer")
            else:
                pid = slugify(name)
                if pid not in by_slug and not any(m["id"] == pid for m in new_nodes):
                    new_nodes.append({
                        "id": pid, "name": name, "kind": "person",
                        "born": None, "died": None, "origin": None,
                        "roles": ["producer"], "genres": [],
                        "mbid": None, "spotify_id": None, "seed": False,
                        "sources": ["web-curated"],
                    })
                    by_slug[pid] = new_nodes[-1]
                    added_nodes += 1
            # produced edge: producer -> artist
            if pid == art["id"] or (pid, art["id"], "produced") in existing:
                continue
            existing.add((pid, art["id"], "produced"))
            new_edges.append({
                "source": pid, "target": art["id"], "type": "produced",
                "intervals": [], "weight": 0.8,
                "note": f"{name} produced {art['name']}. Source: {entry['source']}",
                "sources": ["web-curated"],
            })
            print(f"  {name} --produced--> {art['name']}" + ("" if prod else "  (new)"))

    print(f"\n{len(new_edges)} produced edges, {added_nodes} new producer nodes.")
    if missing:
        print(f"  (artists not in graph, skipped: {', '.join(missing)})")
    if not write:
        print("(dry run -- pass --write to apply)")
        return 0
    (GRAPH / "nodes.json").write_text(json.dumps(nodes + new_nodes, indent=2) + "\n")
    (GRAPH / "edges.json").write_text(json.dumps(edges + new_edges, indent=2) + "\n")
    print("written. now run: python3 scripts/validate_graph.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
