#!/usr/bin/env python3
"""Find artists your collection INFLUENCED — recommendation candidates.

The prescriptive flip side of fetch_wikidata_influence: instead of "who your
artists drew on", ask Wikidata "who cites YOUR artists as an influence" (P737
pointing *into* your collection). Those followers descend from your taste's DNA,
so they're natural things to try. We only keep followers influenced by 2+ of
your artists (strong fit, not one-off), and never re-add excised artists.

Adds them as nodes flagged candidate:true (source "wikidata-follower"), with an
influenced_by edge follower -> your artist. They show up in the graph as the
outer ring your constellation reaches into. Then: fit_score / recommend.

  python3 scripts/fetch_wikidata_followers.py            # preview
  python3 scripts/fetch_wikidata_followers.py --write
"""
from __future__ import annotations
import argparse, json, re, sys, time, unicodedata, urllib.parse, urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "data" / "graph"
CACHE = ROOT / "data" / "cache" / "wikidata"
WDQS = "https://query.wikidata.org/sparql"
UA = "BandsOfMine/0.1 ( https://github.com/pem725/BandsOfMine )"
CHUNK, RATE = 120, 1.5
_last = 0.0


def slugify(name: str) -> str:
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", n).strip("-").lower() or "unknown"


def sparql(mbids):
    global _last
    values = " ".join(f'"{m}"' for m in mbids)
    q = f"""
    SELECT ?mbid ?follower ?folMbid ?folLabel ?folType WHERE {{
      VALUES ?mbid {{ {values} }}
      ?artist wdt:P434 ?mbid .
      ?follower wdt:P737 ?artist .
      OPTIONAL {{ ?follower wdt:P434 ?folMbid . }}
      OPTIONAL {{ ?follower wdt:P31 ?folType . }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". ?follower rdfs:label ?folLabel. }}
    }}"""
    CACHE.mkdir(parents=True, exist_ok=True)
    key = CACHE / ("rev-" + slugify("".join(sorted(mbids)))[:110] + ".json")
    if key.exists():
        return json.loads(key.read_text())
    dt = time.monotonic() - _last
    if dt < RATE:
        time.sleep(RATE - dt)
    _last = time.monotonic()
    url = f"{WDQS}?{urllib.parse.urlencode({'query': q, 'format': 'json'})}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/sparql-results+json"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        rows = json.loads(resp.read().decode()).get("results", {}).get("bindings", [])
    key.write_text(json.dumps(rows))
    return rows


GROUP_TYPES = {"Q215380", "Q2088357", "Q105756498"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--min-links", type=int, default=2, help="keep followers influenced by >= N of your artists")
    args = ap.parse_args()

    nodes = json.loads((GRAPH / "nodes.json").read_text())
    edges = json.loads((GRAPH / "edges.json").read_text())
    by_mbid = {n["mbid"]: n for n in nodes if n.get("mbid")}
    by_slug = {n["id"]: n for n in nodes}
    existing = {(e["source"], e["target"], e["type"]) for e in edges}
    excl = set()
    nm = GRAPH / "not_mine.txt"
    if nm.exists():
        excl = {l.strip() for l in nm.read_text().splitlines() if l.strip() and not l.startswith("#")}

    # only chase followers of OWNED artists with an mbid (your true taste)
    owned_mbids = [n["mbid"] for n in nodes if n.get("mbid") and (n.get("local_tracks") or 0) > 0]
    print(f"chasing followers of {len(owned_mbids)} owned artists ({(len(owned_mbids)+CHUNK-1)//CHUNK} queries)...")

    # follower -> set of your artists that influenced them
    fol_meta = {}
    fol_links = defaultdict(set)
    for i in range(0, len(owned_mbids), CHUNK):
        try:
            rows = sparql(owned_mbids[i:i+CHUNK])
        except Exception as exc:
            print(f"  !! chunk {i//CHUNK}: {exc}", file=sys.stderr); continue
        for r in rows:
            src = by_mbid.get(r["mbid"]["value"])
            if not src:
                continue
            name = r.get("folLabel", {}).get("value", "").strip()
            qid = r["follower"]["value"].rsplit("/", 1)[-1]
            if not name or name == qid:
                continue
            slug = slugify(name)
            if slug in by_slug or slug in excl:   # already have them, or excised
                continue
            fol_links[slug].add(src["id"])
            fol_meta[slug] = {"name": name, "mbid": r.get("folMbid", {}).get("value"),
                              "type": (r.get("folType", {}) or {}).get("value", "").rsplit("/", 1)[-1]}

    # over-connected "pop bridge" roots carry little signal (everything cites them)
    inf_in = defaultdict(int)
    for e in edges:
        if e["type"] == "influenced_by":
            inf_in[e["target"]] += 1
    def distinct_srcs(srcs):   # how many of your DISTINCTIVE artists it descends from
        return [s for s in srcs if inf_in.get(s, 0) <= 8]

    # keep only real catalogued artists (has MBID) that descend from >=2 of your
    # DISTINCTIVE artists — this strips the self-promoter spam and the pop bias
    strong = {}
    for s, srcs in fol_links.items():
        if not fol_meta[s].get("mbid"):
            continue
        d = distinct_srcs(srcs)
        if len(d) >= args.min_links:
            strong[s] = srcs
    ranked = sorted(strong.items(), key=lambda kv: (-len(distinct_srcs(kv[1])), -len(kv[1])))
    print(f"\n{len(strong)} quality candidates (catalogued + descend from >= {args.min_links} "
          f"DISTINCTIVE artists of yours):\n")
    for slug, srcs in ranked[:30]:
        d = distinct_srcs(srcs)
        who = ", ".join(by_slug.get(s, {}).get("name", s) for s in d[:4])
        print(f"  {len(d)}x  {fol_meta[slug]['name']:26} <- {who}")

    if not args.write:
        print("\n(preview -- pass --write to add them as candidate nodes)")
        return 0

    new_nodes, new_edges = [], []
    for slug, srcs in strong.items():
        m = fol_meta[slug]
        new_nodes.append({
            "id": slug, "name": m["name"],
            "kind": "band" if m["type"] in GROUP_TYPES else "person",
            "born": None, "died": None, "origin": None, "roles": [], "genres": [],
            "mbid": m["mbid"], "spotify_id": None, "seed": False, "candidate": True,
            "rec_score": len(srcs), "sources": ["wikidata-follower"],
        })
        for s in srcs:
            if (slug, s, "influenced_by") in existing:
                continue
            existing.add((slug, s, "influenced_by"))
            new_edges.append({"source": slug, "target": s, "type": "influenced_by",
                              "intervals": [], "weight": 0.6,
                              "note": f"{m['name']} cites {by_slug.get(s,{}).get('name',s)} as an influence (Wikidata).",
                              "sources": ["wikidata-follower"]})
    (GRAPH / "nodes.json").write_text(json.dumps(nodes + new_nodes, indent=2) + "\n")
    (GRAPH / "edges.json").write_text(json.dumps(edges + new_edges, indent=2) + "\n")
    print(f"\nwrote {len(new_nodes)} candidate nodes, {len(new_edges)} edges. Run validate_graph.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
