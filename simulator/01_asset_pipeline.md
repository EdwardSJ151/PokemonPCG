# Asset Pipeline — Gen 3 Tileset Extraction & Atlas Packing

Source: `pokeemerald/` decomp (already on disk). All tile graphics are stored as
raw PNG files extracted from the GBA ROM by the decomp toolchain.

---

## What we need vs what the decomp provides

| Need | Decomp source | Notes |
|---|---|---|
| Ground tiles (grass, path, water, sand, snow) | `graphics/tilesets/*/tiles.png` | Each tileset is a 128×px PNG strip |
| Metatile composition | `data/tilesets/*/metatiles.bin` | 8×8 sub-tiles composed into 16×16 metatiles |
| Metatile attributes | `data/tilesets/*/metatile_attributes.bin` | Behavior + layer type per metatile |
| Player sprites | `graphics/sprites/player/` | Walking, running, cycling, surfing, fishing |
| NPC sprites | `graphics/object_events/pics/people/` | All NPC classes |
| Pokémon battle sprites | `graphics/pokemon/*/front.png` + `back.png` | 64×64 per species |
| Battle backgrounds | `graphics/battle_anims/backgrounds/` | |
| UI elements | `graphics/interface/` | Dialogue box, HP bar, menus |
| Audio | `audio/` | BGM (.midi / .s), SFX | Only if we use the decomp's audio |

We do NOT need to run any extraction script — the decomp already provides PNG files.
The metatile composition step is the only processing required.

---

## Metatile → sprite pipeline

A Gen 3 metatile is 16×16 px, composed of four 8×8 sub-tiles arranged in a 2×2 grid,
with two layers (bottom + top). The decomp stores these as indices into the tileset PNG.

### Step 1 — Compose metatile PNGs

Write a one-time Java tool (or Python script) that:
1. Reads `data/tilesets/<primary>/metatiles.bin` — array of 8-byte records, each = four
   2-byte tile indices (bottom layer) + four 2-byte tile indices (top layer)
2. For each metatile index, reads the four 8×8 tiles from `graphics/tilesets/<tileset>/tiles.png`
   (tile index × 8 px offset in the strip)
3. Composites bottom layer tiles into a 16×16 image, then overlays top layer tiles
   (respecting palette transparency)
4. Saves `composed/<tileset>/<metatile_id>.png`

This is a one-time offline step, not a runtime cost.

### Step 2 — Classify metatiles by behavior

`metatile_attributes.bin` gives each metatile a behavior byte (MB_NORMAL, MB_TALL_GRASS,
MB_WATER, etc.) and a layer type. Cross-reference the behavior with the ASCII symbol table
to know what char each metatile produces. Group metatiles by their char output:

| Char | Behavior(s) | Sprite group name |
|---|---|---|
| ` ` | MB_NORMAL, MB_SAND, MB_ASH | `floor` |
| `"` | MB_TALL_GRASS, MB_LONG_GRASS | `tall_grass` |
| `~` | MB_WATER, MB_POND_WATER, MB_SHALLOW_WATER | `water_shallow` |
| `≈` | MB_WATER_SEA | `water_deep` |
| `█` | MB_IMPASSABLE, MB_MOUNTAIN_TOP | `wall` |
| `♣` | (tree metatile ids, no behavior) | `tree` |
| `.` | MB_SAND, MB_ASH, MB_SNOW_SHALLOW | `floor_variant` |
| `:` | MB_SAND_DEEP, MB_SNOW_DEEP | `floor_deep` |

### Step 3 — Select one representative sprite per group per biome

Each biome needs one concrete metatile sprite per char group. Selection rule: pick the
metatile that appears most frequently in maps of that biome type (by histogram over all
`.txt` files). This avoids hand-picking and stays data-driven.

Produce one PNG per (biome, char-group) pair — this is what the atlas stores.

---

## Autotile blob sheet layout

For chars that need autotiling (` `, `█`, `"`, `~`, `≈`, `.`, `:`), produce a 47-frame
sprite sheet per (biome, char-group). Frame order follows the standard blob bitmask
lookup (same as RPG Maker's TileA format, reordered):

```
blob_sheet_grass_temperate.png   — 47 tiles × 16×16 = 752×16 or arranged as 8 rows × 6 cols
blob_sheet_wall_cave.png
blob_sheet_water_shallow.png
... etc
```

The 47 frames map directly to the bitmask output of `AutotileResolver.java`. See
`02_ascii_to_sprites.md` for the resolver logic.

For chars that do NOT autotile (point sprites), a single 16×16 PNG per biome suffices:
```
sprite_tree.png
sprite_sign.png
sprite_entrance_center.png    (P char)
sprite_entrance_gym.png       (G char)
... etc
```

---

## Transition tilesets

When two biomes share a border (e.g., TEMPERATE grass meets DESERT sand), a transition
strip blends them. These are NOT autotiled — they are fixed 4-directional edge tiles:
N edge, S edge, E edge, W edge, NE corner, NW corner, SE corner, SW corner = 8 tiles per
pair.

| Pair | Transition needed |
|---|---|
| TEMPERATE ↔ DESERT | grass-to-sand edge |
| TEMPERATE ↔ SNOW | grass-to-snow edge |
| TEMPERATE ↔ CAVE | cliff-to-cave-entrance (handled by warp char, not biome edge) |
| DESERT ↔ WATER | sand-to-water edge |
| SNOW ↔ WATER | snow-to-water edge |
| URBAN ↔ any | urban path meets terrain — path edge tiles |

Transitions only occur at map borders (walking off the edge into the next map). Within
a single map, char-level overrides handle local variation (a beach section in a TEMPERATE
map is just `.` chars rendered with sand sprites regardless of primary biome).

---

## TextureAtlas packing

LibGDX's `TexturePacker` (offline, run once) packs all sprites into atlases:

```
assets/tilesets/
  TEMPERATE.atlas + TEMPERATE.png
  SNOW.atlas + SNOW.png
  DESERT.atlas + DESERT.png
  CAVE.atlas + CAVE.png
  INDOOR.atlas + INDOOR.png
  URBAN.atlas + URBAN.png
  WATER.atlas + WATER.png
  FOREST.atlas + FOREST.png
  transitions.atlas + transitions.png    (all 8-tile transition strips)
  entrances.atlas + entrances.png        (all building entrance door sprites)
```

At runtime, `AssetManager` loads only the atlases needed for the current map and the
adjacent maps being preloaded. Cave interior maps never load TEMPERATE.atlas.

### Atlas naming convention

Within each atlas, regions are named:
```
blob_floor_0 … blob_floor_46       (47 autotile variants for walkable floor)
blob_wall_0 … blob_wall_46         (47 variants for impassable wall)
blob_grass_0 … blob_grass_46       (47 variants for tall grass)
blob_water_shallow_0 … _46
blob_water_deep_0 … _46
point_tree                          (single tree sprite)
point_sign
point_item_ball
point_door_center                   (P entrance)
point_door_gym                      (G entrance)
point_door_shop                     ($ entrance)
... (one per entrance char)
building_generic_roof_L/M/R/solo/narrow  (generic house — all width variants)
building_generic_wall_L/M/R/solo
building_center_roof_L/M/R/solo/narrow  (Pokémon Center — red-white roof, blue cross on M)
building_center_wall_L/M/R/solo
building_gym_roof_L/M/R/solo/narrow     (Gym — stone/badge themed)
building_gym_wall_L/M/R/solo
building_mart_roof_L/M/R/solo/narrow    (Mart — blue roof, storefront stripe)
building_mart_wall_L/M/R/solo
door_generic
door_center                         (Pokémon Center sliding glass doors)
door_gym                            (stone arch)
door_mart                           (shop awning + mat)
bridge_deck
bridge_rail
bridge_under_water
bridge_under_wall
```

---

## Player and NPC sprites

Source: `graphics/object_events/pics/` in the Emerald decomp.
Format: 16×32 px per frame (player is taller than one tile), 4-directional walking
animation (3 frames per direction = 12 frames total per character).

Pack all NPC sprites into a single `npcs.atlas`. Player sprites into `player.atlas`
(walking, running, surfing, cycling, fishing — one animation set per field state).

---

## Pokémon battle sprites

Battles use **Gen 4 assets** — not Gen 3. The overworld uses Gen 3 tiles; the battle
screen is visually entirely Gen 4. These two asset sets are completely separate.

Source: `pokeheartgold/graphics/pokemon/` or `pokeplatinum/graphics/pokemon/` —
`front.png` (96×96 px) and `back.png` (96×96 px) per species. Gen 4 covers all 493
Pokémon (National Dex up to Arceus). Gen 3 only covers 386 — using Gen 3 battle
sprites would make Pokémon 387–493 unavailable.

Pack all frontsprites into `pokemon_front.atlas`, backsprites into `pokemon_back.atlas`.
Load on demand during battle; unload after.

## Trainer battle sprites (Gen 3 — battle intro only)

The trainer sprite shown when a trainer sends out their Pokémon is the **only Gen 3
asset used inside a battle**. Source: `pokeemerald/graphics/trainers/` — one PNG per
trainer class (e.g., `hiker.png`, `gym_leader_brawly.png`). These are larger portraits
(64×64 or 80×80 px depending on class) used only in the trainer battle opening animation.

Pack all trainer class sprites into `trainer_classes.atlas`.

The mapping from `trainer_class` string in the trainer JSON to sprite filename is:
```
"HIKER"      → graphics/trainers/hiker.png
"YOUNGSTER"  → graphics/trainers/youngster.png
"GYM_LEADER" → per-leader sprite if available, else generic gym_leader.png
... (full mapping defined at implementation time)
```

If a `trainer_class` has no matching sprite, use a generic placeholder sprite.

---

## One-time extraction checklist

- [ ] Write `tools/compose_metatiles.py` — metatile composer for Emerald tilesets
- [ ] Run for `gTileset_General` (outdoor ground)
- [ ] Run for `gTileset_Cave` (cave interior)
- [ ] Run for `gTileset_Building` (indoor)
- [ ] Run for secondary tilesets (gTileset_Rustboro, gTileset_Mauville, etc.) as needed
- [ ] Classify composed metatiles by behavior → char group
- [ ] Select representative sprites per (biome, group)
- [ ] Generate blob sheets (47 variants) for autotiled chars — may need manual art for
      transition frames if the decomp doesn't have all 47 variants
- [ ] Pack atlases with TexturePacker
- [ ] Validate atlas load in LibGDX test scene

---

## Asset sourcing gaps

The Emerald decomp covers TEMPERATE, CAVE, INDOOR, URBAN, and partial FOREST biomes.
SNOW and DESERT biomes are not in Emerald — they come from:
- SNOW: `pokeplatinum/` graphics (Mt. Coronet, Route 216/217 tilesets)
- DESERT: Emerald's Route 111 tileset (`gTileset_Desert`)

Both decomps are already on disk. The metatile composer runs on any decomp.
