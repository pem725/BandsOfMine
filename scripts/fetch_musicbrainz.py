#!/usr/bin/env python3
"""Enrich data/graph/ from MusicBrainz.

MusicBrainz is CC0 and models band membership as first-class relations with
begin/end dates -- exactly the temporal edges this project needs. We use it for
hard structure (who was in what, when) and leave the narrative "why" to bios.

Usage:
  python3 scripts/fetch_musicbrainz.py --resolve            # fill in missing mbids
  python3 scripts/fetch_musicbrainz.py --expand neil-young  # pull that artist's relations
  python3 scripts/fetch_musicbrainz.py --expand-seeds       # expand every seed:true node
  python3 scripts/fetch_musicbrainz.py --expand-all --depth 1

Nothing is written until you pass --write. By default it prints a diff of what
it *would* add, so you stay the editor of your own graph.
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
CACHE = ROOT / "data" / "cache"

API = "https://musicbrainz.org/ws/2"
# MusicBrainz requires a descriptive User-Agent and rate-limits to ~1 req/sec.
UA = "BandsOfMine/0.1 ( https://github.com/pem725/BandsOfMine )"
RATE_LIMIT_SECONDS = 1.1

# MusicBrainz relation type -> our edge type
REL_MAP = {
    "member of band": "member_of",
    "collaboration": "collaborated_with",
    "producer": "produced",
    "instrumental supporting musician": "collaborated_with",
    "vocal supporting musician": "collaborated_with",
    "founder": "member_of",
    "subgroup": "spun_off_from",
    "teacher": "mentored",
}

_last_call = 0.0


def slugify(name: str) -> str:
    """Node ids are kebab-case ASCII. 'Frank "Poncho" Sampedro' -> frank-poncho-sampedro."""
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    n = re.sub(r"[^a-zA-Z0-9]+", "-", n).strip("-").lower()
    return n or "unknown"


def get(path: str, **params) -> dict:
    """GET from MusicBrainz with on-disk caching and polite rate limiting."""
    global _last_call
    params.setdefault("fmt", "json")
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"

    CACHE.mkdir(parents=True, exist_ok=True)
    key = CACHE / (slugify(url)[:180] + ".json")
    if key.exists():
        return json.loads(key.read_text())

    elapsed = time.monotonic() - _last_call
    if elapsed < RATE_LIMIT_SECONDS:
        time.sleep(RATE_LIMIT_SECONDS - elapsed)
    _last_call = time.monotonic()

    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    key.write_text(json.dumps(data))
    return data


def year_of(date_str: str | None) -> int | None:
    if not date_str:
        return None
    m = re.match(r"(\d{4})", date_str)
    return int(m.group(1)) if m else None


def resolve_mbid(node: dict) -> dict | None:
    """Search MusicBrainz by name and return the best matching artist dict.

    We do NOT constrain by kind: Spotify-seeded nodes are all tagged 'person'
    even when they're bands, so constraining would miss every band. Instead we
    read the matched entity's own type back and let the caller fix the kind.
    """
    res = get("artist", query=f'artist:"{node["name"]}"', limit=5)
    for a in res.get("artists", []):
        # MB scores 0-100; below ~85 the match is usually a different artist.
        if a.get("score", 0) < 85:
            continue
        born = year_of((a.get("life-span") or {}).get("begin"))
        if node.get("born") and born and abs(node["born"] - born) > 2:
            continue  # right name, wrong person
        return a
    return None


def relations_for(mbid: str) -> list[dict]:
    data = get(f"artist/{mbid}", inc="artist-rels")
    return data.get("relations", [])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resolve", action="store_true", help="fill in missing mbids")
    ap.add_argument("--expand", metavar="NODE_ID", help="pull relations for one node")
    ap.add_argument("--expand-seeds", action="store_true", help="expand every seed:true node")
    ap.add_argument("--expand-bands", action="store_true",
                    help="expand only band nodes (their members connect the graph)")
    ap.add_argument("--expand-persons", action="store_true",
                    help="expand every person node with an mbid (find every band each "
                         "person joined — Joe Walsh -> James Gang, Barnstorm, Eagles...)")
    ap.add_argument("--link-only", action="store_true",
                    help="with expand: only add edges between artists ALREADY in the graph; "
                         "don't pull in new member nodes")
    ap.add_argument("--write", action="store_true", help="actually write the graph files")
    args = ap.parse_args()

    nodes = json.loads((GRAPH / "nodes.json").read_text())
    edges = json.loads((GRAPH / "edges.json").read_text())
    by_id = {n["id"]: n for n in nodes}
    existing = {(e["source"], e["target"], e["type"]) for e in edges}

    changed_nodes: list[str] = []
    new_nodes: list[dict] = []
    new_edges: list[dict] = []

    targets: list[dict] = []
    if args.expand:
        if args.expand not in by_id:
            print(f"no such node: {args.expand}", file=sys.stderr)
            return 1
        targets = [by_id[args.expand]]
    elif args.expand_bands:
        targets = [n for n in nodes if n.get("seed") and n.get("kind") == "band"]
    elif args.expand_persons:
        targets = [n for n in nodes if n.get("kind") == "person" and n.get("mbid")]
    elif args.expand_seeds:
        targets = [n for n in nodes if n.get("seed")]

    # --- resolve mbids ---------------------------------------------------
    to_resolve = nodes if args.resolve else targets
    for n in to_resolve:
        if n.get("mbid"):
            continue
        try:
            match = resolve_mbid(n)
        except Exception as exc:  # network hiccup shouldn't lose prior work
            print(f"  !! {n['id']}: {exc}", file=sys.stderr)
            continue
        if not match:
            print(f"  ??  {n['id']}: no confident MusicBrainz match")
            continue
        n["mbid"] = match["id"]
        fixes = []
        # Correct kind from MB's own typing (Spotify tagged everything 'person').
        mb_kind = {"Group": "band", "Person": "person"}.get(match.get("type"))
        if mb_kind and mb_kind != n["kind"]:
            n["kind"] = mb_kind
            fixes.append(f"kind->{mb_kind}")
        # Fill in formed/born and dissolved/died years when we don't have them.
        span = match.get("life-span") or {}
        if not n.get("born") and year_of(span.get("begin")):
            n["born"] = year_of(span.get("begin")); fixes.append(f"born {n['born']}")
        if not n.get("died") and year_of(span.get("end")):
            n["died"] = year_of(span.get("end")); fixes.append(f"died {n['died']}")
        if "musicbrainz" not in n.get("sources", []):
            n.setdefault("sources", []).append("musicbrainz")
        changed_nodes.append(n["id"])
        tail = ("  " + ", ".join(fixes)) if fixes else ""
        print(f"  mbid {n['id']} -> {match['id']}{tail}")

    # --- expand relations ------------------------------------------------
    for n in targets:
        if not n.get("mbid"):
            continue
        print(f"\nexpanding {n['id']}")
        try:
            rels = relations_for(n["mbid"])
        except Exception as exc:
            print(f"  !! {exc}", file=sys.stderr)
            continue

        for rel in rels:
            etype = REL_MAP.get(rel.get("type"))
            if not etype:
                continue
            other = rel.get("artist") or {}
            if not other.get("name"):
                continue

            # Is this other artist already in the graph? Match by MBID first,
            # then by slug (so we connect existing nodes rather than dup them).
            slug = slugify(other["name"])
            match_id = next(
                (m["id"] for m in nodes + new_nodes
                 if m.get("mbid") and m["mbid"] == other["id"]),
                None,
            )
            if match_id is None and (slug in by_id or any(m["id"] == slug for m in new_nodes)):
                match_id = slug
            known = match_id is not None

            # --link-only: draw edges among artists you already have; don't
            # pull every sideman on Earth into the graph.
            if args.link_only and not known:
                continue

            oid = match_id or slug
            if oid not in by_id and not any(m["id"] == oid for m in new_nodes):
                new_nodes.append(
                    {
                        "id": oid,
                        "name": other["name"],
                        "kind": "band" if other.get("type") == "Group" else "person",
                        "born": year_of((other.get("life-span") or {}).get("begin")),
                        "died": year_of((other.get("life-span") or {}).get("end")),
                        "origin": None,
                        "roles": [],
                        "genres": [],
                        "mbid": other["id"],
                        "spotify_id": None,
                        "seed": False,
                        "sources": ["musicbrainz"],
                    }
                )

            # MusicBrainz relations carry direction; "backward" means the other
            # artist is the subject (e.g. THEY are a member of THIS band).
            src, tgt = (n["id"], oid)
            if rel.get("direction") == "backward":
                src, tgt = tgt, src

            if (src, tgt, etype) in existing:
                continue
            existing.add((src, tgt, etype))

            start = year_of(rel.get("begin"))
            end = year_of(rel.get("end"))
            new_edges.append(
                {
                    "source": src,
                    "target": tgt,
                    "type": etype,
                    "intervals": [[start, end]] if start else [],
                    "weight": 0.7,
                    "note": None,
                    "sources": ["musicbrainz"],
                }
            )
            span = f"{start or '?'}-{end or ''}"
            print(f"  + {src} -{etype}-> {tgt}  [{span}]")

    print(
        f"\n{len(changed_nodes)} mbids resolved, "
        f"{len(new_nodes)} new nodes, {len(new_edges)} new edges"
    )
    if not args.write:
        print("(dry run -- pass --write to apply)")
        return 0

    (GRAPH / "nodes.json").write_text(json.dumps(nodes + new_nodes, indent=2) + "\n")
    (GRAPH / "edges.json").write_text(json.dumps(edges + new_edges, indent=2) + "\n")
    print("written. now run: python3 scripts/validate_graph.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
