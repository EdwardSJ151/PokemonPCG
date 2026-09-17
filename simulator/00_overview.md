# Simulator — Overview & Architecture

A Java game engine that loads `.txt` terrain files produced by the ASCII renderer and
makes them fully playable. The three real games (Emerald, HeartGold, Platinum) serve as
test beds; the actual use case is running PCG-generated maps through the same engine.

---

## Stack

| Layer | Choice | Why |
|-------|--------|-----|
| Language | Java 17 | Cross-platform, strong ecosystem, target language |
| Framework | LibGDX | TiledMap support, SpriteBatch, OrthographicCamera, input, audio — all included |
| Tile size | 16×16 px native, integer-scaled to screen | True to Gen 3; `Nearest` filter preserves pixels |
| Tileset style | Gen 3 GBA (Emerald / FRLG) | Pure 2D — no 3D models to deal with |
| Build | Gradle (LibGDX standard) | LibGDX project template generates this |
| Battle engine | Gen 4 mechanics (damage formula, type chart, abilities, moves) | Stadium-style: correct logic, custom UI/sprites |

---

## Module breakdown

```
simulator/
  core/
    MapLoader        — parses .txt → MapData
    MapRenderer      — builds TiledMapTileLayers, calls autotiler
    BiomeClassifier  — char histogram + constant keyword → Biome enum
    AutoTiler        — 47-blob bitmask + transition compositor
    AtlasManager     — loads/caches TextureAtlas per biome
    WorldGraph       — warp + edge link graph, map transition logic
    CollisionMap     — char → passable/impassable + directional rules
  entity/
    Player           — movement, animation state machine, HM usage
    NPC              — dialogue, flag gates, movement patterns
    Trainer          — sight range, battle trigger, party
    ItemBall         — pickup interaction
  encounter/
    WildEncounter    — grass/surf/fishing trigger + table lookup
    EncounterTable   — parsed from .txt Wild Encounters section
  battle/
    BattleEngine     — Gen 4 damage formula, status, abilities, moves
    BattleUI         — sprite rendering, menu, HP bars, animations
    MoveData         — all Gen 4 moves
    PokemonData      — base stats, learnsets, evolution
  progression/
    BadgeSystem      — 8-badge store, E4 gate, HM unlock table
    SaveManager      — player state, flags, inventory, party, position
  ui/
    DialogueBox      — NPC text rendering, input advance
    MenuSystem       — start menu, bag, Pokédex, options
```

---

## Data flow

```
.txt file
    │
    ▼
MapLoader.parse()
    │  produces MapData:
    │    char[][] grid
    │    List<Warp>
    │    List<NPC>
    │    List<Trainer>
    │    EncounterTable encounters
    │    List<ItemBall>
    │    Map<(col,row), char> underChars  (Elevation Addendum)
    │
    ▼
BiomeClassifier.classify(mapData)  →  Biome
    │
    ▼
MapRenderer.build(mapData, biome)
    │  produces:
    │    TiledMapTileLayer groundLayer
    │    TiledMapTileLayer overlayLayer
    │    TiledMapTileLayer underBridgeLayer  (optional)
    │
    ▼
Game loop renders layers + entities each frame
```

---

## Map transitions (warp system)

Every warp in the Warp Index has the form:
```
[char]  (col=X, row=Y)  →  DESTINATION_CONSTANT  →  spawn (col=A, row=B)
```

`WorldGraph` builds a `Map<String, MapData>` keyed by constant. On warp trigger:
1. Look up destination constant in the graph
2. Load destination `.txt` if not cached
3. Place player at spawn (col=A, row=B)
4. Fade out → swap active map → fade in

For Emerald maps, the Connections block provides cardinal edges (walking off the border
transitions to an adjacent map). These are stored as directional edges in the graph and
handled by a border-crossing check each move step.

**Map caching:** Keep the last N maps in memory (N=5 is sufficient). LRU eviction.
Large maps (Union Cave at 32×96) fit comfortably — a char grid is trivially small.
The TiledMapTileLayers are the actual memory cost; evict those first.

---

## Collision rules

Derived from the char at the player's target tile:

| Char class | Rule |
|------------|------|
| ` ` and most floor chars | Passable |
| `█` `▓` `♣` `♠` `H` `∘` `⊗` `▣` `◻` `◈` `✿` `R` `▐▌▀▄│─` | Impassable |
| `≈` | Passable only if surfing (deep ocean) |
| `~` | Passable on foot (shallow water — no surf needed); no encounters except in CAVE biome |
| `↓↑→←` | Passable, forces movement in current direction |
| `►◄▲▼` | Passable in one direction only (jump ledge) |
| `i` | Passable, applies ice slide physics |
| `s` | Passable, applies slide physics |
| `=` | Passable (bridge deck) |
| `≡` | Passable only if on bike (bike bridge) |
| `¿` | Passable (stair animation plays) |
| `k` | Passable only if on bike |
| `r` | Passable only if on bike |
| `○` | Passable, triggers fall to lower floor |
| All entrance chars | Passable, triggers warp lookup |
| Furniture chars (`p v B q y ⊞ E e L`) | Impassable, triggers interaction on face-and-press |

---

## Badge & progression system

### Rule
The engine **will not load a map set** unless it can find exactly 8 distinct gym maps
in the warp graph (maps whose constant contains `GYM` and which contain a trainer
flagged as gym leader). If fewer than 8 are found, the loader throws at startup.

### Badge → HM unlock

Each badge unlocks one HM (field move). The unlock table is read from a
`progression.json` file that lives alongside the `.txt` files. The PCG system writes
this file at generation time.

```json
{
  "badge_1": "CUT",
  "badge_2": "ROCK_SMASH",
  "badge_3": "SURF",
  "badge_4": "STRENGTH",
  "badge_5": "FLY",
  "badge_6": "WATERFALL",
  "badge_7": "ROCK_CLIMB",
  "badge_8": null
}
```

If `progression.json` is absent (e.g., loading a single real-game map for testing),
the engine falls back to the canonical Emerald badge order.

### Three approaches considered for badge → HM assignment in PCG

The decision is the PCG system's, not the engine's — the engine only reads the file.
For reference, three approaches are viable:

1. **Topological (recommended for PCG):** Analyse which terrain chars gate which gym.
   If reaching Gym 3 requires crossing `≈` (surf), then Gym 2 must unlock SURF.
   The PCG system reads the map graph and assigns HMs in traversal order.

2. **Type-based:** Each PCG gym has a type. Water gym → SURF, Flying gym → FLY, etc.
   Elegant but requires the PCG to assign gym types before generating the HM table.

3. **Fixed order:** Badge N always unlocks HM N from a fixed list. Simplest; works for
   test runs with real-game maps where topology is already correct.

### Elite Four gate

The E4 map's entrance warp is always guarded: the engine checks `badgeCount == 8`
before allowing the transition. If false, an NPC blocks the door with a dialogue line.
No other story gating exists in the engine.

---

## Battle system

**Scope:** Full Gen 4 mechanics — damage formula, type effectiveness (17-type chart),
status conditions, abilities, held items, PP. Not simplified.

**Reference implementation:** Use the open-source
[Pokemon Showdown damage calculator](https://github.com/smogon/damage-calc) as a formula
reference, implemented in Java. Do not port the JS — re-implement from the Gen 4
mechanics spec.

**What we build ourselves:**
- Battle UI: HP bars, move menu, sprite display, text box
- Pokémon sprites: Gen 3 front/back sprites (from decomp or spriters-resource)
- Move animations: simple — hit flash + type-colored overlay is sufficient for v1

**Battle entry points:**
- Wild encounter: triggered by stepping on `"` (tall grass), surfing `≈`/`~`, or fishing
- Trainer battle: triggered by stepping into trainer sight line
- Static encounter: stepping on `@`

---

## Asset sources

All assets are free/open for fan use:

| Asset | Source |
|-------|--------|
| Gen 3 tilesets | Extract from Emerald decomp (`pokeemerald/data/tilesets/`) — raw 4bpp GBA tiles, convert to PNG with existing tools or `grit` |
| Player sprites | Extract from Emerald decomp (`pokeemerald/data/graphics/field_effects/`) |
| Pokémon sprites | The Spriter's Resource — Gen 3 sprite sheets |
| Font | Gen 3 bitmap font from decomp; or use a libre pixel font (e.g., Press Start 2P) |
| Battle backgrounds | Extract from Emerald decomp or draw per biome |
| SFX / music | PokeMMO-style: use open MIDI arrangements; LibGDX plays via `Music`/`Sound` |

---

## Implementation order

See individual module docs for full detail. Recommended build order:

1. `MapLoader` — parse `.txt` to `MapData` (no rendering yet)
2. `CollisionMap` + player movement on a solid-colour placeholder grid
3. `BiomeClassifier` + `AutoTiler` + `MapRenderer` (see `02_ascii_to_sprites.md`)
4. `WorldGraph` + warp transitions
5. `NPC` dialogue system + `ItemBall` pickup
6. `WildEncounter` trigger + `EncounterTable` parsing
7. `BattleEngine` (logic only, no UI)
8. `BattleUI`
9. `Trainer` sight + battle trigger
10. `BadgeSystem` + `progression.json` + E4 gate
11. `SaveManager`
12. Polish: animations, SFX, music, menus
