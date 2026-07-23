# Adding an artist to the graph

The workflow is deliberately two-track: **paste raw bios locally**, **commit only facts**.

## 1. Capture the bio (local only)

Paste or capture the artist's bio into `data/raw/bios/<slug>.<source>.txt` with a
frontmatter header like the Neil Young file. This directory is **gitignored** — it's your
reading cache, not a publishable asset.

If a bio is on Spotify and won't copy/paste, Claude can read it via the Chrome browser
tools (that's how the seed bio was captured).

## 2. Extract facts into the graph

Read the bio and add the facts to `data/graph/`:

### Nodes (`nodes.json`)

- `id` — stable kebab-case slug, the primary key. `neil-young`, `crazy-horse`.
- `kind` — `person` | `band` | `label` | `venue` | `scene`.
- `born`/`died` — for bands, this is formed/dissolved. Year only, or `null`.
- `seed: true` — set this only for **your** artists, the entry points to the graph.
- `sources` — the bio slug, e.g. `["neil-young.spotify"]`. Never add an unattributed node.

### Edges (`edges.json`)

- `type` — see the table in [`../SCHEMA.md`](../SCHEMA.md).
- `intervals` — `[[start, end], ...]`. `end: null` means ongoing. A person can be in a
  band across **disjoint** windows (reunions) — that's why it's a list.
- `weight` — 0..1, how strong the tie is. A founding member is ~1.0; a one-track guest ~0.3.
- `note` — the human detail. This is where "quit several times before leaving in 1968" or
  "his death directly inspired Tonight's the Night" lives. Keep it short and factual.
- `sources` — same rule.

**Reuse existing node ids.** If Bob Dylan's bio mentions The Band, and `the-band` already
exists from Neil Young's bio, point the edge at the existing id — don't make a duplicate.
That merging is exactly how the seed graphs connect into one network.

## 3. Optionally enrich from MusicBrainz

```bash
python3 scripts/fetch_musicbrainz.py --expand <slug>          # preview
python3 scripts/fetch_musicbrainz.py --expand <slug> --write  # apply
```

MusicBrainz gives you band membership with real begin/end dates for free. It will also
surface **alias collisions** — e.g. it returns `crosby-stills-nash-young` where we already
have `csny`. When that happens, keep our canonical id, delete the duplicate node, and
repoint its edges. (An alias-merge helper is a good next script to write.)

## 4. Validate, then commit

```bash
python3 scripts/validate_graph.py    # must print "Graph is valid"
```

The validator checks for dangling edges, backwards intervals, self-loops, duplicate edges
(merge the intervals instead), impossible dates, and endpoint-kind mismatches (you can't
be `member_of` a label). Fix every ERROR before committing. Warnings (orphan nodes,
relationships predating a birth year) are worth a look but won't block you.

## Curation notes

- **Prefer specificity in time.** "1966–1968, then 2010–2011" tells a story that "1966–2011"
  erases. Reunions and gaps are the interesting part.
- **`influenced_by` and `mentored` are the soul of the project.** They're subjective and
  hard to source — cite the bio phrase that supports them in the `note`.
- **Direction matters.** `influenced_by` points from the younger/derivative artist *to*
  the source. `A influenced_by B` means B shaped A.
