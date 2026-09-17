# World Graph — Map Connections & Transitions

---

## map_graph.json format

Produced by `map_graph.py`. The Java game reads this file directly — do not transform it.

```json
{
  "game": "heartgold",
  "nodes": [
    {"id": "MAP_UNION_CAVE_1F", "name": "Union Cave 1F", "map_type": "CAVE"},
    {"id": "MAP_ROUTE_32",      "name": "Route 32",      "map_type": "ROUTE"}
  ],
  "edges": [
    {"a": "MAP_ROUTE_35",       "b": "MAP_NATIONAL_PARK",  "kinds": ["connection"], "bidirectional": true},
    {"a": "MAP_UNION_CAVE_1F",  "b": "MAP_ROUTE_32",       "kinds": ["warp"],       "bidirectional": true},
    {"a": "MAP_ROUTE_32",       "b": "MAP_UNION_CAVE_1F",  "kinds": ["warp", "connection"], "bidirectional": true}
  ],
  "stats": {"nodes": 499, "edges": 535}
}
```

### Field definitions

| Field | Type | Meaning |
|---|---|---|
| `nodes[].id` | string | Map constant — primary key used everywhere |
| `nodes[].name` | string | Human-readable name for UI and logging |
| `nodes[].map_type` | string | `"ROUTE"`, `"CAVE"`, `"TOWN"`, `"CITY"`, `"BUILDING"`, etc. |
| `edges[].a` | string | One endpoint (map constant) |
| `edges[].b` | string | Other endpoint (map constant) |
| `edges[].kinds` | array | One or both of `"warp"` and `"connection"` |
| `edges[].bidirectional` | bool | `true` = player can travel A→B and B→A. `false` = A→B only |

`kinds` values:
- `["warp"]` — the only link between the two maps is a warp tile (door, cave mouth, stairs)
- `["connection"]` — the only link is walking off the map border
- `["warp", "connection"]` — both a warp tile AND a border connection exist between them

All three games (Emerald, HeartGold, Platinum) produce this format. Emerald derives
`connection` edges from `map.json` (explicit in the decomp). HeartGold and Platinum
derive them from map-matrix adjacency (two maps whose blocks sit next to each other on
the world matrix). The Java game does not care about the derivation source — it reads
all edges identically.

`bidirectional: false` is a real one-way relationship. The engine does not add a return
edge. Common causes: a fall-through hole, a scripted cave-exit warp with no entrance,
a dive spot with no surfacing partner.

---

## WorldGraph.java

`WorldGraph` is the single authority on which maps exist and how they connect.

### Construction

```java
class WorldGraph {
    private final Map<String, NodeInfo>   nodes;       // id → node metadata
    private final Map<String, List<Edge>> adjacency;   // id → outgoing edges
    private final MapCache                cache;

    static WorldGraph load(Path graphJson, Path mapsDir) {
        // 1. Parse map_graph.json → populate nodes and adjacency
        // 2. For each connection edge, merge the tile offset from
        //    the per-map JSON's "connections" array (see below)
        // 3. Return
    }
}

record Edge(String dest, Set<String> kinds, boolean bidirectional, int colOffset, int rowOffset) {}
// colOffset / rowOffset: how many tiles the player is shifted on crossing a connection border.
// Zero for warp edges (spawn coords come from the per-map JSON warp entry instead).
```

### Merging connection offsets

`map_graph.json` records that map A and map B are connected, but does not record the
tile offset (how far the player is shifted when walking from one to the other). That
offset lives in the per-map JSON:

```json
// MAP_ROUTE_1.json
{
  "connections": [
    {"direction": "SOUTH", "dest": "MAP_PALLET_TOWN", "col_offset": 0, "row_offset": 0},
    {"direction": "NORTH", "dest": "MAP_VIRIDIAN_CITY", "col_offset": 0, "row_offset": 0}
  ]
}
```

`WorldGraph.load()` iterates every `connection` edge in `map_graph.json`. For each
endpoint A, it opens `MAP_A.json`, reads `connections`, and finds the entry whose
`dest` matches B. The `col_offset` and `row_offset` from that entry are stored on the
`Edge` object. If no matching entry is found (can happen for PCG maps that omit the
array), offset defaults to `(0, 0)`.

### Preloading

When a map becomes active, `WorldGraph` immediately submits all 1-hop neighbors to a
background `ExecutorService` for loading. The load order is:
1. All `warp` neighbors (likely destinations if the player enters a building)
2. All `connection` neighbors (likely destinations if the player walks to the edge)

Maps already in `MapCache` are skipped. Maps 2+ hops away are not preloaded.

---

## Map transitions

### Warp transition

Trigger: the player steps onto a tile whose `(col, row)` appears in the active map's
`warps` array in its JSON.

Sequence:
1. Pause player input
2. Fade screen to black over 100 ms
3. Look up destination constant from the warp entry
4. Load destination `MapData` (from cache if preloaded; synchronous otherwise)
5. Place player at `spawn_col`, `spawn_row` from the warp entry, facing `SOUTH` by default
6. Fade in over 100 ms
7. Resume player input

The warp char on the tile (e.g., `P`, `G`, `c`) is used for rendering and collision
only — the actual destination is always read from the JSON warp entry, not inferred
from the char.

### Connection transition (seamless scroll)

Trigger: the player's movement step would place them outside the map bounds in a
direction that has a `connection` edge.

Sequence:
1. Do not pause input or fade
2. Load destination map (from cache; synchronous if not ready)
3. Begin scroll: render both maps simultaneously. The camera moves continuously from
   the source map into the destination map. Scroll speed matches player walk speed.
4. After the player's tile position crosses the border: swap the active map to the
   destination. Source map remains in cache but is no longer the active map.
5. Player's position in the destination map is calculated as:
   - Walking SOUTH off source: player enters destination at `row = 0`, `col = source_col + col_offset`
   - Walking NORTH off source: player enters destination at `row = dest_rows - 1`, `col = source_col + col_offset`
   - Walking EAST off source: player enters destination at `col = 0`, `row = source_row + row_offset`
   - Walking WEST off source: player enters destination at `col = dest_cols - 1`, `row = source_row + row_offset`

If the destination map is not in cache when the crossing begins, the scroll pauses and
a loading indicator appears. This should not happen under normal preloading but must be
handled.

### One-way edges

If `bidirectional: false`, only the A→B direction exists in the engine. The destination
map has no return warp or connection back to the source. The player navigates back by
other means (a different warp chain, a Fly destination, etc.). The engine never
automatically inserts a return path.

---

## PCG boot validation

`WorldGraph.validate(startConstant, progressionConfig)` runs before the first frame
renders. It throws `BootException` on hard failure and logs warnings for soft issues.

### Step 1 — BFS reachability

BFS from `startConstant` through all edges in `adjacency`. At each step, treat all
edges as passable regardless of HM state (the validation checks topology, not
progression). Collect the set of all reachable map constants.

### Step 2 — Gym count

For each reachable constant, open the corresponding JSON and check whether any trainer
has `"is_leader": true`. Count distinct maps that pass this check.

```
gymCount = reachableConstants
    .stream()
    .filter(c -> mapJson(c).trainers().stream().anyMatch(t -> t.isLeader()))
    .count()
```

If `gymCount < 8`:
```
BootException: Only 5/8 gyms reachable from MAP_PALLET_TOWN.
  Found gyms: MAP_PEWTER_GYM, MAP_CERULEAN_GYM, MAP_VERMILION_GYM,
              MAP_CELADON_GYM, MAP_FUCHSIA_GYM
  Missing: 3 gyms (check that all gym maps have a warp path from start)
```

### Step 3 — Elite Four warning

If `progressionConfig` contains an `elite_four_map` constant and that constant is not
in the reachable set: log a warning, do not throw.

```
Warning: Elite Four map MAP_INDIGO_PLATEAU_LOBBY not reachable from start.
  This may be correct if it is gated behind a badge check at runtime.
  If the map has no warp path at all, the player can never reach it.
```

### Step 4 — Topological progression check (when progression.json is absent)

If no `progression.json` exists, the engine attempts to derive the badge→HM assignment
using A* traversal. This is documented in `09_progression.md`. If the topological check
fails (cannot reach all 8 gyms even with all HMs unlocked), a `BootException` is thrown
with a description of which gyms are blocked and why.
