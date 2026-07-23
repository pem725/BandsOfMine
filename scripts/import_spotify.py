#!/usr/bin/env python3
"""Seed the graph from YOUR Spotify listening.

Pulls your top artists, followed artists, and the artists behind your saved
tracks, and merges them into data/graph/nodes.json as seed nodes -- so the
influence graph grows outward from what you actually listen to.

Auth is Authorization Code + PKCE: you need only a Spotify **Client ID**
(no secret), and you approve access in your own browser. Claude never sees
your Spotify password -- the token comes back to a local redirect server.

--------------------------------------------------------------------------
ONE-TIME SETUP
--------------------------------------------------------------------------
1. Go to https://developer.spotify.com/dashboard and log in.
2. "Create app". Name/description anything. For "Redirect URI" add EXACTLY:
       http://127.0.0.1:8888/callback
   Check the "Web API" box. Save.
3. Open the app's Settings and copy the "Client ID".
4. Put it where this script can find it -- either:
       export SPOTIFY_CLIENT_ID=xxxxxxxxxxxx
   or drop it in a gitignored file at the repo root named `.spotify_client_id`.

--------------------------------------------------------------------------
USAGE
--------------------------------------------------------------------------
  python3 scripts/import_spotify.py                # authorize + preview (dry run)
  python3 scripts/import_spotify.py --write        # apply to the graph
  python3 scripts/import_spotify.py --top-only      # skip follows/saved, just top artists
  python3 scripts/import_spotify.py --range medium  # short | medium | long (default long)

The first run opens a browser to authorize. The refresh token is cached in
data/cache/spotify_token.json (gitignored) so later runs are non-interactive.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "data" / "graph"
CACHE = ROOT / "data" / "cache"
TOKEN_FILE = CACHE / "spotify_token.json"

REDIRECT_URI = "http://127.0.0.1:8888/callback"
REDIRECT_PORT = 8888
SCOPES = "user-top-read user-follow-read user-library-read"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"


# ----------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------
def slugify(name: str) -> str:
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    n = re.sub(r"[^a-zA-Z0-9]+", "-", n).strip("-").lower()
    return n or "unknown"


def client_id() -> str:
    cid = os.environ.get("SPOTIFY_CLIENT_ID")
    if not cid:
        f = ROOT / ".spotify_client_id"
        if f.exists():
            cid = f.read_text().strip()
    if not cid:
        sys.exit(
            "No Spotify Client ID found. See the SETUP section at the top of this "
            "script:\n  export SPOTIFY_CLIENT_ID=...  (or put it in .spotify_client_id)"
        )
    return cid


def api_get(path: str, token: str, **params) -> dict:
    url = f"{API}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def post_token(data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        TOKEN_URL, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


# ----------------------------------------------------------------------
# OAuth (Authorization Code + PKCE)
# ----------------------------------------------------------------------
class _CallbackHandler(BaseHTTPRequestHandler):
    code = None
    error = None

    def do_GET(self):  # noqa: N802
        q = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(q)
        _CallbackHandler.code = (params.get("code") or [None])[0]
        _CallbackHandler.error = (params.get("error") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        msg = "Authorized. You can close this tab and return to the terminal." \
            if _CallbackHandler.code else f"Authorization failed: {_CallbackHandler.error}"
        self.wfile.write(f"<html><body style='font-family:sans-serif;padding:3em'>"
                         f"<h2>Bands of Mine</h2><p>{msg}</p></body></html>".encode())

    def log_message(self, *_):  # silence the default logging
        pass


def authorize(cid: str) -> dict:
    """Full interactive PKCE flow. Returns a token dict and caches it."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    state = secrets.token_urlsafe(16)

    params = {
        "client_id": cid,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "state": state,
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    print("Opening your browser to authorize Spotify access...")
    print(f"If it doesn't open, paste this into your browser:\n  {url}\n")
    webbrowser.open(url)

    server = HTTPServer(("127.0.0.1", REDIRECT_PORT), _CallbackHandler)
    server.timeout = 300
    print(f"Waiting for the redirect on {REDIRECT_URI} ...")
    while _CallbackHandler.code is None and _CallbackHandler.error is None:
        server.handle_request()
    server.server_close()

    if _CallbackHandler.error:
        sys.exit(f"Spotify authorization failed: {_CallbackHandler.error}")

    tok = post_token({
        "grant_type": "authorization_code",
        "code": _CallbackHandler.code,
        "redirect_uri": REDIRECT_URI,
        "client_id": cid,
        "code_verifier": verifier,
    })
    _save_token(tok)
    return tok


def _save_token(tok: dict) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    tok["obtained_at"] = int(time.time())
    TOKEN_FILE.write_text(json.dumps(tok))


def get_token(cid: str) -> str:
    """Return a valid access token, refreshing or re-authorizing as needed."""
    if TOKEN_FILE.exists():
        tok = json.loads(TOKEN_FILE.read_text())
        age = time.time() - tok.get("obtained_at", 0)
        if age < tok.get("expires_in", 3600) - 60:
            return tok["access_token"]
        if tok.get("refresh_token"):
            try:
                new = post_token({
                    "grant_type": "refresh_token",
                    "refresh_token": tok["refresh_token"],
                    "client_id": cid,
                })
                new.setdefault("refresh_token", tok["refresh_token"])
                _save_token(new)
                return new["access_token"]
            except Exception:
                pass  # fall through to full re-auth
    return authorize(cid)["access_token"]


# ----------------------------------------------------------------------
# gather artists from your account
# ----------------------------------------------------------------------
RANGE_MAP = {"short": "short_term", "medium": "medium_term", "long": "long_term"}


def collect_artists(token: str, time_range: str, top_only: bool) -> dict[str, dict]:
    """Return {spotify_artist_id: {name, genres, weight, why:set}}."""
    found: dict[str, dict] = {}

    def bump(artist: dict, weight: float, why: str):
        aid = artist["id"]
        entry = found.setdefault(aid, {
            "name": artist["name"], "genres": artist.get("genres", []),
            "weight": 0.0, "why": set(),
        })
        entry["weight"] = max(entry["weight"], weight)
        entry["why"].add(why)
        if artist.get("genres"):
            entry["genres"] = artist["genres"]

    # Top artists -- rank informs weight (your #1 is a stronger seed than #50).
    top = api_get("me/top/artists", token, time_range=RANGE_MAP[time_range], limit=50)
    n = len(top.get("items", []))
    for i, a in enumerate(top.get("items", [])):
        bump(a, round(1.0 - 0.5 * (i / max(n - 1, 1)), 3), f"top #{i + 1}")
    print(f"  top artists: {n}")

    if top_only:
        return found

    # Followed artists.
    after = None
    followed = 0
    while True:
        params = {"type": "artist", "limit": 50}
        if after:
            params["after"] = after
        page = api_get("me/following", token, **params).get("artists", {})
        items = page.get("items", [])
        for a in items:
            bump(a, 0.8, "followed")
        followed += len(items)
        after = page.get("cursors", {}).get("after")
        if not after or not items:
            break
    print(f"  followed artists: {followed}")

    # Artists behind saved tracks (first ~200 to stay polite).
    saved_artist_ids: dict[str, int] = {}
    offset = 0
    while offset < 200:
        page = api_get("me/tracks", token, limit=50, offset=offset)
        items = page.get("items", [])
        if not items:
            break
        for it in items:
            for a in it.get("track", {}).get("artists", []):
                saved_artist_ids[a["id"]] = saved_artist_ids.get(a["id"], 0) + 1
        offset += 50
    # Only artists you've saved 2+ tracks from, to cut one-off features.
    strong = [aid for aid, c in saved_artist_ids.items() if c >= 2 and aid not in found]
    for chunk_start in range(0, len(strong), 50):
        ids = strong[chunk_start:chunk_start + 50]
        if not ids:
            break
        full = api_get("artists", token, ids=",".join(ids)).get("artists", [])
        for a in full:
            if a:
                bump(a, 0.6, f"{saved_artist_ids[a['id']]} saved tracks")
    print(f"  artists from saved tracks (2+ tracks): {len(strong)}")

    return found


# ----------------------------------------------------------------------
# merge into the graph
# ----------------------------------------------------------------------
def merge(found: dict[str, dict], write: bool) -> int:
    nodes = json.loads((GRAPH / "nodes.json").read_text())
    by_spotify = {n.get("spotify_id"): n for n in nodes if n.get("spotify_id")}
    by_slug = {n["id"]: n for n in nodes}

    added = 0
    promoted = 0
    for aid, info in sorted(found.items(), key=lambda kv: -kv[1]["weight"]):
        existing = by_spotify.get(aid)
        if not existing:
            # maybe the artist is already in the graph by slug but without a spotify_id
            slug = slugify(info["name"])
            existing = by_slug.get(slug)

        why = ", ".join(sorted(info["why"]))
        if existing:
            changed = []
            if not existing.get("spotify_id"):
                existing["spotify_id"] = aid
                changed.append("linked spotify_id")
            if not existing.get("seed"):
                existing["seed"] = True
                promoted += 1
                changed.append("promoted to seed")
            if info["genres"] and not existing.get("genres"):
                existing["genres"] = info["genres"]
            if "spotify" not in existing.get("sources", []):
                existing.setdefault("sources", []).append("spotify")
            if changed:
                print(f"  ~ {existing['id']}: {', '.join(changed)}  ({why})")
            continue

        node = {
            "id": slugify(info["name"]),
            "name": info["name"],
            "kind": "person",  # Spotify can't tell person vs band; refine later / via MusicBrainz
            "born": None, "died": None, "origin": None,
            "roles": [], "genres": info["genres"],
            "mbid": None, "spotify_id": aid,
            "seed": True,
            "sources": ["spotify"],
        }
        # de-dupe slug collisions
        base = node["id"]
        k = 2
        while node["id"] in by_slug:
            node["id"] = f"{base}-{k}"
            k += 1
        by_slug[node["id"]] = node
        nodes.append(node)
        added += 1
        print(f"  + {node['id']}  (weight {info['weight']}, {why})")

    print(f"\n{added} new seed artists, {promoted} existing nodes promoted to seed.")
    if not write:
        print("(dry run -- pass --write to apply, then run validate_graph.py)")
        return 0

    (GRAPH / "nodes.json").write_text(json.dumps(nodes, indent=2) + "\n")
    print("written. next steps:")
    print("  python3 scripts/validate_graph.py")
    print("  python3 scripts/fetch_musicbrainz.py --resolve   # fix person/band kinds + get mbids")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="apply changes to the graph")
    ap.add_argument("--top-only", action="store_true", help="only top artists, skip follows/saved")
    ap.add_argument("--range", choices=list(RANGE_MAP), default="long",
                    help="time window for top artists (default: long)")
    args = ap.parse_args()

    cid = client_id()
    token = get_token(cid)
    me = api_get("me", token)
    print(f"authorized as: {me.get('display_name') or me.get('id')}\n")

    found = collect_artists(token, args.range, args.top_only)
    print(f"\n{len(found)} distinct artists gathered from your account.\n")
    return merge(found, args.write)


if __name__ == "__main__":
    sys.exit(main())
