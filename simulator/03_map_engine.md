# Map Engine — Loading, Collision, Camera, Transitions

---

## MapData — the parsed `.txt`

`MapLoader.java` parses a `.txt` file into a `MapData` object at load time.

```java
class MapData {
    String constant;           // e.g. MAP_UNION_CAVE_1F
    String game;               // emerald / heartgold / platinum
    int cols, rows;
    char[][] grid;             // [row][col]
    List<Warp> warps;
    List<Connection> edges;    // walking off the border — all 3 games; stored in per-map JSON connections array
    List<NpcData> npcs;
    List<TrainerData> trainers;
    List<ItemBall> itemBalls;
    List<HiddenItem> hiddenItems;
    List<EncounterTable> encounters;
    List<Sign> signs;
    Biome primaryBiome;        // set by BiomeClassifier after grid is parsed
}
```

### Parse order
1. Read header → `constant`, `game`, `cols`, `rows`
2. Read grid section → `grid[row][col]`
3. Run `BiomeClassifier.classify(constant, game, grid)` → `primaryBiome`
4. Read Legend (skip — only used for human reading)
5. Read Elevation Addendum → Map of `(col,row) → underChar` for bridge tiles
6. Read Warp Index → `warps`; also read Connections block if present → `edges`
7. Read Wild Encounters → `encounters`
8. Read Item Balls, Hidden Items, Field Obstacles, Signs, Berry Trees
9. Read NPC Index → `npcs`
10. Read Trainer Index → `trainers`

All sections are optional — a map with no NPCs simply has no NPC Index section.
The parser detects section headers by the `======` banner line.

**For JSON loading**: `elevation_addendum` is always present as an array. Build a
`Map<(col,row), underChar>` from it at load time. Any bridge tile (`=`) not in this
map has passable floor (`" "`) beneath it — treat it as walkable ground level.

---

## CollisionMap

Built from `grid` at load time. Each tile is classified into one of seven `TileKind`
values; `passable` is then derived from the kind plus runtime state.

```java
enum TileKind {
    WARP,           // stepping onto this tile fires a map transition
    INTERACTIVE,    // face tile + press A to interact; player cannot stand here
    FURNITURE,      // purely blocking object; no interaction
    IMPASSABLE,     // hard terrain block
    PASSABLE,       // walk through freely (may trigger wild encounter)
    CONDITIONAL,    // passable only when a badge / field move / bike state is active
    LEDGE,          // one-directional jump in the arrow direction
    CURRENT,        // water / air current; pushes the player automatically while surfing
}

class CollisionMap {
    TileKind[][] kind;

    // ── WARP ───────────────────────────────────────────────────────────────────
    // Stepping onto the tile fires a map transition.  The *destination* comes from
    // the Warp Index, not from the char.  If a char below appears in the grid but
    // has NO corresponding warp entry in the Warp Index, treat it as IMPASSABLE.
    //
    // Building entrances (all games):
    //   P  Pokémon Center          G  Gym             $  PokéMart
    //   M  Dept / specialty shop   K  Lab / research  F  Elite Four
    //   S  School                  C  Contest Hall    X  Game Corner
    //   W  Museum / exhibit        Z  Battle facility d  Day Care
    //   g  Gate / gatehouse        h  House           Q  Global Terminal
    //   T  Major station           ⌁  Radio/cable-car N  Safari / Pal Park
    //   u  Harbor                  A  Ship / port     l  Lighthouse
    //   j  Library                 J  Snowpoint Temple f  Forest
    //   t  Tunnel / underground    V  Tower / shrine (outdoor only — see note)
    //
    // Generic doors / stairs:
    //   D  door / warp entrance    E  escalator up    e  escalator down
    //   L  ladder                  c  cave entrance
    //   ○  fall hole (drops to lower floor)
    //
    // Note on V: outdoors V is a building-entrance warp (Bell Tower etc.).
    // Indoors V is a vending machine (INTERACTIVE).  Resolved by the Warp Index:
    // if V has a warp entry → WARP; otherwise → INTERACTIVE.
    static final Set<Character> WARP_CHARS = Set.of(
        'P', 'G', '$', 'M', 'K', 'F', 'S', 'C',
        'X', 'W', 'Z', 'd', 'g', 'h', 'Q', 'T',
        '⌁', 'N', 'u', 'A', 'l', 'j', 'J', 'f', 't', 'V',
        'D', 'E', 'e', 'L', 'c', '○'
    );

    // ── INTERACTIVE ────────────────────────────────────────────────────────────
    // Face the tile and press A.  Player cannot stand on it.
    //   §   sign / notice board
    //   ⊙   visible item ball (pick up)
    //   *   hidden item (press A to find)
    //   I   item in vase / flower pot (PT)
    //   p   PC  → opens party / box-swap screen
    //   ⊞   town map  → opens the PCG-generated world map
    //   H   headbutt tree (HG)  → headbutt for Pokémon
    //   ♠   apricorn tree (HG)  → pick apricorn
    //   ◈   Ice Rock  → player faces for Glaceon evolution (PT)
    //   ✿   Moss Rock → player faces for Leafeon evolution (PT / EM)
    //   Y   honey tree (PT)  → slather honey; check back later
    //   @   static encounter  → face the overworld Pokémon to battle it
    //   V   vending machine (indoor, no warp entry) — see WARP note above
    static final Set<Character> INTERACTIVE_CHARS = Set.of(
        '§', '⊙', '*', 'I',
        'p', '⊞',
        'H', '♠', '◈', '✿', 'Y',
        '@', 'V'
    );

    // ── FURNITURE ──────────────────────────────────────────────────────────────
    // Blocking object that cannot be interacted with.
    // The ASCII grid provides no text or action for these; they are pure obstacles.
    //   v  TV          B  bookshelf / mart shelf    q  table / counter
    //   y  trash can   z  magma (HG)               ▓  building body / wall
    static final Set<Character> FURNITURE_CHARS = Set.of(
        'v', 'B', 'q', 'y', 'z', '▓'
    );

    // ── IMPASSABLE ─────────────────────────────────────────────────────────────
    //   █  terrain wall / mountain       ∘  sea rock (rock in water)
    //   ▐▌▀▄│─  directional walls (block from their primary face; see DIRECTIONAL section)
    static final Set<Character> IMPASSABLE_CHARS = Set.of(
        '█', '∘',
        '▐', '▌', '▀', '▄', '│', '─'
    );

    // ── PASSABLE ───────────────────────────────────────────────────────────────
    //   ' '  normal floor       "  tall grass (wild encounter)
    //   .    sand / ash / snow   :  deep sand / deep snow
    //   m    mud                 i  ice (player slides; see below)
    //   x    cracked ice (EM)    ^  mountain floor (PT)
    //   b    berry patch         s  slide
    //   =    bridge deck         ~  shallow water (no Surf needed on foot)
    //   ♨    hot springs         U  Pastoria Gym puzzle floor (passability game-controlled)
    //   ¿    stairs / slope      o  whirlpool (passable in water while surfing)
    static final Set<Character> PASSABLE_CHARS = Set.of(
        ' ', '"', '.', ':', 'm', 'i', 'x', '^',
        'b', 's', '=', '~', '♨', 'U', '¿', 'o'
    );

    // ── CONDITIONAL ────────────────────────────────────────────────────────────
    // Passable only when the matching badge or field-move state is active:
    //   ≈   deep ocean   — needs SURF
    //   ♣   cut tree     — needs CUT badge (or HM CUT learned, depending on game)
    //   ⊗   rock         — needs ROCK SMASH
    //   ▣   boulder      — push with STRENGTH (not a simple passability toggle)
    //   ◻   ice block    — push logic (same as ▣)
    //   R   rock-climb   — needs ROCK CLIMB badge
    //   ≡   bike bridge  — needs BIKE
    //   r   bike ramp    — needs BIKE
    //   k   bumpy slope  — needs BIKE
    //   n   bike rail    — needs BIKE (cycling-road rails; on foot = blocked)
    //   ↓   waterfall    — needs WATERFALL to ascend; always passable descending
    //   ●   snowball (PT Snowpoint Gym) — push once; breaks on impact; no HM needed
    static final Set<Character> CONDITIONAL_CHARS = Set.of(
        '≈', '♣', '⊗', '▣', '◻', '●', 'R', '≡', 'r', 'k', 'n', '↓'
    );

    // ── LEDGE ──────────────────────────────────────────────────────────────────
    // One-directional jump in the arrow direction.  Walking into the arrow = hop
    // animation, player lands two tiles forward.  Walking against the arrow = blocked.
    //   ► ◄ ▲ ▼  (cardinal ledges, all games)
    static final Set<Character> LEDGE_CHARS = Set.of('►', '◄', '▲', '▼');

    // ── CURRENT ────────────────────────────────────────────────────────────────
    // Water / conveyor currents — player moves automatically each step while in the
    // current tile.  Only reachable while surfing (the tile itself is water).
    //   ↑ → ← ↗ ↖ ↘ ↙  (diagonal and cardinal water currents)
    static final Set<Character> CURRENT_CHARS = Set.of(
        '↑', '→', '←', '↗', '↖', '↘', '↙'
    );
}
```

### Classification logic

```java
TileKind classify(char ch, boolean hasWarpEntry) {
    if (WARP_CHARS.contains(ch))       return hasWarpEntry ? WARP : IMPASSABLE;
    if (INTERACTIVE_CHARS.contains(ch)) return INTERACTIVE;
    if (FURNITURE_CHARS.contains(ch))   return FURNITURE;
    if (IMPASSABLE_CHARS.contains(ch))  return IMPASSABLE;
    if (PASSABLE_CHARS.contains(ch))    return PASSABLE;
    if (CONDITIONAL_CHARS.contains(ch)) return CONDITIONAL;
    if (LEDGE_CHARS.contains(ch))       return LEDGE;
    if (CURRENT_CHARS.contains(ch))     return CURRENT;
    return IMPASSABLE;  // unknown char → safe default
}
```

`hasWarpEntry` is true when `MapData.warps` contains an entry at that `(col, row)`.
This resolves the `V` ambiguity (outdoor tower entrance vs indoor vending machine)
without branching on map type.

### Conditional passability — runtime checks

| Char | Condition | Badge / state |
|------|-----------|--------------|
| `≈`  | SURF state | HM03 Surf |
| `♣`  | CUT unlocked | HM01 Cut + badge |
| `⊗`  | ROCK_SMASH unlocked | HM06 Rock Smash + badge |
| `▣`  | STRENGTH unlocked | HM04 Strength + badge |
| `◻`  | STRENGTH (push ice blocks) | same as boulder |
| `R`  | ROCK_CLIMB unlocked | HM08 Rock Climb + badge |
| `≡`  | BIKE equipped | any bike in party |
| `r`  | BIKE equipped | any bike in party |
| `k`  | BIKE equipped | any bike in party |
| `n`  | BIKE equipped | any bike in party |
| `↓`  | WATERFALL to ascend | HM07 Waterfall + badge; descend is free |

Directional walls (`▐▌▀▄│─`) block the face they point toward; the player may enter
from other directions. Encode as `DirectionalWall` with an `allowedEntryFaces` bitmask.

### Snowball (`●`)

Snowpoint Gym (PT) places `●` snowballs as push-puzzle obstacles.

- No badge or HM required to push.
- When the player faces a `●` tile and presses A (or walks into it), the snowball slides
  one tile in that direction.
- If the destination tile is IMPASSABLE or FURNITURE (including another `●`), the snowball
  **breaks permanently**: it is removed from the map and the tile reverts to floor (` `).
- If the destination tile is passable floor, the snowball moves there and the original
  tile becomes floor.
- The snowball cannot be pushed off a ledge or into a warp.
- Snowball state (position / broken) is part of the map's mutable state and must be
  tracked in the same structure as boulder/ice-block positions.

### Ice sliding (`i`)

On `i` tiles the player does not stop when a movement key is released — they continue
sliding in the current direction until hitting an IMPASSABLE or FURNITURE tile.
This is a special movement state, not a passability change.

---

## Warp resolution

Warps are stored in `MapData.warps` as:

```java
record Warp(char ch, int col, int row, String destConstant, int destCol, int destRow) {}
```

When the player steps onto a warp tile (char is a warp char AND the cell is in the
warps list), `WorldGraph.transition(warp)` fires:

1. Fade to black (100 ms)
2. Load `MapData` for `destConstant` (from cache or disk)
3. Place player at `(destCol, destRow)`
4. Fade in

The warp char on the grid is just a visual/collision marker. The `warps` list is the
authoritative destination. Multiple warps on one map can share a char (e.g., several
`D` doors each go to different destinations).

---

## Edge connections (walking off the border)

Emerald `.txt` files have a Connections block listing which map is adjacent in each
cardinal direction and the tile offset. Gen 4 maps infer adjacency from the matrix
(already resolved into the warp index in our .txt output).

```java
record Connection(Direction dir, String destConstant, int offset) {}
// offset: how many tiles the destination is shifted relative to this map's edge
```

When the player walks off the edge of the map in direction `dir`:
1. Check `edges` for a Connection in that direction
2. If found: load destination map, calculate entry tile from offset, transition without
   fade (seamless scroll)
3. If not found: blocked (invisible wall at map border)

Seamless scroll means the camera is already partially showing the next map before the
player crosses — preload adjacent maps one screen-width ahead.

---

## Camera

LibGDX `OrthographicCamera` + `FitViewport`.

- Viewport: 26 tiles wide × 15 tiles tall (416×240 px logical, scaled 3× to 1248×720)
- Camera follows player position, clamped to map bounds:
  ```
  camX = clamp(playerX, viewportW/2, mapW - viewportW/2)
  camY = clamp(playerY, viewportH/2, mapH - viewportH/2)
  ```
- For maps smaller than the viewport: center the map, fill outside with black
- Seamless transition: during edge-crossing, camera smoothly scrolls across the border
  without a cut — both maps are rendered simultaneously during the transition frame

---

## Render layers

LibGDX `OrthogonalTiledMapRenderer` with two `TiledMapTileLayer` objects:

| Layer | Z-order | Contents |
|---|---|---|
| `-1` (under-bridge) | below everything | Under-chars from Elevation Addendum |
| `0` (ground) | base | Floor, water, grass, sand — all layer-0 chars |
| `1` (entities) | above ground, below overlay | Player, NPCs, items, Pokémon |
| `2` (overlay) | above entities | Tree canopy, building tops, bridge railings |
| `3` (UI) | top | HUD, dialogue box, menus |

The two TiledMapTileLayers (ground + overlay) are built once at map load and rebuilt
only when the map state changes (e.g., a tree is cut, a boulder is pushed).

---

## Map caching and preloading

`WorldGraph` maintains a `MapCache` (LRU, max 8 maps):

- Current map: always loaded
- Adjacent maps (warps + edges reachable within 1 hop): preloaded on a background thread
  after the current map finishes loading
- Maps 2+ hops away: not loaded

On transition: the destination map is usually already in cache (preloaded). If not
(PCG maps with unusual topology), load synchronously with a loading indicator.

Memory budget: each map grid (32×96 = 3072 tiles) is trivial. The atlas textures
dominate — max 2 biome atlases in VRAM at once (current + adjacent if different biome).

---

## `.txt` parser implementation notes

The parser is a simple line-by-line state machine:

```
State: HEADER → reads until blank line after the === banner
State: GRID → reads rows until "Legend:" line
State: LEGEND → reads until next === banner
State: ELEVATION → reads until next === banner (optional)
State: WARPS → reads until next === banner
... etc
```

Column ruler lines (the `0    1    2...` header above the grid) are identified by the
pattern `^\s+\d` and skipped. Grid rows are identified by `^\s+\d+ ` (row number
followed by tile chars). The 5-char row-number prefix is stripped.

**UTF-8 chars** (≈, ♣, ►, etc.) are stored as Java `char` — the grid is `char[][]`.
Java chars are 16-bit Unicode, covering all BMP code points used in the format.

---

## PCG boot validation

Before rendering the first frame, `WorldGraph.validate()` runs:

```java
void validate(MapData startMap) throws BootException {
    Set<String> visited = bfsReachable(startMap);
    long gymCount = visited.stream()
        .map(cache::get)
        .filter(m -> isGymMap(m))
        .count();
    if (gymCount < 8)
        throw new BootException("Only " + gymCount + "/8 gyms reachable");
}

boolean isGymMap(MapData m) {
    // A gym map has at least one trainer tagged [LEADER] in the Trainer Index
    return m.trainers.stream().anyMatch(t -> t.isLeader);
}
```

The `[LEADER]` tag comes from the `.txt` Trainer Index — gym leaders are tagged in the
source data. This is the only hard constraint. The engine logs a warning (not a boot
failure) if the Elite Four map is not reachable — it may be gated by the badge check
which only fires at runtime.
