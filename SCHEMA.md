# Bands of Mine — Graph Schema

The graph is **temporal**: every edge carries the time window(s) during which it was live.
This is what lets you scrub a slider to 1972 and see the network as it actually stood.

## Nodes — `data/graph/nodes.json`

```jsonc
{
  "id": "neil-young",              // stable kebab-case slug, the primary key
  "name": "Neil Young",
  "kind": "person",                // person | band | label | venue | scene
  "born": 1945,                    // year, or null
  "died": null,
  "origin": "Toronto, Canada",
  "roles": ["guitar", "vocals", "songwriter", "harmonica"],
  "genres": ["folk rock", "country rock", "hard rock", "grunge"],
  "mbid": null,                    // MusicBrainz UUID, filled by scripts/fetch_musicbrainz.py
  "spotify_id": "6v8FB84lnmJs434UJf2Mrm",
  "seed": true,                    // true = one of *your* bands, an entry point to the graph
  "sources": ["neil-young.spotify"]
}
```

`kind: "band"` nodes use `born`/`died` as formed/dissolved years.

## Edges — `data/graph/edges.json`

```jsonc
{
  "source": "neil-young",
  "target": "buffalo-springfield",
  "type": "member_of",
  "intervals": [[1966, 1968], [2010, 2011]],  // [start, end]; end null == ongoing
  "weight": 1.0,                  // 0..1 strength of the tie
  "note": "Founded with Stephen Stills after meeting in L.A.; quit repeatedly.",
  "sources": ["neil-young.spotify"]
}
```

### Edge types

| type | meaning | direction |
|---|---|---|
| `member_of` | person was in a band | person → band |
| `collaborated_with` | recorded/toured together, not a formal membership | symmetric |
| `influenced_by` | A cites or audibly draws on B | A → B |
| `produced` | production credit | producer → artist |
| `signed_to` | recording contract | artist → label |
| `covered` | recorded another's song | coverer → author |
| `spun_off_from` | band lineage / offshoot | new → old |
| `mentored` | gave exposure, brought along | elder → younger |

`influenced_by` and `mentored` are the edges that make this an *influence* diagram rather
than a personnel chart. They are the hardest to source and the most valuable.

## Provenance

Every node and edge carries `sources: []` — a list of bio slugs (`neil-young.spotify`) or
`musicbrainz`. Never add a fact you can't attribute. When the graph surprises you, you want
to be able to ask "where did that come from?"

## Why raw bios are gitignored

`data/raw/` holds copyrighted third-party text (AllMusic/Rovi via Spotify, Wikipedia, etc.).
It is a local reading cache. Only the **extracted facts** — dates, names, relationships —
land in `data/graph/`. Facts are not copyrightable; the prose is.
