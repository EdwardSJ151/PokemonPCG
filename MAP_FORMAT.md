# Map File Format — Simulator Reference

---

## File structure

Every file is UTF-8 with 70-char `=` section banners. Sections in order:

1. **Header** — name, constant, game (`emerald` / `heartgold` / `platinum`), size (cols × rows), matrix (Gen 4) or tilesets (Emerald), maps sharing the layout (Emerald only)
2. **Terrain grid** — column ruler + numbered rows; one char per tile; width = cols from header
3. **Legend** — only the chars that actually appear on this map; one row per `(char, description)` pair (same char can have two rows if it means two different things on the same map)
4. **Elevation Addendum** — present only when bridge tiles (`=`) have non-trivial tiles underneath; lists each bridged coord with its under-char
5. **Warp Index** — each warp: char, coord, destination constant, spawn coord in destination space; Emerald also has a Connections block (adjacent maps by cardinal direction + tile offset)
6. **Wild Encounters** — encounter tables by method (grass / surf / fishing); HG adds Morning/Day/Night slots, Hoenn Sound, Sinnoh Sound; PT adds Day/Night slots, Swarm, PokéRadar, GBA inserts
7. **Honey Tree Encounters** — Platinum only, global table
8. **Item Balls** — coord + item name for visible pickups (`⊙`)
9. **Hidden Items** — coord + item name for invisible pickups (`*`)
10. **Field Obstacles** — coord + type (Rock Smash rock, Cut tree, Strength boulder, etc.)
11. **Sign Index** — coord + sign text
12. **Berry Tree Index** — Emerald only
13. **NPC Index** — coord + sprite name + dialogue (+ flag tag when the NPC is story-gated)
14. **Trainer Index** — coord + class/name + party + pre/defeat/post dialogues; Emerald adds VS Seeker rematches
15. **Rival Encounters** — Emerald only

---

## Coordinate system

- Origin: top-left = (col=0, row=0)
- Col increases right, row increases down
- All sections use the same system
- Warp spawn coords are in the *destination* map's space
- Filename from constant: lowercase, strip prefix (`MAP_` → HG, `MAP_HEADER_` → PT, `LAYOUT_` → EM), append `.txt`

---

## Elevation

No Z values are stored. What exists:
- `=` bridge tiles — check Elevation Addendum for the under-char; if under is passable water, the coord is accessible at water level (surfing)
- `¿` stair tiles — marks a slope; no target elevation or direction encoded
- Everything else is flat

---

## Complete char reference

### Terrain / traversal

| Char | Meaning | Games |
|------|---------|-------|
| ` ` | Walkable floor | all |
| `█` | Impassable (wall, cliff, mountain top) | all |
| `▓` | Building body — enterable only via an adjacent entrance char | all |
| `¿` | Stair / slope | all |
| `=` | Bridge deck | all |
| `≡` | Bike bridge | PT, EM |
| `^` | Mountain floor | PT only |
| `k` | Bumpy slope (requires bike) | EM |
| `s` | Slide (E/W/N/S) | PT |
| `r` | Bike ramp / slope / parking | PT only |
| `R` | Rock Climb wall | PT |
| `►◄▲▼` | Jump ledge (E/W/N/S) — one-way | all |
| `▐▌▀▄│─` | Directional impassable wall | all |

### Water

| Char | Meaning | Notes |
|------|---------|-------|
| `≈` | Sea / ocean water | surf to traverse |
| `~` | River, pond, puddle, shallow water | surf / wade |
| `↓↑→←` | Waterfall / current (cardinal) | |
| `↗↖↘↙` | Diagonal current | |
| `∘` | Stone in water / sea rock | impassable |
| `o` | Whirlpool | HG only |
| `○` | Fall hole (drops to lower floor) | EM |

### Ground surface variants
Same mechanic in all games — use a different sprite based on the map's environment.

| Char | Meaning |
|------|---------|
| `"` | Tall grass — triggers wild encounter |
| `.` | Sand / snow / ash — passable cosmetic floor |
| `:` | Deep sand (EM) / deep snow (PT) — slowing terrain |
| `m` | Mud / deep mud |
| `i` | Ice (slippery) |
| `x` | Cracked ice (EM) |

### Vegetation / natural objects

| Char | Meaning |
|------|---------|
| `♣` | Tree (impassable) — also crown (top of tree canopy) |
| `♨` | Hot spring (EM) |
| `b` | Berry patch / berry soil |
| `H` | Headbutt tree — interactive, impassable (HG outdoor maps only) |

### Interactive / furniture (behavior tiles, not warps)

| Char | Meaning | Games |
|------|---------|-------|
| `p` | PC | all |
| `v` | TV | all |
| `B` | Bookshelf / mart shelf | all |
| `q` | Counter / table | all |
| `y` | Trash can | all |
| `⊞` | Town map board | HG, PT |
| `z` | Magma | HG |
| `U` | Pastoria Gym puzzle ground (water-level dependent) | PT |
| `§` | Sign | all |
| `n` | Rail / fence | EM |
| `D` | Generic door warp (Gen 4 targets zero of these) | all |
| `E` / `e` | Escalator up / escalator down | PT |
| `L` | Ladder | all |

### Items on map

| Char | Meaning |
|------|---------|
| `⊙` | Visible item ball |
| `*` | Hidden item |
| `I` | Item ball overlay (rendered on grid) |
| `@` | Static Pokémon encounter |

### Overworld field objects

| Char | Meaning |
|------|---------|
| `⊗` | Rock Smash rock |
| `▣` | Strength boulder |
| `◻` | Ice block (pushable) |
| `♠` | Apricorn tree |
| `◈` | Ice Rock (Glaceon evolution) |
| `✿` | Moss Rock (Leafeon evolution) |
| `Y` | Honey Tree (EM) — slather with honey |

### Building entrance chars (warp tiles)

These appear on the tile you step on to enter the building. The char tells you the building type; the Warp Index tells you the destination.

| Char | Building type | Games |
|------|--------------|-------|
| `P` | Pokémon Center | all |
| `G` | Gym | all |
| `$` | PokéMart (normal shop) | all |
| `M` | Department Store / specialty shop | all |
| `K` | Lab / Research facility | all |
| `F` | Elite Four / Pokémon League | all |
| `S` | School | all |
| `C` | Contest Hall | all |
| `X` | Game Corner | all |
| `W` | Museum / exhibit | all |
| `Z` | Battle facility | HG, EM |
| `d` | Day Care | all |
| `g` | Gate / gatehouse | all |
| `h` | House | all |
| `V` | Tower / shrine | HG |
| `Q` | Global Terminal | HG, PT |
| `T` | Major station (Magnet Train — HG; Jubilife TV — PT) | HG, PT |
| `⌁` | Radio Tower (HG) / Cable Car Station (EM) | HG, EM |
| `N` | Safari Zone / Pal Park | HG |
| `u` | Harbor | EM |
| `A` | Ship / port | HG |
| `l` | Lighthouse | HG |
| `j` | Library | PT |
| `J` | Snowpoint Temple | PT |
| `M` | Pokémon Mansion | PT only |
| `c` | Cave entrance | all |
| `f` | Forest entrance | HG |
| `t` | Tunnel / underground path | HG, PT |

---

## Bridge / under-bridge coverage

**If a map has no Elevation Addendum, all its `=` tiles are over plain walkable floor.**

When the section is present, every `=` tile with a non-trivial under is listed. Trivial (plain floor ` `) entries are omitted.

Under-chars observed:
- `≈` / `~` — water below; accessible by surfing
- `█` — impassable below; tile only reachable from above
- `♣` — tree below; impassable below
- `▼` — ledge below; still one-way

---

## Chars that need environment-aware sprites

These chars are mechanically identical across all games but visually different depending on the map's biome. The Java game should select the sprite based on the map's game + region context, not the char itself.

| Char | EM | PT | HG |
|------|----|----|-----|
| `.` | Sand / ash | Snow | — |
| `:` | Deep sand | Deep snow | — |
| `"` | Tall grass | Tall grass / deep mud grass | Tall grass |
| `m` | Mud | Mud / deep mud | — |
| `T` | — | Jubilife TV Station | Magnet Train Station |
| `⌁` | Cable Car Station | — | Radio Tower |

---

## Remaining cross-game char differences (not mechanical conflicts)

These chars mean a different building in different games, but the mechanic is identical (walk in → warp to interior). Sprite selection is environment-driven.

| Char | HG | PT | EM |
|------|----|----|-----|
| `K` | Lab / Research | Pokétch Co. / Lab | Lab |
| `T` | Magnet Train Station | Jubilife TV Station | — |
| `⌁` | Radio Tower | — | Cable Car Station |
| `Z` | Battle Facility | Resort Area (rest, not battle) | Battle Facility |
