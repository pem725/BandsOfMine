#!/usr/bin/env python3
"""Place your owned artists on a world map by origin (no geocoding API).

Origins came from MusicBrainz as place names. We map the common ones to
coordinates via a built-in gazetteer (music cities + countries), with sensible
disambiguation for your collection (Athens=GA, Venice=CA, Birmingham=UK...).
Writes data/graph/geo.json for the geographic view.

  python3 scripts/build_geo.py            # coverage report
  python3 scripts/build_geo.py --write
"""
import json, re, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "data" / "graph"

# place -> [lon, lat]  (D3 geo order). Approximate; fine for a dot map.
GAZ = {
    # US cities
    "los angeles": [-118.24,34.05], "san francisco": [-122.42,37.77],
    "new york": [-74.01,40.71], "brooklyn": [-73.94,40.68], "the bronx": [-73.86,40.84],
    "boston": [-71.06,42.36], "chicago": [-87.63,41.88], "nashville": [-86.78,36.16],
    "seattle": [-122.33,47.61], "cleveland": [-81.69,41.50], "atlanta": [-84.39,33.75],
    "austin": [-97.74,30.27], "houston": [-95.37,29.76], "dallas": [-96.80,32.78],
    "new orleans": [-90.07,29.95], "memphis": [-90.05,35.15], "berkeley": [-122.27,37.87],
    "san jose": [-121.89,37.34], "portland": [-122.68,45.52], "las vegas": [-115.14,36.17],
    "newark": [-74.17,40.74], "jacksonville": [-81.66,30.33], "macon": [-83.63,32.84],
    "athens": [-83.38,33.96], "chapel hill": [-79.06,35.91], "charlottesville": [-78.48,38.03],
    "burlington": [-73.21,44.48], "washington, d.c.": [-77.04,38.90], "washington": [-77.04,38.90],
    "venice": [-118.47,33.99], "long branch": [-74.00,40.30], "ripley": [-88.95,35.75],
    "detroit": [-83.05,42.33], "philadelphia": [-75.16,39.95], "san diego": [-117.16,32.72],
    "denver": [-104.99,39.74], "minneapolis": [-93.27,44.98], "tulsa": [-95.99,36.15],
    "miami": [-80.19,25.76], "phoenix": [-112.07,33.45],
    # UK / Ireland
    "london": [-0.13,51.51], "liverpool": [-2.99,53.41], "birmingham": [-1.90,52.48],
    "glasgow": [-4.25,55.86], "manchester": [-2.24,53.48], "dublin": [-6.26,53.35],
    "paddington": [-0.18,51.52], "sheffield": [-1.47,53.38], "belfast": [-5.93,54.60],
    # world cities
    "toronto": [-79.38,43.65], "kingston": [-76.79,17.99], "sydney": [151.21,-33.87],
    "paris": [2.35,48.86], "lisbon": [-9.14,38.72],
    # countries / regions (coarse centroids)
    "united states": [-98.5,39.8], "united kingdom": [-1.5,52.5], "england": [-1.5,52.5],
    "ireland": [-8.0,53.4], "scotland": [-4.2,56.5], "canada": [-106,56], "australia": [133,-25],
    "jamaica": [-77.3,18.1], "france": [2.3,46.6], "germany": [10.4,51.2], "italy": [12.5,42.8],
    "portugal": [-8.2,39.5], "south africa": [24,-29], "colorado": [-105.5,39.0],
    "california": [-119.4,36.8], "texas": [-99.9,31.9], "hawaii": [-155.6,19.9],
    # more cities from the long tail
    "manhattan": [-73.97,40.78], "edinburgh": [-3.19,55.95], "leeds": [-1.55,53.80],
    "duluth": [-92.10,46.79], "salzburg": [13.05,47.81], "pomona": [-117.75,34.06],
    "richmond": [-77.44,37.54], "buffalo": [-78.88,42.89], "akron": [-81.52,41.08],
    "tacoma": [-122.44,47.25], "gainesville": [-82.32,29.65], "muscle shoals": [-87.67,34.74],
    "tupelo": [-88.70,34.26], "lubbock": [-101.86,33.58], "bakersfield": [-119.02,35.37],
    "oakland": [-122.27,37.80], "sacramento": [-121.49,38.58], "baltimore": [-76.61,39.29],
    "pittsburgh": [-79.996,40.44], "cincinnati": [-84.51,39.10], "st. louis": [-90.20,38.63],
    "st louis": [-90.20,38.63], "kansas city": [-94.58,39.10], "oklahoma city": [-97.52,35.47],
    "bristol": [-2.59,51.45], "cardiff": [-3.18,51.48], "cork": [-8.47,51.90],
    "montreal": [-73.57,45.50], "vancouver": [-123.12,49.28], "winnipeg": [-97.14,49.90],
    # countries / regions
    "netherlands": [5.3,52.1], "spain": [-3.7,40.4], "sweden": [15.2,62.0],
    "norway": [8.5,61.0], "brazil": [-51.9,-14.2], "mexico": [-102.6,23.6],
    "wales": [-3.8,52.1], "new zealand": [172.0,-41.0], "cuba": [-79.0,21.5],
    "scotland": [-4.2,56.5], "northern ireland": [-6.7,54.7], "japan": [138.3,36.2],
}


def coords(origin):
    o = origin.strip().lower()
    # strip a trailing ", XX" US-state suffix ONLY after a comma (so "San Francisco" stays intact)
    o = re.sub(r"\s*,\s*[a-z]{2}\.?$", "", o).strip()
    if o in GAZ:
        return GAZ[o]
    # fall back: any gazetteer key contained in the string, or country words
    for k, v in GAZ.items():
        if k in o:
            return v
    return None


def main() -> int:
    write = "--write" in sys.argv
    nodes = json.loads((GRAPH / "nodes.json").read_text())
    owned = [n for n in nodes if (n.get("local_tracks") or 0) > 0 or n.get("play_weight") or n.get("seed")]

    pts, missed = [], Counter()
    for n in owned:
        if not n.get("origin"):
            continue
        c = coords(n["origin"])
        if not c:
            missed[n["origin"]] += 1
            continue
        pts.append({"id": n["id"], "name": n["name"], "lon": c[0], "lat": c[1],
                    "place": n["origin"], "tracks": n.get("local_tracks") or 0,
                    "genre": (n.get("genres") or [None])[0]})

    placed = len(pts)
    total = sum(1 for n in owned if n.get("origin"))
    print(f"placed {placed}/{total} artists-with-origin ({len(missed)} distinct places unmapped)")
    if missed:
        print("  top unmapped:", ", ".join(f"{p}({c})" for p, c in missed.most_common(10)))
    if not write:
        print("(preview -- pass --write)")
        return 0
    (GRAPH / "geo.json").write_text(json.dumps({"points": pts}, indent=2) + "\n")
    print(f"wrote data/graph/geo.json ({placed} points)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
