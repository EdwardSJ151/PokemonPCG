# Player System — Movement, Animation, Field Moves

---

## Movement

- Movement is tile-based and 4-directional. Diagonal movement does not exist.
- The player occupies exactly one tile at all times. There is no sub-tile position.
- Movement is from tile center to tile center. Visually, the player sprite slides
  smoothly between centers, but logically the destination tile is claimed immediately
  when movement begins (collision is checked at the destination before movement starts).
- Base walk speed: 4 tiles per second.
- Run speed: 8 tiles per second. Running is always available — no Running Shoes item
  is required. Running is activated by holding the run key (default: Shift).
- Input: holding a directional key causes continuous movement at the active speed.
  A single tap moves exactly one tile, then stops.
- Facing direction updates immediately on directional key press, even if movement to
  that tile is blocked. The player turns to face the direction before any movement or
  action resolves.
- While a movement animation is in progress (the sprite is sliding to the next tile),
  new movement input is buffered. One tile of buffered input is accepted; additional
  input during that same animation is discarded. This prevents input queuing at high
  speed.

---

## Player states

The player is always in exactly one of the following states. States are mutually
exclusive.

| State | Description |
|---|---|
| `WALK` | Moving on passable floor at walk speed |
| `RUN` | Moving on passable floor at run speed (run key held) |
| `IDLE` | Not moving; no input held |
| `BIKE` | On bike; movement at bike speed (see Bike section) |
| `SURF` | On water (`≈` or `~`); mounted on Pokémon |
| `FISH` | Facing water, fishing animation playing |
| `STAIR` | Stepping on `¿`; walk animation plays, camera does not rotate |
| `LEDGE` | Hopping over `►◄▲▼`; player has no input control for duration |
| `SLIDE` | On `s` tile; player has no input control, slides in tile direction |
| `ICE_SLIDE` | On `i` tile; player has no input control, slides until wall |
| `CLIMB_WATERFALL` | Ascending `↓` waterfall tile; no input control for duration |
| `ROCK_CLIMB` | Traversing `R` tile; no input control for duration |
| `IN_DIALOGUE` | Talking to NPC or reading sign; movement blocked |
| `IN_BATTLE` | Battle screen active; overworld frozen |
| `IN_MENU` | Any menu open; movement blocked |

---

## Animation sets

Each state maps to a specific sprite animation. All player sprites are Gen 3 GBA style,
16×32 px per frame (player is two tiles tall visually but occupies one tile logically).

| State | Animation | Frames | Loop |
|---|---|---|---|
| `IDLE` | Standing | 1 (frame 0 of walk) | — |
| `WALK` | Walking | 3 per direction (12 total) | yes |
| `RUN` | Running | 3 per direction (12 total) | yes |
| `BIKE` | Biking | 2 per direction (8 total) | yes |
| `SURF` | Surfing (on Pokémon) | 2 per direction (8 total) | yes |
| `FISH` | Fishing | 3 frames (no direction variants) | no — holds on last frame until bite |
| `STAIR` | Same as `WALK` | 3 per direction | yes |
| `LEDGE` | Hop arc | 4 frames (single direction) | no |
| `SLIDE` | Slide | 2 frames (direction of slide) | yes |
| `ICE_SLIDE` | Frozen walk frame | 1 (frame 1 of walk, facing direction) | — |
| `CLIMB_WATERFALL` | Walk upward | 3 frames | no |
| `ROCK_CLIMB` | Rock climb | 4 frames | no |

Animation frame rate: 8 fps for WALK/RUN/BIKE/SURF. 4 fps for LEDGE/SLIDE.
Direction order in sprite sheet: DOWN, UP, LEFT, RIGHT (standard Gen 3 layout).

---

## Collision resolution

Before each movement step, the engine checks the destination tile. The check order is:

1. Is the destination within map bounds?
   - No → check for a `connection` edge in that direction. If one exists, trigger a
     connection transition. If none, movement is blocked.
2. Read `dest_char` from `grid[dest_row][dest_col]`.
3. Apply the collision table below.

### Collision table

| Char | Default | Condition override |
|---|---|---|
| ` ` | Passable | — |
| `"` | Passable | — (triggers encounter check on entry) |
| `'` | Passable | — |
| `.` | Passable | — |
| `:` | Passable | — |
| `m` | Passable | — |
| `i` | Passable | triggers ICE_SLIDE state |
| `x` | Passable | triggers fall animation |
| `^` | Passable | — |
| `b` | Passable | — |
| `s` | Passable | triggers SLIDE state |
| `k` | Impassable | Passable only in BIKE state |
| `r` | Impassable | Passable only in BIKE state |
| `R` | Impassable | Passable with ROCK_CLIMB unlocked + A-press |
| `=` | Passable | — |
| `≡` | Impassable | Passable only in BIKE state |
| `¿` | Passable | triggers STAIR state |
| `≈` | Impassable | Passable only in SURF state |
| `~` | Passable | Entering triggers SURF state if not already surfing |
| `↓` | Passable descending; impassable ascending | Ascending passable with WATERFALL unlocked + A-press |
| `↑↑→←` | Passable (moves player in current direction) | — |
| `►` | Passable moving EAST only | Impassable from WEST, NORTH, SOUTH |
| `◄` | Passable moving WEST only | Impassable from EAST, NORTH, SOUTH |
| `▲` | Passable moving NORTH only | Impassable from SOUTH, EAST, WEST |
| `▼` | Passable moving SOUTH only | Impassable from NORTH, EAST, WEST |
| `▐` | Impassable from WEST | Passable from EAST, NORTH, SOUTH |
| `▌` | Impassable from EAST | Passable from WEST, NORTH, SOUTH |
| `▀` | Impassable from SOUTH | Passable from NORTH, EAST, WEST |
| `▄` | Impassable from NORTH | Passable from SOUTH, EAST, WEST |
| `│` | Impassable from EAST and WEST | Passable from NORTH and SOUTH |
| `─` | Impassable from NORTH and SOUTH | Passable from EAST and WEST |
| `█` | Impassable | — |
| `▓` | Impassable | — |
| `♣` | Impassable | Passable with CUT unlocked + A-press |
| `♠` | Impassable | — |
| `H` | Impassable | Interaction only (headbutt) |
| `∘` | Impassable | — |
| `⊗` | Impassable | Passable (tile removed) with ROCK_SMASH unlocked + A-press |
| `▣` | Impassable | Push with STRENGTH unlocked; boulder moves to adjacent ` ` tile |
| `◻` | Impassable | Push (ice physics: slides until hitting wall); no HM required |
| `○` | Passable | triggers fall to lower floor |
| `n` | Impassable | — |
| All entrance chars (P G $ K F S C X W Z d g h V Q T Y N u A l j J M c f t) | Passable | triggers warp lookup on entry |
| Furniture chars (p v B q y ⊞ E e L) | Impassable | interaction on face + A-press |
| `§` | Impassable | sign text on face + A-press |
| `⊙` `*` `I` | Passable | item pickup on entry |
| `@` | Passable | triggers static encounter on entry |
| `D` | Passable | triggers warp lookup on entry |

---

## Field moves (HM equivalents)

All field moves unlock by badge. No Pokémon is required to know the move.

### CUT
- Condition: CUT unlocked (see `09_progression.md`)
- Trigger: player faces `♣` tile, presses A
- Prompt: "Cut down this tree?" [Yes / No]
- Effect on Yes: `♣` tile at that coordinate is replaced with ` ` in the active
  `MapData.grid`. `CollisionMap` is updated for that cell. `TiledMapTileLayer` is
  rebuilt for that cell only. The coordinate is added to `save.flags.cut_trees` as
  `"MAP_CONSTANT:col:row"`.
- Effect persists for the session. On map reload (re-entry), the `cut_trees` flag
  set is checked and the tile is replaced again before the collision map is built.

### ROCK_SMASH
- Condition: ROCK_SMASH unlocked
- Trigger: player faces `⊗` tile, presses A
- Prompt: "Smash the rock?" [Yes / No]
- Effect on Yes: `⊗` → ` `. Same persistence logic as CUT. Coordinate added to
  `save.flags.smashed_rocks`.
- Rock smash has a wild encounter chance (10%) when used. If triggered, a random
  Pokémon from the map's rock-smash encounter table fires (if the table exists in the
  map JSON; if not, no encounter).

### STRENGTH
- Condition: STRENGTH unlocked
- Trigger: player walks into `▣` boulder tile
- No prompt. Effect is immediate.
- The boulder moves one tile in the player's current facing direction if the target
  tile is ` ` (empty passable floor). If the target tile is anything else, the boulder
  does not move and movement is blocked.
- Boulder position is tracked in memory (not persisted to `save.json` — resets on
  map reload). Multiple boulders on one map are tracked independently.
- After pushing, the boulder's old tile becomes ` ` and the new tile becomes `▣` in
  the in-memory grid. Collision map and tile layer update for both cells.

### SURF
- Condition: SURF unlocked
- Trigger (entering water): player steps from a non-water tile onto `~`. No A-press
  needed. No prompt. Player state transitions to SURF automatically.
- Trigger (entering deep water): player steps from `~` or non-water tile onto `≈`.
  Same automatic transition.
- Trigger (exiting water): player steps from `~` or `≈` onto any non-water tile.
  Player state transitions back to previous land state (WALK or RUN).
- The player cannot enter `≈` without SURF unlocked. `~` is passable on foot without
  SURF (shallow water / puddle / pond).
- Wild encounters on `≈` while surfing: uses the map's `surf` encounter table. Rate
  and species from the map JSON.
- Wild encounters on `~` while surfing: no encounters. `~` only triggers encounters
  when the player is on foot inside a CAVE biome (see `06_encounters.md`).

### WATERFALL
- Condition: WATERFALL unlocked
- Trigger: player faces `↓` tile while positioned directly below it (i.e., the tile
  one step north of the player is `↓`), presses A
- Prompt: "Climb the waterfall?" [Yes / No]
- Effect on Yes: player enters CLIMB_WATERFALL state, moves north one tile per 0.25s
  until no longer on a `↓` tile. Player input is blocked during climb.
- Moving south onto a `↓` tile is always passable (descending). No HM or prompt needed.

### ROCK_CLIMB
- Condition: ROCK_CLIMB unlocked
- Trigger: player faces `R` tile, presses A
- Prompt: "Use Rock Climb?" [Yes / No]
- Effect on Yes: player enters ROCK_CLIMB state, traverses the `R` tile. The player
  moves through consecutive `R` tiles until reaching a non-`R` passable tile. Input
  blocked during traversal.

### FLY
- Condition: FLY unlocked
- Trigger: player opens the world map (dedicated key, default: M). The map UI shows
  all towns/cities the player has previously visited (i.e., in `save.visited_towns`).
- Player selects a destination from the list.
- Effect: instant teleport to the Fly spawn point of the selected map. The Fly spawn
  point is the first warp entry in the destination map's JSON whose `"is_fly_spawn": true`
  flag is set. If no entry has this flag, the spawn defaults to `(col=0, row=0)`.
- No animation in v1. Screen fades to black, then fades in at the destination.
- A map is added to `save.visited_towns` the first time the player sets foot on it
  AND the map's `map_type` in `map_graph.json` is `"TOWN"` or `"CITY"`.

---

## Bike

- The bike is in the player's inventory from the start of the game. No purchase or
  NPC interaction is needed to obtain it.
- Toggle: press the bike key (default: B) to switch between land state (WALK/RUN) and
  BIKE state.
- Bike speed: 16 tiles per second.
- The bike cannot be used when the active map's biome is INDOOR or CAVE. Pressing the
  bike key in those biomes does nothing and plays a denial sound.
- Bike-exclusive tiles: `k` (bumpy slope) and `≡` (bike bridge) are only passable in
  BIKE state. Attempting to enter them on foot or surfing is blocked.
- Encounter rate in tall grass is halved while on the bike.

---

## Fishing

- Trigger: player faces any `≈` or `~` tile and presses A, while the player state is
  WALK, RUN, or IDLE (not SURF, BIKE, etc.)
- The best available fishing rod is used automatically. Rod tier: OLD_ROD < GOOD_ROD <
  SUPER_ROD. The best rod in `save.inventory.key_items` is selected. If no rod is
  present, the action does nothing.
- Animation: player faces the water and a bobber appears. After a random delay of
  1.5–3.0 seconds, a nibble event fires.
- On nibble: a "!" appears above the player. The player must press A within 0.5 seconds
  to reel in. If they do not press A in time, the fish escapes and the fishing ends.
- On successful reel-in: a wild battle starts using the map's fishing encounter table
  for the active rod tier. If the map has no fishing table for that tier, nothing is
  caught (the fish "got away").
- Fishing can be cancelled at any time before the nibble by pressing B or moving.

---

## Interaction (A-press) resolution order

When the player presses A, the following checks run in order against the tile the
player is currently facing. The first matching check wins; subsequent checks are not
evaluated.

1. Is an NPC standing on that tile?
   → Trigger NPC dialogue (see `08_npc_trainers.md`)
2. Is the tile a WARP char (P G $ M K F S C X W Z d g h V Q T ⌁ N u A l j J f t D E e L c ○)?
   → Execute warp transition (destination from Warp Index)
3. Is the tile `♣` and CUT is unlocked?
   → Show "Cut?" prompt
4. Is the tile `⊗` and ROCK_SMASH is unlocked?
   → Show "Smash?" prompt
5. Is the tile `▣` and STRENGTH is unlocked?
   → Push boulder (no prompt)
6. Is the tile `~` or `≈` and SURF is unlocked and player is not surfing?
   → Enter SURF state (mount water)
7. Is the tile `~` or `≈` and the player has a fishing rod?
   → Begin fishing
8. Is the tile `p`?
   → Open PC menu (party ↔ box swap: player can deposit / withdraw Pokémon)
9. Is the tile `⊞`?
   → Open world map (displays the PCG-generated town/route map with the player's
     current location marked; player can inspect but cannot fast-travel from here)
10. Is the tile `§`?
    → Show sign text
11. Is the tile `H` (headbutt tree)?
    → Show "Headbutt?" prompt; on confirm, trigger headbutt encounter roll
12. Is the tile `♠` (apricorn tree)?
    → Pick apricorn (one per day; add to bag)
13. Is the tile `Y` (honey tree)?
    → Show "Slather honey?" prompt; if yes, consume one Honey from bag and mark tree;
      check back after 6 hours for a honey-tree encounter
14. Is the tile `◈` (Ice Rock)?
    → If player's current Pokémon is Eevee, trigger Glaceon evolution prompt
15. Is the tile `✿` (Moss Rock)?
    → If player's current Pokémon is Eevee, trigger Leafeon evolution prompt
16. Is the tile `@` (static encounter)?
    → Trigger battle with the overworld Pokémon standing there
17. Is the tile `V` with no warp entry (indoor vending machine)?
    → Open vending-machine menu (buy drinks: Fresh Water, Soda Pop, Lemonade)
18. Is the tile `I` (item in vase / flower pot)?
    → Pick up item
19. No action. Play a dull thud sound if the tile is `█`, `▓`, or any impassable char.

---

## Encounter trigger on tile entry

Wild encounter checks happen on tile entry (when the player's movement step completes
and they are now standing on the new tile). The full encounter rules are in
`06_encounters.md`. From the player system's perspective:

- After every movement step, call `EncounterSystem.checkStep(newTile, playerState, mapData)`
- If it returns an encounter, freeze player input and transition to `IN_BATTLE`
- After battle ends, resume from the tile the player is standing on
