#!/usr/bin/env python3
"""Seed the graph from a local music library (default ~/Music).

Your local collection is ground truth you *own* -- and how many tracks you keep
of an artist is itself a love-signal. This walks the library, treats each
top-level folder as an artist (descending into known container folders like
"Amazon MP3"), counts tracks and albums per artist, and merges the result into
the graph as seed nodes.

It reads the directory tree, NOT ID3 tags -- 20k+ tag reads would be glacial and
these folders are cleanly artist-named. Accuracy is refined later via MusicBrainz.

Usage:
  python3 scripts/import_local_library.py                 # scan ~/Music, preview
  python3 scripts/import_local_library.py --report        # ranking only
  python3 scripts/import_local_library.py --min-tracks 3 --write
  python3 scripts/import_local_library.py /path/to/music --write
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "data" / "graph"

AUDIO_EXT = {".mp3", ".m4a", ".flac", ".ogg", ".wma", ".aac", ".wav"}

# Folders that hold Artist/ subfolders rather than being an artist themselves.
CONTAINER_DIRS = {"amazon mp3", "itunes", "various artists", "compilations",
                  "unknown artist", "music", "downloads", "mp3", "plexvideo"}

# Top-level names that are clearly not artists (live-show/venue-date dumps, etc.)
NON_ARTIST_RE = re.compile(
    r"(\d{1,2}[_\-/]\d{1,2}[_\-/]\d{2,4})"          # a date like 6_20_2004
    r"|(,\s*[A-Z][a-z]+\s*-\s*\d)"                     # "Berlin, Germany - 6..."
)


def slugify(name: str) -> str:
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    n = re.sub(r"[^a-zA-Z0-9]+", "-", n).strip("-").lower()
    return n or "unknown"


def load_excludes() -> set[str]:
    f = ROOT / "data" / "seed_exclude.txt"
    if not f.exists():
        return set()
    out = set()
    for line in f.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(line)
    return out


def scan(music: Path) -> dict[str, dict]:
    """Return {artist_name: {tracks, albums:set}} from the directory tree."""
    stats: dict[str, dict] = defaultdict(lambda: {"tracks": 0, "albums": set()})
    for dirpath, _dirs, files in os.walk(music):
        audio = [f for f in files if os.path.splitext(f)[1].lower() in AUDIO_EXT]
        if not audio:
            continue
        rel = Path(dirpath).relative_to(music).parts
        if not rel:
            continue  # loose files in the library root — no artist context
        top = rel[0]
        if top.lower() in CONTAINER_DIRS:
            if len(rel) < 2:
                continue
            artist, album = rel[1], (rel[2] if len(rel) > 2 else rel[1])
        else:
            artist, album = rel[0], (rel[1] if len(rel) > 1 else rel[0])

        entry = stats[artist]
        entry["tracks"] += len(audio)
        entry["albums"].add(album)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("music", nargs="?", default=str(Path.home() / "Music"),
                    help="music library folder (default ~/Music)")
    ap.add_argument("--min-tracks", type=int, default=2,
                    help="ignore artists with fewer than this many tracks (default 2)")
    ap.add_argument("--report", action="store_true", help="print ranking only")
    ap.add_argument("--write", action="store_true", help="apply to the graph")
    args = ap.parse_args()

    music = Path(args.music).expanduser()
    if not music.exists():
        sys.exit(f"No such folder: {music}")

    print(f"scanning {music} ...")
    stats = scan(music)

    excludes = load_excludes()
    flagged: list[str] = []
    artists: list[dict] = []
    for name, s in stats.items():
        if NON_ARTIST_RE.search(name):
            flagged.append(name)
            continue
        if s["tracks"] < args.min_tracks:
            continue
        artists.append({"name": name, "tracks": s["tracks"], "albums": len(s["albums"])})
    artists.sort(key=lambda a: -a["tracks"])

    print(f"{len(artists)} artists (>= {args.min_tracks} tracks); "
          f"{len(flagged)} folders skipped as non-artist.\n")
    print("  your deepest holdings:")
    for i, a in enumerate(artists[:30], 1):
        print(f"  {i:>3}. {a['tracks']:>4} tracks / {a['albums']:>2} albums  {a['name']}")
    if flagged:
        print(f"\n  skipped (look like live-show/date folders): {', '.join(flagged[:12])}"
              + (" ..." if len(flagged) > 12 else ""))
    print()

    if args.report:
        return 0

    # --- merge ---------------------------------------------------------
    nodes = json.loads((GRAPH / "nodes.json").read_text())
    by_slug = {n["id"]: n for n in nodes}
    by_name = {n["name"].lower(): n for n in nodes}
    max_tracks = artists[0]["tracks"] if artists else 1

    added = linked = skipped = 0
    for a in artists:
        slug = slugify(a["name"])
        if slug in excludes:
            skipped += 1
            continue
        weight = round(0.4 + 0.6 * (a["tracks"] / max_tracks), 3)
        existing = by_name.get(a["name"].lower()) or by_slug.get(slug)
        if existing:
            if "local-library" not in existing.get("sources", []):
                existing.setdefault("sources", []).append("local-library")
            existing["local_tracks"] = a["tracks"]
            if not existing.get("seed"):
                existing["seed"] = True
            linked += 1
            continue
        node = {
            "id": slug, "name": a["name"], "kind": "person",
            "born": None, "died": None, "origin": None,
            "roles": [], "genres": [],
            "mbid": None, "spotify_id": None, "seed": True,
            "local_tracks": a["tracks"], "local_weight": weight,
            "sources": ["local-library"],
        }
        base = slug; k = 2
        while node["id"] in by_slug:
            node["id"] = f"{base}-{k}"; k += 1
        by_slug[node["id"]] = node
        nodes.append(node)
        added += 1

    print(f"{added} new artists, {linked} matched existing nodes, "
          f"{skipped} excluded.")
    if not args.write:
        print("(dry run -- pass --write to apply)")
        return 0
    (GRAPH / "nodes.json").write_text(json.dumps(nodes, indent=2) + "\n")
    print("written. next:")
    print("  python3 scripts/validate_graph.py")
    print("  python3 scripts/fetch_musicbrainz.py --resolve   # kinds + mbids for new artists")
    return 0


if __name__ == "__main__":
    sys.exit(main())
