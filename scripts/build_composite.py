#!/usr/bin/env python3
"""Fuse every dimension into one composite similarity, then a network from it.

For each pair of your owned artists we measure similarity four ways and blend:

  influence  Jaccard of shared taproots (who they both draw on)        w=0.35
  sound      cosine of audio fingerprints (tempo/tone/energy/timbre)   w=0.25
  style      Jaccard of genre tags                                     w=0.20
  background era (birth-year proximity) + geography (shared origin)    w=0.20

Each pair blends only the dimensions BOTH artists have data for (weights
renormalized), so sparse artists still connect on what's known. We keep each
artist's top-K most-similar neighbours above a floor, and record the per-
dimension breakdown so the view can explain *why* any link exists.

  python3 scripts/build_composite.py            # summary
  python3 scripts/build_composite.py --write
"""
import json, math, re, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "data" / "graph"
OWN_MIN = 12
TOPK = 8
FLOOR = 0.28
WEIGHTS = {"influence": 0.35, "sound": 0.25, "style": 0.20, "background": 0.20}

SONIC_FEATS = ["tempo", "brightness", "bandwidth", "rolloff", "percussive", "energy", "dynamics"]


def jaccard(a, b):
    if not a or not b:
        return None
    u = len(a | b)
    return len(a & b) / u if u else None


def main() -> int:
    write = "--write" in sys.argv
    nodes = {n["id"]: n for n in json.loads((GRAPH / "nodes.json").read_text())}
    edges = json.loads((GRAPH / "edges.json").read_text())
    sonic = {}
    af = GRAPH / "audio_features.json"      # full per-artist fingerprints
    if af.exists():
        sonic = json.loads(af.read_text())

    inf = defaultdict(set)
    for e in edges:
        if e["type"] == "influenced_by":
            inf[e["source"]].add(e["target"])

    # skip compilation / classical-collection / misc noise nodes
    excl = set()
    ex = GRAPH.parent / "seed_exclude.txt"
    if ex.exists():
        excl = {l.split("#", 1)[0].strip() for l in ex.read_text().splitlines() if l.split("#", 1)[0].strip()}
    NOISE = re.compile(r"misc|unknown|orchestra|philharmon|broadway|collection|classic-rock-19|"
                       r"segovia|facing-future|duets|cast-recording|various|festival|heifetz|"
                       r"karajan|classics-vol|kink|hykk|rp85|pvdo|el-diablo|cantiga|benedictine|"
                       r"choeur|chapelle|ensemble|consort|quartet|chorale|tenors|romeros|"
                       r"reservoir|soundtrack|motion-picture|original-score|-ost$|greatest-hits")
    def ok(n):
        # Real, catalogued artist with *some* descriptive metadata. A node that has
        # an MBID but no genres and no birth year is a MusicBrainz shell entry
        # (soundtracks, wedding-band compilations) — it can only ever match on
        # sound, which turns it into a hub. Requiring genre|born keeps the
        # composite honest across all four dimensions.
        return (n["id"] not in excl and not NOISE.search(n["id"]) and n.get("mbid")
                and (n.get("genres") or n.get("born")))

    # the artists we map: your owned collection, real catalogued artists only
    # (MBID requirement strips compilation/soundtrack/"Time Life" folder nodes)
    A = [nid for nid, n in nodes.items() if (n.get("local_tracks") or 0) >= OWN_MIN and ok(n)]

    # ---- standardize sonic vectors, then use DISCRIMINATIVE gaussian distance ----
    vecs = {}
    present = [a for a in A if a in sonic and sonic[a].get("mfcc")]
    if present:
        import statistics as st
        cols = {}
        for f in SONIC_FEATS:
            vals = [sonic[a][f] for a in present]
            mu = sum(vals) / len(vals); sd = st.pstdev(vals) or 1.0
            cols[f] = (mu, sd)
        for a in present:
            v = [(sonic[a][f] - cols[f][0]) / cols[f][1] for f in SONIC_FEATS]
            v += [x / 8.0 for x in sonic[a].get("mfcc", [])]     # scale mfcc to match z-scored feats
            vecs[a] = v
    def dist(a, b):
        va, vb = vecs.get(a), vecs.get(b)
        if not va or not vb:
            return None
        n = min(len(va), len(vb))
        return math.sqrt(sum((va[i] - vb[i]) ** 2 for i in range(n)))
    # scale = median pairwise distance, so "sound similar" means genuinely close
    ds = []
    for i in range(0, len(present), 3):        # sample for speed
        for j in range(i + 1, len(present), 3):
            d = dist(present[i], present[j])
            if d is not None: ds.append(d)
    SCALE = (sorted(ds)[len(ds) // 2] if ds else 1.0) or 1.0
    def sound_sim(a, b):
        d = dist(a, b)
        if d is None:
            return None
        return math.exp(-(d / SCALE) ** 2)

    def country(o):
        if not o:
            return None
        o = o.lower()
        us = ["los angeles", "san francisco", "new york", "boston", "chicago", "nashville",
              "seattle", "atlanta", "austin", "detroit", "brooklyn", "memphis", "new orleans",
              "united states", "california", "texas", "ca", "berkeley", "cleveland"]
        uk = ["london", "birmingham", "liverpool", "manchester", "glasgow", "sheffield",
              "united kingdom", "england", "scotland", "wales", "leeds"]
        if any(k in o for k in us): return "US"
        if any(k in o for k in uk): return "UK"
        if "ireland" in o or "dublin" in o: return "IE"
        if "jamaica" in o or "kingston" in o: return "JM"
        return o

    def bg(a, b):
        na, nb = nodes[a], nodes[b]
        parts, wsum = 0.0, 0.0
        if na.get("born") and nb.get("born"):
            era = math.exp(-abs(na["born"] - nb["born"]) / 25.0)
            parts += era * 0.65; wsum += 0.65
        if na.get("origin") and nb.get("origin"):
            if na["origin"] == nb["origin"]:
                geo = 1.0
            elif country(na["origin"]) and country(na["origin"]) == country(nb["origin"]):
                geo = 0.4
            else:
                geo = 0.0
            parts += geo * 0.35; wsum += 0.35
        return parts / wsum if wsum else None

    def dup(a, b):   # near-duplicate nodes (one name inside the other)
        na, nb = nodes[a]["name"].lower(), nodes[b]["name"].lower()
        return na in nb or nb in na

    # ---- pairwise: record every dimension for every pair ----
    perdim = {d: defaultdict(list) for d in WEIGHTS}   # dim -> artist -> [(other, sim)]
    pair_bd = {}                                        # (a,b) -> {dim: sim} + composite
    for i in range(len(A)):
        for j in range(i + 1, len(A)):
            a, b = A[i], A[j]
            if dup(a, b):
                continue
            dims = {
                "influence": jaccard(inf.get(a), inf.get(b)),
                "sound": sound_sim(a, b),
                "style": jaccard(set(nodes[a].get("genres") or []), set(nodes[b].get("genres") or [])),
                "background": bg(a, b),
            }
            avail = {k: v for k, v in dims.items() if v is not None}
            if not avail:
                continue
            wsum = sum(WEIGHTS[k] for k in avail)
            comp = sum(WEIGHTS[k] * v for k, v in avail.items()) / wsum
            pair_bd[(a, b)] = {"by": {k: round(v, 2) for k, v in avail.items()}, "comp": round(comp, 3)}
            for d, v in avail.items():
                perdim[d][a].append((b, v)); perdim[d][b].append((a, v))

    # Union of each artist's top-K links PER DIMENSION, but keep an edge only when
    # BOTH artists rank each other (mutual k-NN). Mutual-kNN kills "hub" nodes: a
    # sonically generic node lands in everyone's top-K, but it ranks only its own
    # true neighbours back, so the spurious inbound links drop out.
    PERDIM_K = 4
    PERDIM_FLOOR = {"influence": 0.15, "sound": 0.55, "style": 0.34, "background": 0.55}
    topset = {d: {} for d in WEIGHTS}      # dim -> artist -> set(top-K neighbours)
    for d in WEIGHTS:
        for a, lst in perdim[d].items():
            topset[d][a] = {b for b, v in sorted(lst, key=lambda t: -t[1])[:PERDIM_K] if v >= PERDIM_FLOOR[d]}
    kept = {}
    for d in WEIGHTS:
        for a, nbrs in topset[d].items():
            for b in nbrs:
                if a in topset[d].get(b, ()):     # mutual: b also ranks a
                    kept.setdefault(tuple(sorted((a, b))), set()).add(d)

    out_edges = []
    for (a, b), dset in kept.items():
        info = pair_bd.get((a, b)) or pair_bd.get((b, a))
        if not info:
            continue
        # the driver = highest-scoring dimension that selected this edge
        driver = max(dset, key=lambda d: info["by"].get(d, 0))
        out_edges.append({"source": a, "target": b, "sim": info["comp"],
                          "by": info["by"], "driver": driver,
                          "via": sorted(dset)})

    # ---- personnel layer: the strongest connection of all — actually worked together ----
    # Similarity says "sounds/feels alike"; personnel says "were in the same room".
    # We surface band lineups (every member of an owned band) plus collaborations and
    # spin-offs. A member who belongs to two of your bands becomes a bridge between them.
    band_members = defaultdict(set)     # band -> {member persons}
    memb = defaultdict(set)             # person -> {bands}
    collab = []                         # (a, b, type)
    for e in edges:
        if e["type"] == "member_of":
            band_members[e["target"]].add(e["source"]); memb[e["source"]].add(e["target"])
        elif e["type"] in ("collaborated_with", "spun_off_from"):
            collab.append((e["source"], e["target"], e["type"]))

    ownset = set(A)
    owned_bands = {a for a in A if nodes[a].get("kind") == "band"}
    # A member of an owned band becomes a graph node if they either BRIDGE two of your
    # bands (the "worked with others" signal) or are a documented artist in their own
    # right (genres+born — session-only players usually aren't). Everyone else still
    # appears in the band's full lineup shown in the side panel, just not as a dot, so
    # the graph stays legible instead of drowning in 1000+ sidemen.
    def member_ok(p):
        return p in nodes and p not in ownset and not NOISE.search(p) and p not in excl
    def notable(p):
        n = nodes[p]
        return bool(n.get("genres")) and bool(n.get("born"))
    connectors = set()
    for p, bands in memb.items():
        if not member_ok(p) or not (bands & owned_bands):
            continue
        if len(bands & owned_bands) >= 2 or notable(p):
            connectors.add(p)
    nodeset = ownset | connectors
    # full lineups (every member, node or not) for the side panel
    lineups = {b: sorted((nodes[p]["name"] for p in band_members[b] if p in nodes))
               for b in owned_bands if band_members[b]}

    seenp = set()
    pers = []
    def addp(a, b, note):
        if a == b or a not in nodeset or b not in nodeset:
            return
        k = tuple(sorted((a, b)))
        if k in seenp:
            return
        seenp.add(k)
        pers.append({"source": a, "target": b, "sim": 0.55, "by": {}, "note": note,
                     "driver": "personnel", "via": ["personnel"]})
    for b in owned_bands:                          # band ↔ each of its members
        for p in band_members[b]:
            addp(p, b, "member")
    for a, b, t in collab:                          # collaborations & spin-offs
        addp(a, b, "collaborated" if t == "collaborated_with" else "spun off")
    out_edges += pers

    connected = {e["source"] for e in out_edges} | {e["target"] for e in out_edges}
    def nrec(a):
        n = nodes[a]
        return {"id": a, "name": n["name"], "tracks": n.get("local_tracks") or 0,
                "genre": (n.get("genres") or [None])[0], "kind": n.get("kind"),
                "origin": n.get("origin"), "born": n.get("born"),
                "role": "owned" if a in ownset else "connector"}
    out_nodes = [nrec(a) for a in nodeset if a in connected]

    drv = defaultdict(int)
    for e in out_edges:
        drv[e["driver"]] += 1
    nconn = sum(1 for n in out_nodes if n["role"] == "connector")
    print(f"{len(out_nodes)} nodes ({len(out_nodes)-nconn} owned + {nconn} connectors), {len(out_edges)} links")
    print(f"  driven by: " + ", ".join(f"{k} {c}" for k, c in sorted(drv.items(), key=lambda kv: -kv[1])))
    print(f"  (sonic data for {len(vecs)} of them)")

    if not write:
        # strongest *similarity* pairs (personnel edges are factual, not scored)
        sims_only = [e for e in out_edges if e["driver"] != "personnel"]
        top = sorted(sims_only, key=lambda e: -e["sim"])[:8]
        print("\n  strongest similarity pairs:")
        for e in top:
            print(f"    {nodes[e['source']]['name']:22} ~ {nodes[e['target']]['name']:22} "
                  f"{e['sim']}  ({e['driver']})")
        # show a band and its now-visible lineup
        for band in ["eagles", "the-doobie-brothers", "genesis"]:
            if band in nodes:
                lineup = [nodes[e["source"]]["name"] for e in out_edges
                          if e.get("note") == "member" and e["target"] == band]
                print(f"\n  {nodes[band]['name']} lineup ({len(lineup)}): {', '.join(lineup[:10])}")
        print("\n(preview -- pass --write)")
        return 0
    keep_lineups = {b: m for b, m in lineups.items() if b in connected}
    (GRAPH / "composite.json").write_text(
        json.dumps({"nodes": out_nodes, "edges": out_edges, "lineups": keep_lineups}, indent=2) + "\n")
    print("\nwrote data/graph/composite.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
