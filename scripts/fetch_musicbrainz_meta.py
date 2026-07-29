#!/usr/bin/env python3
"""Fill in genres, origin, and life-span from MusicBrainz (no web search).

Most nodes have empty genre/origin fields. MusicBrainz has all of it — genres
(folksonomy tags), the artist's area, and begin/end years — keyed by the MBIDs
we already resolved. This enriches node metadata so we can colour the graph by
genre or geography and sharpen the families clustering.

Targets owned + seed artists with an MBID that are missing genres. Idempotent.

  python3 scripts/fetch_musicbrainz_meta.py            # preview counts
  python3 scripts/fetch_musicbrainz_meta.py --write
  python3 scripts/fetch_musicbrainz_meta.py --all --write   # every mbid node, not just owned/seed
"""
from __future__ import annotations
import argparse, json, re, sys, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "data" / "graph"
CACHE = ROOT / "data" / "cache" / "mb-meta"
API = "https://musicbrainz.org/ws/2"
UA = "BandsOfMine/0.1 ( https://github.com/pem725/BandsOfMine )"
RATE = 1.1
_last = 0.0


def get(mbid: str) -> dict:
    global _last
    CACHE.mkdir(parents=True, exist_ok=True)
    key = CACHE / (mbid + ".json")
    if key.exists():
        return json.loads(key.read_text())
    dt = time.monotonic() - _last
    if dt < RATE:
        time.sleep(RATE - dt)
    _last = time.monotonic()
    url = f"{API}/artist/{mbid}?inc=genres+tags&fmt=json"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    key.write_text(json.dumps(data))
    return data


def year_of(s):
    if not s:
        return None
    m = re.match(r"(\d{4})", s)
    return int(m.group(1)) if m else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--all", action="store_true", help="every mbid node, not just owned/seed")
    args = ap.parse_args()

    nodes = json.loads((GRAPH / "nodes.json").read_text())

    def wanted(n):
        if not n.get("mbid") or n.get("kind") not in ("person", "band"):
            return False
        if not args.all and not (n.get("local_tracks") or n.get("seed") or n.get("play_weight")):
            return False
        return not n.get("genres")   # only ones still missing genres

    targets = [n for n in nodes if wanted(n)]
    print(f"{len(targets)} nodes to enrich with genre/origin/life-span "
          f"({'all mbid nodes' if args.all else 'owned/seed only'})")
    if not args.write:
        print("(preview -- pass --write to fetch and fill)")
        return 0

    genres_added = origin_added = years_added = done = 0
    for n in targets:
        try:
            d = get(n["mbid"])
        except Exception as exc:
            print(f"  !! {n['id']}: {exc}", file=sys.stderr)
            continue
        # genres (curated) preferred, fall back to folksonomy tags, by count
        glist = d.get("genres") or []
        if not glist:
            glist = d.get("tags") or []
        top = [g["name"] for g in sorted(glist, key=lambda g: -(g.get("count") or 0))[:4]]
        if top:
            n["genres"] = top
            genres_added += 1
        if not n.get("origin"):
            area = (d.get("begin-area") or d.get("area") or {})
            origin = area.get("name") or d.get("country")
            if origin:
                n["origin"] = origin
                origin_added += 1
        span = d.get("life-span") or {}
        if not n.get("born") and year_of(span.get("begin")):
            n["born"] = year_of(span.get("begin")); years_added += 1
        if not n.get("died") and year_of(span.get("end")):
            n["died"] = year_of(span.get("end"))
        if "musicbrainz" not in (n.get("sources") or []):
            n.setdefault("sources", []).append("musicbrainz")
        done += 1
        if done % 25 == 0:
            print(f"  {done}/{len(targets)}...")
            (GRAPH / "nodes.json").write_text(json.dumps(nodes, indent=2) + "\n")

    (GRAPH / "nodes.json").write_text(json.dumps(nodes, indent=2) + "\n")
    print(f"\nenriched {done} nodes: +{genres_added} genres, +{origin_added} origins, "
          f"+{years_added} birth years")
    return 0


if __name__ == "__main__":
    sys.exit(main())
