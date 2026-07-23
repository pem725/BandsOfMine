#!/usr/bin/env python3
"""Validate data/graph/*.json against SCHEMA.md.

Run this before every commit. A graph that lies to you is worse than no graph.
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "data" / "graph"

NODE_KINDS = {"person", "band", "label", "venue", "scene"}
EDGE_TYPES = {
    "member_of",
    "collaborated_with",
    "influenced_by",
    "produced",
    "signed_to",
    "covered",
    "spun_off_from",
    "mentored",
}
# Which node kinds each edge type may connect (source_kinds, target_kinds).
# None means "any kind".
EDGE_ENDPOINTS = {
    "member_of": ({"person"}, {"band"}),
    "signed_to": ({"person", "band"}, {"label"}),
    "spun_off_from": ({"band"}, {"band"}),
    "produced": ({"person"}, {"person", "band"}),
}

MIN_YEAR, MAX_YEAR = 1900, 2100


def main() -> int:
    nodes = json.loads((GRAPH / "nodes.json").read_text())
    edges = json.loads((GRAPH / "edges.json").read_text())
    errors: list[str] = []
    warnings: list[str] = []

    # --- nodes ---------------------------------------------------------
    by_id: dict[str, dict] = {}
    for n in nodes:
        nid = n.get("id")
        if not nid:
            errors.append(f"node without id: {n}")
            continue
        if nid in by_id:
            errors.append(f"duplicate node id: {nid}")
        by_id[nid] = n
        if n.get("kind") not in NODE_KINDS:
            errors.append(f"{nid}: bad kind {n.get('kind')!r}")
        born, died = n.get("born"), n.get("died")
        for label, y in (("born", born), ("died", died)):
            if y is not None and not (MIN_YEAR <= y <= MAX_YEAR):
                errors.append(f"{nid}: implausible {label} year {y}")
        if born and died and died < born:
            errors.append(f"{nid}: died {died} before born {born}")
        if not n.get("sources"):
            warnings.append(f"{nid}: no sources — unattributed fact")

    # --- edges ---------------------------------------------------------
    seen_edges: set[tuple[str, str, str]] = set()
    for e in edges:
        src, tgt, etype = e.get("source"), e.get("target"), e.get("type")
        tag = f"{src} -{etype}-> {tgt}"
        if src not in by_id:
            errors.append(f"{tag}: dangling source {src!r}")
        if tgt not in by_id:
            errors.append(f"{tag}: dangling target {tgt!r}")
        if src == tgt:
            errors.append(f"{tag}: self-loop")
        if etype not in EDGE_TYPES:
            errors.append(f"{tag}: unknown edge type {etype!r}")
        key = (src, tgt, etype)
        if key in seen_edges:
            errors.append(f"{tag}: duplicate edge (merge the intervals instead)")
        seen_edges.add(key)

        if etype in EDGE_ENDPOINTS and src in by_id and tgt in by_id:
            ok_src, ok_tgt = EDGE_ENDPOINTS[etype]
            if by_id[src].get("kind") not in ok_src:
                errors.append(
                    f"{tag}: source kind {by_id[src].get('kind')!r} invalid for {etype}"
                )
            if by_id[tgt].get("kind") not in ok_tgt:
                errors.append(
                    f"{tag}: target kind {by_id[tgt].get('kind')!r} invalid for {etype}"
                )

        w = e.get("weight")
        if not isinstance(w, (int, float)) or not (0.0 <= w <= 1.0):
            errors.append(f"{tag}: weight {w!r} outside 0..1")

        intervals = e.get("intervals") or []
        if not intervals:
            errors.append(f"{tag}: no intervals — a temporal graph needs time")
        prev_end = None
        for iv in intervals:
            if not (isinstance(iv, list) and len(iv) == 2):
                errors.append(f"{tag}: malformed interval {iv!r}")
                continue
            start, end = iv
            if start is None:
                errors.append(f"{tag}: interval with null start")
                continue
            if not (MIN_YEAR <= start <= MAX_YEAR):
                errors.append(f"{tag}: implausible start {start}")
            if end is not None:
                if end < start:
                    errors.append(f"{tag}: interval [{start}, {end}] runs backwards")
                if not (MIN_YEAR <= end <= MAX_YEAR):
                    errors.append(f"{tag}: implausible end {end}")
            if prev_end is not None and start < prev_end:
                errors.append(f"{tag}: intervals overlap or are unsorted at {iv}")
            prev_end = end if end is not None else MAX_YEAR

            # A relationship shouldn't predate either party's existence.
            for side in (src, tgt):
                node = by_id.get(side)
                if node and node.get("born") and start < node["born"]:
                    warnings.append(
                        f"{tag}: interval starts {start}, before {side} exists ({node['born']})"
                    )

    # --- connectivity ---------------------------------------------------
    connected = {e["source"] for e in edges} | {e["target"] for e in edges}
    for nid in by_id:
        if nid not in connected:
            warnings.append(f"{nid}: orphan node, no edges")

    # --- report ---------------------------------------------------------
    kinds = Counter(n.get("kind") for n in nodes)
    types = Counter(e.get("type") for e in edges)
    print(f"{len(nodes)} nodes  {dict(kinds)}")
    print(f"{len(edges)} edges  {dict(types)}")

    for w in warnings:
        print(f"  warn  {w}")
    for err in errors:
        print(f"  ERROR {err}")

    if errors:
        print(f"\n{len(errors)} error(s). Graph is invalid.")
        return 1
    print(f"\nGraph is valid ({len(warnings)} warning(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
