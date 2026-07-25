#!/usr/bin/env python3
"""Add real influence edges from Wikidata's "influenced by" (P737).

MusicBrainz models who *worked* together but not who *influenced* whom. Wikidata
does, via property P737, and every artist we resolved carries a MusicBrainz ID
(P434) that links the two. So: batch our MBIDs into Wikidata SPARQL, pull each
artist's documented influences, and add them as `influenced_by` edges
(A influenced_by B == A draws on B).

Influences that are already in the graph become internal cross-links. Influences
we don't have (Robert Johnson, Woody Guthrie, the taproots everyone draws on)
are added as new nodes — those foundational figures are exactly what an
influence map should surface.

  python3 scripts/fetch_wikidata_influence.py            # preview
  python3 scripts/fetch_wikidata_influence.py --write
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "data" / "graph"
CACHE = ROOT / "data" / "cache" / "wikidata"

WDQS = "https://query.wikidata.org/sparql"
UA = "BandsOfMine/0.1 ( https://github.com/pem725/BandsOfMine )"
CHUNK = 150          # MBIDs per SPARQL query
RATE = 1.5           # seconds between queries (be polite to WDQS)

_last = 0.0


def slugify(name: str) -> str:
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    n = re.sub(r"[^a-zA-Z0-9]+", "-", n).strip("-").lower()
    return n or "unknown"


def sparql(mbids: list[str]) -> list[dict]:
    global _last
    values = " ".join(f'"{m}"' for m in mbids)
    q = f"""
    SELECT ?mbid ?inf ?infMbid ?infLabel ?infType WHERE {{
      VALUES ?mbid {{ {values} }}
      ?artist wdt:P434 ?mbid .
      ?artist wdt:P737 ?inf .
      OPTIONAL {{ ?inf wdt:P434 ?infMbid . }}
      OPTIONAL {{ ?inf wdt:P31 ?infType . }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". ?inf rdfs:label ?infLabel. }}
    }}"""
    CACHE.mkdir(parents=True, exist_ok=True)
    key = CACHE / (slugify("".join(sorted(mbids)))[:120] + ".json")
    if key.exists():
        return json.loads(key.read_text())

    dt = time.monotonic() - _last
    if dt < RATE:
        time.sleep(RATE - dt)
    _last = time.monotonic()

    url = f"{WDQS}?{urllib.parse.urlencode({'query': q, 'format': 'json'})}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/sparql-results+json"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode())
    rows = data.get("results", {}).get("bindings", [])
    key.write_text(json.dumps(rows))
    return rows


# Wikidata Q-ids whose P31 (instance of) means "a group/band" rather than a person.
GROUP_TYPES = {"Q215380", "Q2088357", "Q105756498"}  # band, musical group, musical duo


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="cap artists queried (testing)")
    args = ap.parse_args()

    nodes = json.loads((GRAPH / "nodes.json").read_text())
    edges = json.loads((GRAPH / "edges.json").read_text())
    by_id = {n["id"]: n for n in nodes}
    by_mbid = {n["mbid"]: n for n in nodes if n.get("mbid")}
    by_slug = {n["id"]: n for n in nodes}
    existing = {(e["source"], e["target"], e["type"]) for e in edges}

    mbids = [n["mbid"] for n in nodes if n.get("mbid")]
    if args.limit:
        mbids = mbids[:args.limit]
    print(f"querying Wikidata for influences of {len(mbids)} artists "
          f"({(len(mbids) + CHUNK - 1) // CHUNK} SPARQL calls)...")

    new_nodes: list[dict] = []
    new_edges: list[dict] = []
    added_new = linked = 0

    for i in range(0, len(mbids), CHUNK):
        chunk = mbids[i:i + CHUNK]
        try:
            rows = sparql(chunk)
        except Exception as exc:
            print(f"  !! chunk {i // CHUNK}: {exc}", file=sys.stderr)
            continue

        for r in rows:
            src_mbid = r["mbid"]["value"]
            src = by_mbid.get(src_mbid)
            if not src:
                continue
            inf_qid = r["inf"]["value"].rsplit("/", 1)[-1]
            inf_name = r.get("infLabel", {}).get("value", "").strip()
            inf_mbid = r.get("infMbid", {}).get("value")
            inf_type = (r.get("infType", {}) or {}).get("value", "").rsplit("/", 1)[-1]
            if not inf_name or inf_name == inf_qid:
                continue

            # Find the influencer in our graph (by mbid, then slug), else make it.
            tgt = by_mbid.get(inf_mbid) if inf_mbid else None
            if not tgt:
                tgt = by_slug.get(slugify(inf_name))
            if tgt:
                tid = tgt["id"]
                linked += 1
            else:
                tid = slugify(inf_name)
                if tid not in by_slug and not any(m["id"] == tid for m in new_nodes):
                    new_nodes.append({
                        "id": tid, "name": inf_name,
                        "kind": "band" if inf_type in GROUP_TYPES else "person",
                        "born": None, "died": None, "origin": None,
                        "roles": [], "genres": [],
                        "mbid": inf_mbid, "spotify_id": None, "seed": False,
                        "sources": ["wikidata"],
                    })
                    by_slug[tid] = new_nodes[-1]
                    added_new += 1

            if src["id"] == tid or (src["id"], tid, "influenced_by") in existing:
                continue
            existing.add((src["id"], tid, "influenced_by"))
            new_edges.append({
                "source": src["id"], "target": tid, "type": "influenced_by",
                "intervals": [], "weight": 0.7,
                "note": f"Wikidata: {src['name']} influenced by {inf_name}.",
                "sources": ["wikidata"],
            })
        print(f"  chunk {i // CHUNK + 1}: {len(new_edges)} influence edges so far")

    print(f"\n{len(new_edges)} influence edges, {added_new} new influencer nodes, "
          f"{linked} links to artists you already have.")
    if not args.write:
        # Show a taste of what we found.
        for e in new_edges[:20]:
            print(f"    {by_id.get(e['source'],{}).get('name',e['source'])} "
                  f"--influenced by--> {e['target']}")
        print("(dry run -- pass --write to apply)")
        return 0

    (GRAPH / "nodes.json").write_text(json.dumps(nodes + new_nodes, indent=2) + "\n")
    (GRAPH / "edges.json").write_text(json.dumps(edges + new_edges, indent=2) + "\n")
    print("written. now run: python3 scripts/validate_graph.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
