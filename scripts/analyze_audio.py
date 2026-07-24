#!/usr/bin/env python3
"""Extract a sonic fingerprint for each artist from the local audio files.

This is the layer the personnel graph can't give you: how the music actually
*sounds*. For each artist we sample a few tracks and measure

  tempo        beats per minute            -- your "timing"
  key          estimated musical key       -- the tonal center
  brightness   spectral centroid (Hz)      -- dark/warm vs bright/trebly
  energy       RMS loudness                -- gentle vs driving
  percussive   zero-crossing rate          -- smooth vs gritty/noisy
  dynamics     spread of loudness          -- flat vs dynamic
  timbre       13 MFCC means               -- the "color" of the sound, for similarity

Averaged per artist, these become coordinates in a sonic space where we can ask
where your music converges, diverges, and stands alone.

Run in the venv that has librosa:
  .venv-audio/bin/python scripts/analyze_audio.py --tracks 2            # preview counts
  .venv-audio/bin/python scripts/analyze_audio.py --tracks 2 --write    # analyze + save
  .venv-audio/bin/python scripts/analyze_audio.py --only-graph --write  # only artists already in the graph

Writes/updates data/graph/audio_features.json incrementally (resumable).
Tempo/key/brightness are physical facts about the recording, not copyrightable,
so this file is safe to commit.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
import warnings
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")  # librosa/audioread are chatty on odd mp3s

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "data" / "graph"
OUT = GRAPH / "audio_features.json"
AUDIO_EXT = {".mp3", ".m4a", ".flac", ".ogg", ".wma", ".aac", ".wav"}
CONTAINER_DIRS = {"amazon mp3", "itunes", "various artists", "compilations",
                  "unknown artist", "music", "downloads", "mp3", "plexvideo"}

# Krumhansl-Schmuckler key profiles (major, minor), normalized at use time.
KS_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KS_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def slugify(name: str) -> str:
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    n = re.sub(r"[^a-zA-Z0-9]+", "-", n).strip("-").lower()
    return n or "unknown"


def load_excludes() -> set[str]:
    f = GRAPH.parent / "seed_exclude.txt"
    if not f.exists():
        return set()
    return {ln.split("#", 1)[0].strip() for ln in f.read_text().splitlines()
            if ln.split("#", 1)[0].strip()}


def scan_artists(music: Path) -> dict[str, list[Path]]:
    """artist_name -> list of audio file paths."""
    tracks: dict[str, list[Path]] = {}
    for dirpath, _dirs, files in os.walk(music):
        audio = [f for f in files if os.path.splitext(f)[1].lower() in AUDIO_EXT]
        if not audio:
            continue
        rel = Path(dirpath).relative_to(music).parts
        if not rel:
            continue
        top = rel[0]
        if top.lower() in CONTAINER_DIRS:
            if len(rel) < 2:
                continue
            artist = rel[1]
        else:
            artist = rel[0]
        tracks.setdefault(artist, []).extend(Path(dirpath) / f for f in audio)
    return tracks


def estimate_key(chroma_mean: np.ndarray) -> str:
    """Correlate the mean chroma against rotated KS profiles; pick the best."""
    best = (-2.0, "?")
    for i in range(12):
        for prof, mode in ((KS_MAJOR, "major"), (KS_MINOR, "minor")):
            r = np.corrcoef(chroma_mean, np.roll(prof, i))[0, 1]
            if r > best[0]:
                best = (r, f"{NOTES[i]} {mode}")
    return best[1]


def analyze_track(path: Path) -> dict | None:
    import librosa
    try:
        # 45s from 30s in — skip fade-ins, capture the song's body.
        y, sr = librosa.load(str(path), sr=22050, mono=True, offset=30.0, duration=45.0)
        if y.size < sr:  # track shorter than the offset; grab from the top
            y, sr = librosa.load(str(path), sr=22050, mono=True, duration=45.0)
        if y.size < sr:
            return None
        tempo = float(np.atleast_1d(librosa.feature.tempo(y=y, sr=sr))[0])
        cent = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr)))
        bw = float(np.mean(librosa.feature.spectral_bandwidth(y=y, sr=sr)))
        rolloff = float(np.mean(librosa.feature.spectral_rolloff(y=y, sr=sr)))
        zcr = float(np.mean(librosa.feature.zero_crossing_rate(y)))
        rms = librosa.feature.rms(y=y)[0]
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
        return {
            "tempo": round(tempo, 1),
            "key": estimate_key(chroma.mean(axis=1)),
            "brightness": round(cent, 1),
            "bandwidth": round(bw, 1),
            "rolloff": round(rolloff, 1),
            "percussive": round(zcr, 4),
            "energy": round(float(np.mean(rms)), 5),
            "dynamics": round(float(np.std(rms)), 5),
            "mfcc": [round(float(x), 2) for x in mfcc.mean(axis=1)],
        }
    except Exception:
        return None


def pick(paths: list[Path], n: int) -> list[Path]:
    paths = sorted(paths)
    if len(paths) <= n:
        return paths
    step = len(paths) / n
    return [paths[int(i * step)] for i in range(n)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("music", nargs="?", default=str(Path.home() / "Music"))
    ap.add_argument("--tracks", type=int, default=2, help="tracks sampled per artist")
    ap.add_argument("--only-graph", action="store_true",
                    help="only analyze artists that already exist in the graph")
    ap.add_argument("--limit", type=int, default=0, help="cap number of artists (0 = all)")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    music = Path(args.music).expanduser()
    if not music.exists():
        sys.exit(f"No such folder: {music}")

    graph_slugs = set()
    if args.only_graph:
        nodes = json.loads((GRAPH / "nodes.json").read_text())
        graph_slugs = {n["id"] for n in nodes}

    done: dict[str, dict] = {}
    if OUT.exists():
        done = json.loads(OUT.read_text())
        print(f"resuming: {len(done)} artists already analyzed")

    excludes = load_excludes()
    artists = scan_artists(music)
    # Most-owned first — that's where your taste is concentrated.
    order = sorted(artists.items(), key=lambda kv: -len(kv[1]))

    todo = []
    for name, paths in order:
        slug = slugify(name)
        if slug in excludes or slug in done:
            continue
        if args.only_graph and slug not in graph_slugs:
            continue
        todo.append((name, slug, paths))
    if args.limit:
        todo = todo[:args.limit]

    print(f"{len(todo)} artists to analyze ({args.tracks} tracks each)\n")
    if not args.write:
        for name, slug, paths in todo[:40]:
            print(f"  would analyze {slug} ({len(paths)} tracks available)")
        print("\n(dry run -- pass --write to analyze and save)")
        return 0

    for i, (name, slug, paths) in enumerate(todo, 1):
        feats = [f for p in pick(paths, args.tracks) if (f := analyze_track(p))]
        if not feats:
            print(f"  [{i}/{len(todo)}] {slug}: no analyzable audio, skipped")
            continue
        agg = {
            "name": name,
            "tracks_analyzed": len(feats),
            "tempo": round(float(np.median([f["tempo"] for f in feats])), 1),
            "key": max((f["key"] for f in feats), key=lambda k: [f["key"] for f in feats].count(k)),
            "brightness": round(float(np.mean([f["brightness"] for f in feats])), 1),
            "bandwidth": round(float(np.mean([f["bandwidth"] for f in feats])), 1),
            "rolloff": round(float(np.mean([f["rolloff"] for f in feats])), 1),
            "percussive": round(float(np.mean([f["percussive"] for f in feats])), 4),
            "energy": round(float(np.mean([f["energy"] for f in feats])), 5),
            "dynamics": round(float(np.mean([f["dynamics"] for f in feats])), 5),
            "mfcc": [round(float(x), 2) for x in np.mean([f["mfcc"] for f in feats], axis=0)],
        }
        done[slug] = agg
        print(f"  [{i}/{len(todo)}] {slug}: {agg['tempo']} BPM, {agg['key']}, "
              f"{agg['brightness']:.0f}Hz")
        if i % 10 == 0:                       # checkpoint — resumable
            OUT.write_text(json.dumps(done, indent=2) + "\n")

    OUT.write_text(json.dumps(done, indent=2) + "\n")
    print(f"\nwrote {len(done)} artist fingerprints to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
