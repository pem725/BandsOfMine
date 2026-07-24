#!/usr/bin/env python3
"""Turn per-artist sonic fingerprints into a map of convergence and divergence.

Reads data/graph/audio_features.json (from analyze_audio.py) and computes, using
only numpy:

  * a standardized feature space (tempo, brightness, energy, grit, dynamics, and
    13 MFCC timbre coefficients)
  * a 2-D projection (PCA) so the whole library can be seen at once
  * k sonic clusters -- the "families" your taste falls into
  * each artist's nearest sonic neighbours (where the music CONVERGES)
  * a uniqueness score (how far an artist sits from everyone -- where it DIVERGES)

Writes data/graph/sonic.json for the sonic-map view. These are derived acoustic
measurements, safe to commit.

  .venv-audio/bin/python scripts/sonic_analysis.py            # or system python; only needs numpy
  python3 scripts/sonic_analysis.py --clusters 6
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "data" / "graph"
FEATURES = GRAPH / "audio_features.json"
OUT = GRAPH / "sonic.json"

# Scalar features and whether to log-compress (Hz measures are heavy-tailed).
SCALARS = [
    ("tempo", False), ("brightness", True), ("bandwidth", True),
    ("rolloff", True), ("percussive", False), ("energy", True), ("dynamics", True),
]


def build_matrix(feats: dict) -> tuple[list[str], np.ndarray]:
    slugs = sorted(feats)
    rows = []
    for s in slugs:
        f = feats[s]
        vec = []
        for name, logc in SCALARS:
            v = float(f.get(name, 0.0) or 0.0)
            vec.append(np.log1p(v) if logc else v)
        vec.extend(float(x) for x in f.get("mfcc", [0] * 13))
        rows.append(vec)
    return slugs, np.array(rows, dtype=float)


def standardize(X: np.ndarray) -> np.ndarray:
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd[sd == 0] = 1.0
    return (X - mu) / sd


def pca_2d(Z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    U, S, Vt = np.linalg.svd(Z - Z.mean(axis=0), full_matrices=False)
    coords = U[:, :2] * S[:2]
    return coords, Vt[:2]


def kmeans(Z: np.ndarray, k: int, iters: int = 100) -> np.ndarray:
    np.random.seed(0)  # deterministic clustering run-to-run
    # k-means++ init
    centers = [Z[np.random.randint(len(Z))]]
    for _ in range(1, k):
        d = np.min([np.sum((Z - c) ** 2, axis=1) for c in centers], axis=0)
        probs = d / d.sum()
        centers.append(Z[np.random.choice(len(Z), p=probs)])
    C = np.array(centers)
    labels = np.zeros(len(Z), dtype=int)
    for _ in range(iters):
        dists = np.linalg.norm(Z[:, None] - C[None], axis=2)
        new = dists.argmin(axis=1)
        if (new == labels).all():
            break
        labels = new
        for j in range(k):
            if (labels == j).any():
                C[j] = Z[labels == j].mean(axis=0)
    return labels


def describe_cluster(feats: dict, members: list[str]) -> str:
    """A short human label from the cluster's average tempo/brightness/energy."""
    tempos = [feats[m]["tempo"] for m in members]
    brights = [feats[m]["brightness"] for m in members]
    t, b = float(np.median(tempos)), float(np.median(brights))
    pace = "slow" if t < 95 else "midtempo" if t < 125 else "driving"
    tone = "warm/dark" if b < 2100 else "balanced" if b < 2700 else "bright"
    return f"{pace}, {tone} (~{t:.0f} BPM)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clusters", type=int, default=6)
    ap.add_argument("--neighbors", type=int, default=6)
    args = ap.parse_args()

    if not FEATURES.exists():
        sys.exit("No audio_features.json yet — run analyze_audio.py first.")
    feats = json.loads(FEATURES.read_text())
    if len(feats) < args.clusters + 2:
        sys.exit(f"Only {len(feats)} artists analyzed; need more before clustering.")

    slugs, X = build_matrix(feats)
    Z = standardize(X)
    coords, axes = pca_2d(Z)
    k = min(args.clusters, len(slugs) // 2)
    labels = kmeans(Z, k)

    # Cosine similarity in standardized space -> nearest neighbours.
    norm = Z / (np.linalg.norm(Z, axis=1, keepdims=True) + 1e-9)
    sim = norm @ norm.T
    np.fill_diagonal(sim, -1)

    # Uniqueness: mean distance to the k nearest. Higher = more of an outlier.
    dist = np.linalg.norm(Z[:, None] - Z[None], axis=2)
    np.fill_diagonal(dist, np.inf)
    knn = np.sort(dist, axis=1)[:, :args.neighbors]
    uniq = knn.mean(axis=1)
    uniq = (uniq - uniq.min()) / (uniq.max() - uniq.min() + 1e-9)

    artists = {}
    for i, s in enumerate(slugs):
        order = np.argsort(-sim[i])[:args.neighbors]
        artists[s] = {
            "name": feats[s].get("name", s),
            "x": round(float(coords[i, 0]), 3),
            "y": round(float(coords[i, 1]), 3),
            "cluster": int(labels[i]),
            "uniqueness": round(float(uniq[i]), 3),
            "tempo": feats[s]["tempo"],
            "key": feats[s]["key"],
            "brightness": feats[s]["brightness"],
            "energy": feats[s]["energy"],
            "neighbors": [[slugs[j], round(float(sim[i, j]), 3)] for j in order],
        }

    clusters = {}
    for j in range(k):
        members = [slugs[i] for i in range(len(slugs)) if labels[i] == j]
        clusters[str(j)] = {
            "label": describe_cluster(feats, members),
            "size": len(members),
            "members": sorted(members, key=lambda m: -artists[m]["uniqueness"]),
        }

    # Most unique (divergent) and tightest convergent pairs, for the writeup.
    most_unique = sorted(slugs, key=lambda s: -artists[s]["uniqueness"])[:15]
    pairs = []
    for i in range(len(slugs)):
        j = int(np.argmax(sim[i]))
        if i < j:
            pairs.append((slugs[i], slugs[j], round(float(sim[i, j]), 3)))
    pairs.sort(key=lambda p: -p[2])

    OUT.write_text(json.dumps({
        "artists": artists,
        "clusters": clusters,
        "n": len(slugs),
    }, indent=2) + "\n")

    print(f"{len(slugs)} artists mapped into {k} sonic clusters -> {OUT.relative_to(ROOT)}\n")
    for j in range(k):
        c = clusters[str(j)]
        sample = ", ".join(feats[m].get("name", m) for m in c["members"][:6])
        print(f"  cluster {j}: {c['label']}  ({c['size']})")
        print(f"     {sample}")
    print("\n  most sonically unique (they diverge from everything):")
    for s in most_unique[:8]:
        a = artists[s]
        print(f"    {a['name']}: {a['tempo']:.0f} BPM, {a['key']}, uniqueness {a['uniqueness']:.2f}")
    print("\n  tightest convergences (near-identical sonic signatures):")
    for a, b, sc in pairs[:8]:
        print(f"    {feats[a].get('name',a)}  ~  {feats[b].get('name',b)}   ({sc:.2f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
