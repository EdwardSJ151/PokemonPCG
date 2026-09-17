# ASCII Grid → Sprites

Engineering spec for the tile rendering subsystem. This is the hardest single piece of
the engine — every other system depends on getting this right.

---

## 1. Biome classifier

The biome determines which sprite atlas to load and which variant to pick for
environment-aware chars (`.`, `:`, `"`, `m`). It is computed once at map load time.

### Biome enum

```java
public enum Biome {
    TEMPERATE,  // grassy routes, normal outdoors
    SNOW,       // ice routes, snowfields
    DESERT,     // sandy routes, beaches
    CAVE,       // rock interiors, dungeons
    INDOOR,     // building interiors
    WATER,      // ocean maps, lake centres
    FOREST,     // dense tree maps
    URBAN       // towns, cities
}
```

### Decision rule

Run in this order — first match wins.

**Step 1 — Keyword match on the map constant name (fast, ~80% of maps)**

Strip the game prefix (`MAP_`, `MAP_HEADER_`, `LAYOUT_`) and uppercase the remainder,
then test substrings:

| Substring in constant | Biome |
|-----------------------|-------|
| `CAVE`, `TUNNEL`, `UNDERGROUND`, `DUNGEON`, `HIDEOUT`, `RUINS` | CAVE |
| `BUILDING`, `CENTER`, `MART`, `GYM`, `LAB`, `SCHOOL`, `TOWER`, `MANSION`, `HOUSE`, `SHOP`, `FRONTIER`, `LIGHTHOUSE`, `MUSEUM`, `GATE`, `STATION` | INDOOR |
| `FOREST`, `WOODS`, `JUNGLE` | FOREST |
| `CITY`, `TOWN`, `VILLAGE` | URBAN |
| `BEACH`, `DESERT`, `SAND` | DESERT |
| `SNOWPOINT`, `SNOW`, `ICE_PATH`, `MT_CORONET`, `ACUITY`, `BLIZZARD` | SNOW |
| `OCEAN`, `SEA`, `LAKE`, `ISLAND` | WATER |
| `ROUTE`, `PATH`, `ROAD`, `TRAIL`, `PASS` | TEMPERATE (provisional — histogram may override) |

**Step 2 — Tile frequency histogram (fallback and ROUTE override)**

Count every char in the terrain grid. Compute fractions over total non-space tiles.

```
total    = count of all non-' ' chars
wall_frac   = count('█') / total
water_frac  = (count('≈') + count('~')) / total
grass_frac  = count('"') / total
dot_frac    = count('.') / total
colon_frac  = count(':') / total
tree_frac   = count('♣') / total
```

Apply these thresholds in order:

| Condition | Biome |
|-----------|-------|
| `wall_frac > 0.55` AND keyword did not match INDOOR | CAVE |
| `water_frac > 0.60` | WATER |
| `colon_frac > 0.20` AND game == `platinum` | SNOW |
| `colon_frac > 0.20` AND game == `emerald` | DESERT |
| `dot_frac > 0.25` AND game == `platinum` | SNOW |
| `dot_frac > 0.25` AND game == `emerald` | DESERT |
| `tree_frac > 0.40` | FOREST |
| `grass_frac > 0.15` | TEMPERATE |
| keyword matched ROUTE and none of the above fired | TEMPERATE |
| no match anywhere | TEMPERATE (safe default) |

**Multi-biome maps:** A route with a sandy beach section gets biome TEMPERATE. The `.`
chars on the beach portion render using the DESERT ground tile from the TEMPERATE atlas's
transition set (see Section 3 — transition tiles). The biome is a property of the *map*,
not the *tile*; per-tile sprite variants handle the local exception.

---

## 2. Tile layer model

Gen 3 renders two layers. Build both as `TiledMapTileLayer` objects.

| Layer | Index | Contents |
|-------|-------|----------|
| Ground | 0 | Floor, water, grass, sand, ice, path — everything you walk on |
| Overlay | 1 | Tree canopies, building facades, bridge railings, cliff tops |

Layer 1 tiles have transparency — the player entity renders *between* the two layers so
building facades and tree canopies appear in front of the player when they walk behind
them. Use LibGDX's `OrthographicCamera` with two separate batch passes: ground → entities
→ overlay.

### Char → layer assignment

| Char(s) | Layer 0 sprite | Layer 1 sprite |
|---------|---------------|----------------|
| ` ` (walkable) | path/floor tile (autotiled) | — |
| `█` | wall/cliff tile (autotiled) | cliff-top cap where █ borders ` ` below |
| `▓` | building floor tile | building facade tile (see Section 6) |
| `♣` | tree base (impassable floor) | tree canopy |
| `"` | tall grass tile (autotiled) | — |
| `.` | sand / snow tile (autotiled, biome-driven) | — |
| `:` | deep sand / deep snow tile (autotiled, biome-driven) | — |
| `~` | water tile (autotiled) | — |
| `≈` | deep water tile (autotiled) | — |
| `=` | bridge deck tile | bridge railing sprite |
| `≡` | bike bridge deck | bike bridge railing |
| `¿` | stair tile | — |
| `m` | mud tile (autotiled) | — |
| `i` | ice tile (autotiled) | — |
| `►◄▲▼` | ledge tile (directional) | — |
| `▐▌▀▄│─` | directional wall tile | — |
| `^` | mountain floor tile | — |
| All entrance chars (`P G $ K F S C X W Z d g h V Q T Y N u A l j J M c f t`) | door/entrance tile (single sprite) | — |
| `§` | sign post sprite | sign top sprite |
| `∘` | sea rock sprite | — |
| `⊗` | rock sprite | — |
| `▣` | boulder sprite | — |
| `◻` | ice block sprite | — |
| `♠` | apricorn tree base | apricorn canopy |
| `H` | headbutt tree base | headbutt tree canopy |
| `p v B q y ⊞` | furniture tile (unique sprite per char) | — |
| `E e L` | escalator / ladder tile | — |
| `z` | magma tile (animated) | — |
| `o` | whirlpool tile (animated) | — |
| `○` | hole tile | — |
| `⊙ I` | item ball sprite (entity layer, not tile) | — |
| `*` | invisible — no sprite | — |
| `@` | static encounter — floor tile beneath | — |
| `b` | berry patch tile | — |
| `♨` | hot spring tile (animated) | steam overlay |
| `◈ ✿` | special rock sprite | — |
| `n` | rail/fence tile | — |
| `k r s` | terrain modifier tile | — |
| `R` | rock climb wall tile | — |
| `U` | gym puzzle ground tile | — |
| `x` | cracked ice tile | — |
| `↓↑→←↗↖↘↙` | current tile (animated, directional) | — |

---

## 3. Autotiling

### Which chars get autotiled

Autotiling only makes sense for chars that form contiguous regions where you need smooth
edges. Point sprites and single-occurrence chars do not.

**Autotiled (47-variant blob):**

| Char group | Tile type name |
|------------|----------------|
| ` ` (walkable floor) | `path` |
| `█` (impassable) | `wall` |
| `~` | `water_shallow` |
| `≈` | `water_deep` |
| `"` | `tall_grass` |
| `.` | `surface` (sand OR snow — biome selects the sheet) |
| `:` | `surface_deep` (deep sand OR deep snow) |
| `m` | `mud` |
| `i` | `ice` |

**Not autotiled (single fixed sprite):**
`♣ ♠ H § ∘ ⊗ ▣ ◻ ◈ ✿ Y b ♨ z o ○` and all entrance chars, furniture chars,
directional chars.

### 47-tile blob bitmask

For each cell being autotiled, check its 8 neighbors. A neighbor "matches" if it has
the same autotile group (not the exact same char — `~` and `≈` do NOT match each other;
`█` matches `█` and `▓` and entrance chars since they're all solid).

Assign bits clockwise from top-left:

```
NW=128  N=1   NE=2
W=64         E=4
SW=32   S=8   SE=16
```

Corner bits (NW, NE, SW, SE) are only set if BOTH adjacent cardinal neighbors also match.
This is the "blob" rule — it prevents diagonals from creating orphan corners.

```java
int nw = matches(x-1,y-1) && matches(x,y-1) && matches(x-1,y) ? 128 : 0;
int n  = matches(x,  y-1) ? 1  : 0;
int ne = matches(x+1,y-1) && matches(x,y-1) && matches(x+1,y) ? 2   : 0;
int w  = matches(x-1,y  ) ? 64 : 0;
int e  = matches(x+1,y  ) ? 4  : 0;
int sw = matches(x-1,y+1) && matches(x,y+1) && matches(x-1,y) ? 32  : 0;
int s  = matches(x,  y+1) ? 8  : 0;
int se = matches(x+1,y+1) && matches(x,y+1) && matches(x+1,y) ? 16  : 0;
int mask = nw | n | ne | w | e | sw | s | se;  // 0–255 but only 47 valid combinations
```

Map mask → variant index (0–46) using the standard blob lookup table. The table is a
`static final int[] BLOB_TO_INDEX = new int[256]` where invalid combinations (masked by
the corner rule) map to the nearest valid index. Use the table from:
`https://gamedevelopment.tutsplus.com/tutorials/how-to-use-tile-bitmasking--cms-25673`
or derive it from the 47 canonical blob configurations.

### Transition tiles

When a cell of autotile group A borders a cell of autotile group B, the A cell needs a
*transition variant* that blends the two. These are separate tile strips, not part of the
47-blob sheet.

Required transition pairs (order = A blends into B):

| From | To | Strip name |
|------|----|------------|
| `path` | `tall_grass` | `path_to_grass` |
| `path` | `surface` (sand/snow) | `path_to_surface` |
| `path` | `water_shallow` | `path_to_water` |
| `surface` | `tall_grass` | `surface_to_grass` |
| `surface` | `water_shallow` | `surface_to_water` |
| `tall_grass` | `water_shallow` | `grass_to_water` |
| `wall` | `path` | `wall_to_path` (cliff base) |
| `wall` | `water_shallow` | `wall_to_water` |
| `wall` | `tall_grass` | `wall_to_grass` |

Each strip is an 8-tile row covering the 8 cardinal+diagonal edge variants (N, NE, E, SE,
S, SW, W, NW border). Select the strip tile by the direction the transition faces.

Implementation: after computing the blob index for a cell, check if any neighbor belongs
to a different autotile group with a defined transition. If yes, composite the transition
strip tile on top of the blob tile at the same layer. Use LibGDX's `TiledMapTile` with
two stacked regions via a `AnimatedTiledMapTile` wrapper, or simply render the transition
as a second draw call in the ground pass.

### auto-tile-gdx

The library at `github.com/gpertzov/auto-tile-gdx` handles the bitmask computation and
JSON-driven tile lookup. What it does NOT cover:
- Biome-driven sheet selection (you pass it the sheet; you choose which sheet)
- Cross-group transitions (it tiles within one group)
- The `█` matching rule (treating entrance chars and `▓` as solid)

Use it for the bitmask + 47-variant lookup; write the biome selector and transition
compositor yourself.

---

## 4. Sprite atlas layout

### Directory structure

```
assets/
  tilesets/
    TEMPERATE.atlas   + TEMPERATE.png
    SNOW.atlas        + SNOW.png
    DESERT.atlas      + DESERT.png
    CAVE.atlas        + CAVE.png
    INDOOR.atlas      + INDOOR.png
    WATER.atlas       + WATER.png
    FOREST.atlas      + FOREST.png
    URBAN.atlas       + URBAN.png
    TRANSITIONS.atlas + TRANSITIONS.png   (shared cross-biome transition strips)
    OBJECTS.atlas     + OBJECTS.png       (point sprites: rocks, signs, furniture, etc.)
```

### Naming convention inside each atlas

```
# 47-blob variants for each autotiled group — suffix is the blob index 0–46
path_blob_0  …  path_blob_46
wall_blob_0  …  wall_blob_46
tall_grass_blob_0  …  tall_grass_blob_46
water_shallow_blob_0  …  water_shallow_blob_46
water_deep_blob_0  …  water_deep_blob_46
surface_blob_0  …  surface_blob_46          # sand in DESERT, snow in SNOW
surface_deep_blob_0  …  surface_deep_blob_46
mud_blob_0  …  mud_blob_46
ice_blob_0  …  ice_blob_46

# Overlay tiles
cliff_top_N   cliff_top_NE   cliff_top_E   …   (8 directions)
bridge_deck
bridge_railing_H   bridge_railing_V   bridge_railing_end_L   bridge_railing_end_R
bike_bridge_deck
bike_bridge_railing_H   …

# Building components — see Section 6
building_wall_mid
building_roof_left   building_roof_mid   building_roof_right
building_door        # layer 0 for entrance chars

# TRANSITIONS.atlas strips — 8 tiles each
path_to_grass_N  …  path_to_grass_NW
path_to_surface_N  …
# (etc. for each pair listed in Section 3)
```

### Packing

Use LibGDX `TexturePacker` (gdx-tools):

```bash
java -cp gdx-tools.jar com.badlogic.gdx.tools.texturepacker.TexturePackerFileProcessor \
     src/tilesets/TEMPERATE/ assets/tilesets/ TEMPERATE
```

Pack settings (`pack.json` per biome directory):

```json
{
  "maxWidth": 2048,
  "maxHeight": 2048,
  "pot": true,
  "paddingX": 2,
  "paddingY": 2,
  "duplicatePadding": true,
  "filterMin": "Nearest",
  "filterMag": "Nearest"
}
```

`Nearest` filtering is mandatory — bilinear blurring destroys pixel art at 16×16.

---

## 5. Render pipeline

### Map load (one-time, on warp or startup)

```
1. Parse the .txt file
   a. Read header → cols, rows, constant, game
   b. Read terrain grid → char[][] grid
   c. Read Elevation Addendum → Map<(col,row), Character> underChars
   d. Read Warp Index → List<Warp>
   e. Read all other sections (encounters, NPCs, trainers, items...)

2. Run biome classifier → Biome biome

3. Load atlas: AssetManager.load(biome.atlasPath, TextureAtlas.class)
   Also load TRANSITIONS.atlas and OBJECTS.atlas if not already loaded.

4. Build tile layers
   a. For each (col, row):
      - Compute autotile group for the char
      - If autotiled: compute 8-neighbor bitmask → blob index → TextureRegion
      - Detect transitions: for each autotiled neighbor of a different group,
        select transition strip tile
      - Assign layer 0 region
      - Determine layer 1 region (canopy, facade, railing, cliff cap)
   b. Build TiledMapTileLayer ground (layer 0)
   c. Build TiledMapTileLayer overlay (layer 1)
   d. For bridge tiles (=): build TiledMapTileLayer underBridge (layer -1)
      using underChars map for cells that have non-trivial unders

5. Spawn entities from NPC/trainer/item sections
```

### Per-frame render

```
1. Update camera — follow player, clamp to map bounds
2. OrthographicCamera.update()
3. mapRenderer.setView(camera)
4. mapRenderer.renderTileLayer(groundLayer)      // layer 0
5. spriteBatch.begin()
   - render all entities sorted by row (painter's order)
   - item balls, NPCs, trainers, player
6. spriteBatch.end()
7. mapRenderer.renderTileLayer(overlayLayer)     // layer 1 (in front of entities)
```

For the under-bridge layer, render it before the ground layer only when the player is
at water level (surfing beneath a bridge). In practice this is rare — implement it as a
special-case render path gated on player state.

### Camera setup

```java
// Tile size in pixels (native Gen 3)
static final int TILE_PX = 16;

// Integer scale factor — set based on screen height
int scale = Math.max(1, Gdx.graphics.getHeight() / (TILE_PX * 15)); // ~15 tiles tall

camera = new OrthographicCamera();
camera.setToOrtho(false,
    Gdx.graphics.getWidth()  / (float)(TILE_PX * scale),
    Gdx.graphics.getHeight() / (float)(TILE_PX * scale));

// Clamp camera to map bounds
float camHalfW = camera.viewportWidth  / 2f;
float camHalfH = camera.viewportHeight / 2f;
camera.position.x = MathUtils.clamp(playerX, camHalfW, mapCols - camHalfW);
camera.position.y = MathUtils.clamp(playerY, camHalfH, mapRows - camHalfH);
```

---

## 6. Edge cases and hard problems

### `▓` building body — unknown dimensions

The `.txt` marks each building-body cell with `▓` but gives no bounding box. There is
no way to know at generation or design time how many tiles wide or tall a building will be.
The facade must tile correctly at **any** width and height, including PCG-generated sizes.

**Solution — flood-fill at load time:**

```
For each ▓ cell not yet processed:
  BFS outward visiting all adjacent ▓ and entrance-char cells
  → bounding box (minCol, minRow, maxCol, maxRow)
  → read the dominant landmark char from the entrance-char cells found (see below)
  → stamp the chosen building sprite sheet scaled to (width, height) tiles
```

Every building sprite sheet (generic or landmark) uses this tileable layout:

```
roof_L  roof_M  roof_M  roof_R     ← one instance of roof_L + N×roof_M + roof_R
wall_L  wall_M  wall_M  wall_R     ← repeated for each interior row
```

The engine repeats `roof_M` and `wall_M` across the measured width, and `wall_L/R` down
the measured height. A width-1 building gets `roof_solo` and `wall_solo`; width 2 gets
`roof_narrow_L` / `roof_narrow_R`. Every sprite sheet must include these narrow variants.

### Entrance chars and building identity

The entrance char cell sits **below** the `▓` region (adjacent, not part of it). The
flood-fill reads which entrance chars it found and picks the building's facade:

| Entrance char found | Facade sheet used |
|---|---|
| `P` (Pokémon Center) | `building_center` — red-and-white roof, blue cross on facade |
| `G` (Gym) | `building_gym` — stone/badge-themed, heavy roof |
| `$` (Mart) | `building_mart` — blue roof, storefront stripe |
| Any other char | `building_generic` — plain house/building |

If a building has multiple entrance chars (rare but possible), the **most landmark** char
wins in priority order: `P` > `G` > `$` > any other.

**Landmark sheets are still fully tileable.** Their `roof_M`, `wall_M` etc. tiles are
designed to repeat across any width — the Center's blue cross is a single icon placed
only on `roof_M` at the horizontal midpoint (calculated at stamp time), not baked into
every `roof_M` tile. The wall color and roof color vary per sheet; the tiling structure
is identical.

Layer assignment:
- Layer 0: door/step sprite at each entrance char cell (`door_center`, `door_gym`,
  `door_mart`, or `door_generic`) — same layer, char determines which door sprite
- Layer 1: the facade stamp from the flood-fill result (roof + wall tiles)

### Bridge tiles (`=`)

Bridges need three layers:

| Layer | Content |
|-------|---------|
| -1 (under) | The under-char sprite (water, floor, etc.) — rendered only for cells listed in the Elevation Addendum |
| 0 (ground) | Bridge deck tile |
| 1 (overlay) | Bridge railing sprite — H railing for horizontal spans, V for vertical |

Railing direction: check whether the bridge span is wider horizontally or vertically
(compare run lengths). A 1×1 isolated bridge cell gets a 4-way railing cap.

### `█` cliff vs cave wall

Same char, two completely different sprites. The biome determines which tileset is used:

| Biome | `█` sprite set |
|-------|----------------|
| CAVE, INDOOR | Rock wall — dark stone, no sky edge |
| TEMPERATE, FOREST, URBAN | Cliff — top cap tile facing the open ground below |
| SNOW | Snow-topped cliff |
| DESERT | Sandy cliff |
| WATER | Sea cliff / rock face |

The cliff cap (layer 1 tile placed on the `█` cell that borders a ` ` cell below it)
is only generated for outdoor biomes. Cave walls have no cap.

### Warp entrance chars — door sprites

Most entrance chars (`K F S C X W Z d g h V Q T Y N u A l j J M f t` etc.) are generic
buildings and share one `door_generic` sprite on layer 0. The `▓` flood-fill stamp on
layer 1 carries the visual identity of those buildings — see "Entrance chars and building
identity" above.

**Landmark overrides** — these three chars drive both a distinct door sprite AND a distinct
facade sheet:

| Char | Door sprite | Facade |
|---|---|---|
| `P` | `door_center` (sliding glass doors with Nurse Joy silhouette) | `building_center` |
| `G` | `door_gym` (heavy stone arch) | `building_gym` |
| `$` | `door_mart` (shop awning + mat) | `building_mart` |

`c` (cave entrance) is a special case — it usually has no `▓` body at all. It sits in an
open cliff face and gets a dark archway sprite embedded in the wall layer at that cell.

### Animated tiles

Tiles that animate: `z` (magma), `o` (whirlpool), `♨` (hot spring), `↓↑→←↗↖↘↙`
(water currents), `≈` and `~` (subtle water shimmer).

Use LibGDX `AnimatedTiledMapTile` with a frame duration of ~0.3s for water shimmer,
~0.15s for magma/currents. Pack all frames for an animated tile into a named sequence
in the atlas: `water_deep_anim_0`, `water_deep_anim_1`, `water_deep_anim_2`.

### PCG map blending

PCG-generated maps may place biome transitions mid-map more aggressively than the real
game data does. The transition system in Section 3 handles this by design — it's purely
tile-local. No special handling needed for PCG maps; the same pipeline runs on any
char grid.

For PCG maps that mix biomes globally (e.g., a route that is 50% snow and 50% grass),
detect this in Step 2 by checking if two competing histogram conditions both exceed 15%.
In that case, assign the map a compound biome tag `TEMPERATE|SNOW` and load both atlases.
The per-tile biome selection then uses: if the cell is `.` or `:` and its neighbors are
mostly `:` → use SNOW surface; otherwise use TEMPERATE surface. This is a local vote,
not a global reclassification.
