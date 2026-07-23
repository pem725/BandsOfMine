#!/usr/bin/env python3
"""Seed the graph from your Spotify data export -- your true listening history.

Where the Web API gives you rolling windows Spotify computes, the data export
gives you ground truth: every track you've ever played, with how long you
listened. Ranking artists by total listening time is the strongest possible
signal for "bands of mine."

--------------------------------------------------------------------------
GET YOUR DATA
--------------------------------------------------------------------------
Spotify -> Account -> Privacy Settings -> "Download your data".
Two options, either works here:
  * "Account data"  -> arrives in a few days, ~1 year of history
      files: StreamingHistory_music_*.json  (or StreamingHistory0.json ...)
      fields: endTime, artistName, trackName, msPlayed
  * "Extended streaming history" -> arrives in ~30 days, your WHOLE history
      files: Streaming_History_Audio_*.json
      fields: ts, master_metadata_album_artist_name, ms_played, ...

Unzip it and point this script at the folder.

--------------------------------------------------------------------------
USAGE
--------------------------------------------------------------------------
  python3 scripts/import_spotify_export.py path/to/unzipped_export
  python3 scripts/import_spotify_export.py path/to/export --top 40 --write
  python3 scripts/import_spotify_export.py path/to/export --report   # just print the ranking

Recommended home for the export: data/raw/spotify_export/  (gitignored -- it
contains your personal listening history and should never be committed).
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

# Ignore blips: a play under this many ms is a skip, not a listen.
MIN_MS = 30_000


def slugify(name: str) -> str:
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    n = re.sub(r"[^a-zA-Z0-9]+", "-", n).strip("-").lower()
    return n or "unknown"


def iter_plays(folder: Path):
    """Yield (artist_name, ms_played) across both export formats."""
    files = sorted(folder.rglob("*.json"))
    if not files:
        sys.exit(f"No .json files found under {folder}")

    seen_any = False
    for f in files:
        name = f.name.lower()
        is_basic = name.startswith("streaminghistory")
        is_ext = name.startswith("streaming_history_audio")
        if not (is_basic or is_ext):
            continue
        try:
            data = json.loads(f.read_text())
        except Exception as exc:
            print(f"  (skipping {f.name}: {exc})", file=sys.stderr)
            continue
        if not isinstance(data, list):
            continue
        seen_any = True
        for row in data:
            if is_basic:
                artist = row.get("artistName")
                ms = row.get("msPlayed", 0)
            else:
                artist = row.get("master_metadata_album_artist_name")
                ms = row.get("ms_played", 0)
            if artist and ms:
                yield artist, ms

    if not seen_any:
        sys.exit(
            "Found JSON but no recognized streaming-history files.\n"
            "Expected StreamingHistory*.json or Streaming_History_Audio_*.json."
        )


def rank(folder: Path) -> list[dict]:
    total_ms: dict[str, int] = defaultdict(int)
    plays: dict[str, int] = defaultdict(int)
    for artist, ms in iter_plays(folder):
        if ms < MIN_MS:
            continue
        total_ms[artist] += ms
        plays[artist] += 1

    ranked = [
        {"name": a, "ms": total_ms[a], "plays": plays[a], "hours": round(total_ms[a] / 3_600_000, 1)}
        for a in total_ms
    ]
    ranked.sort(key=lambda r: -r["ms"])
    return ranked


def merge(ranked: list[dict], top: int, write: bool) -> int:
    keep = ranked[:top]
    nodes = json.loads((GRAPH / "nodes.json").read_text())
    by_slug = {n["id"]: n for n in nodes}
    by_name = {n["name"].lower(): n for n in nodes}

    # Weight is relative to your single most-played artist.
    max_ms = keep[0]["ms"] if keep else 1
    added = promoted = 0
    for r in keep:
        weight = round(0.4 + 0.6 * (r["ms"] / max_ms), 3)
        why = f"{r['hours']}h over {r['plays']} plays"
        existing = by_name.get(r["name"].lower()) or by_slug.get(slugify(r["name"]))
        if existing:
            notes = []
            if not existing.get("seed"):
                existing["seed"] = True
                promoted += 1
                notes.append("promoted to seed")
            if "spotify-export" not in existing.get("sources", []):
                existing.setdefault("sources", []).append("spotify-export")
            existing["play_weight"] = weight
            if notes:
                print(f"  ~ {existing['id']}: {', '.join(notes)}  ({why})")
            continue

        node = {
            "id": slugify(r["name"]),
            "name": r["name"],
            "kind": "person",  # refine via MusicBrainz later
            "born": None, "died": None, "origin": None,
            "roles": [], "genres": [],
            "mbid": None, "spotify_id": None,
            "seed": True,
            "play_weight": weight,
            "sources": ["spotify-export"],
        }
        base = node["id"]; k = 2
        while node["id"] in by_slug:
            node["id"] = f"{base}-{k}"; k += 1
        by_slug[node["id"]] = node
        nodes.append(node)
        added += 1
        print(f"  + {node['id']}  (w {weight}, {why})")

    print(f"\n{added} new seed artists, {promoted} promoted to seed (top {top} of {len(ranked)}).")
    if not write:
        print("(dry run -- pass --write to apply)")
        return 0
    (GRAPH / "nodes.json").write_text(json.dumps(nodes, indent=2) + "\n")
    print("written. next:")
    print("  python3 scripts/validate_graph.py")
    print("  python3 scripts/fetch_musicbrainz.py --resolve")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path, help="unzipped Spotify data export folder")
    ap.add_argument("--top", type=int, default=40, help="how many top artists to seed (default 40)")
    ap.add_argument("--report", action="store_true", help="just print the ranking, don't touch the graph")
    ap.add_argument("--write", action="store_true", help="apply changes to the graph")
    args = ap.parse_args()

    if not args.folder.exists():
        sys.exit(f"No such folder: {args.folder}")

    ranked = rank(args.folder)
    print(f"{len(ranked)} artists in your history (plays >= {MIN_MS // 1000}s).\n")
    print("  your top 25 by listening time:")
    for i, r in enumerate(ranked[:25], 1):
        print(f"  {i:>3}. {r['hours']:>6}h  {r['plays']:>4} plays  {r['name']}")
    print()

    if args.report:
        return 0
    return merge(ranked, args.top, args.write)


if __name__ == "__main__":
    sys.exit(main())
