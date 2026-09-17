# Save System

---

## Save files

Two save files exist in the game's root folder:

| File | Written by | Purpose |
|---|---|---|
| `save.json` | Player manually saves via menu | Primary save — always preferred on load |
| `autosave.json` | Engine writes automatically on every map transition | Fallback if `save.json` is absent or corrupt |

On game load:
1. Attempt to parse `save.json`. If it parses successfully, use it.
2. If `save.json` is absent or fails JSON parsing, attempt to parse `autosave.json`.
3. If both are absent or corrupt, start a new game (no save data).

There is no multiple-slot system. One game, one save.

---

## save.json — complete structure

```json
{
  "player": {
    "current_map": "MAP_UNION_CAVE_1F",
    "col": 14,
    "row": 32,
    "facing": "SOUTH",
    "state": "WALK"
  },
  "badges": ["B1", "B3"],
  "unlocked_hms": ["CUT", "SURF"],
  "visited_towns": ["MAP_PALLET_TOWN", "MAP_PEWTER_CITY"],
  "party": [
    {
      "species": "SPECIES_BULBASAUR",
      "nickname": null,
      "level": 12,
      "current_hp": 38,
      "max_hp": 45,
      "status": null,
      "moves": [
        {"id": "MOVE_TACKLE", "pp": 30, "max_pp": 35},
        {"id": "MOVE_GROWL",  "pp": 40, "max_pp": 40}
      ],
      "stats": {"atk": 49, "def": 49, "spa": 65, "spd": 65, "spe": 45},
      "ivs":   {"hp": 15, "atk": 12, "def": 8, "spa": 20, "spd": 18, "spe": 10},
      "evs":   {"hp": 0,  "atk": 0,  "def": 0, "spa": 0,  "spd": 0,  "spe": 0},
      "exp": 432,
      "held_item": null,
      "is_shiny": false
    }
  ],
  "inventory": {
    "items":      [{"id": "ITEM_POTION", "quantity": 3}],
    "key_items":  ["ITEM_OLD_ROD"],
    "tms":        []
  },
  "last_center_map": "MAP_PALLET_TOWN",
  "last_center_col": 5,
  "last_center_row": 8,
  "flags": {
    "defeated_trainers": ["TRAINER_HIKER_DANIEL", "TRAINER_HIKER_RUSSEL"],
    "collected_items":   ["MAP_UNION_CAVE_1F:3:34", "MAP_UNION_CAVE_1F:29:72"],
    "cut_trees":         ["MAP_ROUTE_1:5:8"],
    "smashed_rocks":     [],
    "npc_flags":         {}
  },
  "play_time_seconds": 3642
}
```

---

## Field definitions

### `player`

| Field | Type | Values |
|---|---|---|
| `current_map` | string | Map constant of the map the player is standing on |
| `col` | int | Column of the player's tile, 0-indexed from left |
| `row` | int | Row of the player's tile, 0-indexed from top |
| `facing` | string | `"NORTH"`, `"SOUTH"`, `"EAST"`, `"WEST"` |
| `state` | string | The player state at save time (see `05_player.md`). Always restored as `"WALK"` or `"IDLE"` — battle and menu states are never written |

### `badges`

Array of badge IDs earned. Badge IDs match the keys in `progression.json`
(`"B1"` through `"B8"`). Order of insertion does not matter; the engine checks
set membership.

### `unlocked_hms`

Array of HM names currently available to the player. Values: `"CUT"`, `"ROCK_SMASH"`,
`"SURF"`, `"STRENGTH"`, `"FLY"`, `"WATERFALL"`, `"ROCK_CLIMB"`. This array is always
a function of `badges` + `progression.json` and is written here for fast read access
at boot. On load, if `unlocked_hms` is absent or empty but `badges` is non-empty, the
engine re-derives it from `badges` + `progression.json`.

### `visited_towns`

Array of map constants the player has stepped on where `map_type` is `"TOWN"` or
`"CITY"`. Used by the Fly system to populate the destination list. A map constant is
added the first time the player enters it; never removed.

### `party`

Array of 1–6 Pokémon objects. Index 0 is the lead Pokémon. Fields:

| Field | Type | Notes |
|---|---|---|
| `species` | string | Species constant, e.g. `"SPECIES_BULBASAUR"` |
| `nickname` | string or null | `null` means no nickname; display the species name |
| `level` | int | 1–100 |
| `current_hp` | int | Current HP. If 0, the Pokémon is fainted |
| `max_hp` | int | Computed max HP at current level + IVs + EVs |
| `status` | string or null | `null`, `"PSN"`, `"PAR"`, `"SLP"`, `"BRN"`, `"FRZ"`, `"TOX"` |
| `moves` | array | 1–4 moves. Each has `id` (move constant) and current `pp` and `max_pp` |
| `stats` | object | Current computed stats: `atk`, `def`, `spa`, `spd`, `spe` |
| `ivs` | object | Individual values 0–31 for each stat |
| `evs` | object | Effort values 0–252 each, 510 total cap |
| `exp` | int | Total experience points |
| `held_item` | string or null | Item constant, e.g. `"ITEM_ORAN_BERRY"`, or `null` |
| `is_shiny` | bool | `true` if the Pokémon has the shiny palette |

`max_hp` and `stats` are recomputed on load from `ivs`, `evs`, `level`, and the base
stats table. Writing them to the save avoids a full recalculation on every load but
they are not the source of truth — if they disagree with the recomputed values, the
recomputed values win and the save is updated.

### `inventory`

| Field | Type | Notes |
|---|---|---|
| `items` | array | Consumables and battle items. Each entry: `{"id": "ITEM_X", "quantity": N}` |
| `key_items` | array | Non-consumable key items (rods, bikes, etc.) as ID strings |
| `tms` | array | TM IDs in the player's possession. Not used for field move gating (badges gate field moves). Reserved for battle use. |

### `last_center_map` / `last_center_col` / `last_center_row`

Map constant and tile position of the last Pokémon Center nurse the player healed at.
Written when the player says YES to a healer NPC (see `08_npc_trainers.md § Healer NPCs`).
Used as the respawn point when the player blacks out. On new game, defaults to the start map and spawn tile from `progression.json`.

### `flags`

| Field | Type | Notes |
|---|---|---|
| `defeated_trainers` | array of strings | Trainer IDs (`"trainer_id"` from map JSON). A trainer in this list does not initiate battle. |
| `collected_items` | array of strings | `"MAP_CONSTANT:col:row"` for each item ball or hidden item picked up |
| `cut_trees` | array of strings | `"MAP_CONSTANT:col:row"` for each tree cut. Applied at map load: those coordinates become ` `. |
| `smashed_rocks` | array of strings | `"MAP_CONSTANT:col:row"` for each rock smashed. Same load-time application as cut trees. |
| `npc_flags` | object | `{"NPC_ID:flag_name": true}`. Controls multi-state NPC dialogue. Key is `npc_id` from map JSON + `:` + flag name. |

---

## Save triggers

### Manual save
- Player opens the start menu and selects "Save".
- A confirmation prompt appears: "Overwrite save?" [Yes / No].
- On Yes: the full game state is serialized to `save.json`. Existing file is overwritten atomically (write to `save.json.tmp`, then rename to `save.json`).
- A "Saved!" confirmation message is shown for 1.5 seconds.

### Auto-save
- Triggers silently on every map transition (both warp and connection).
- No prompt. No confirmation message.
- Writes to `autosave.json` only. Never overwrites `save.json`.
- The auto-save captures state after the transition completes: the player's
  `current_map`, `col`, and `row` reflect the destination map, not the source.

---

## Load sequence

On application start, before any game screen is shown:

1. Check for `save.json`. If present and parses cleanly, load it.
2. Else check for `autosave.json`. If present and parses cleanly, show a prompt:
   "No save file found. Load auto-save from [map name]?" [Yes / No].
   On Yes: load `autosave.json`. On No: start new game.
3. Else: start new game.

New game initial state:
- `current_map`: the map constant in `progression.json`'s `"start_map"` field.
  If absent, use the first node in `map_graph.json`'s `nodes` array.
- `col`, `row`: `progression.json`'s `"start_col"` and `"start_row"`. Default `0, 0`.
- `badges`: empty
- `unlocked_hms`: empty
- `visited_towns`: empty
- `party`: one starter Pokémon at level 5 (species defined in `progression.json`'s
  `"starter"` field; if absent, `SPECIES_EEVEE` at level 5 with default moves)
- `inventory`: empty
- `flags`: all empty
- `play_time_seconds`: 0

---

## Corrupted save handling

A save file is considered corrupt if:
- The file exists but is not valid JSON
- Required top-level keys (`player`, `badges`, `party`) are missing
- `player.current_map` does not exist in `map_graph.json`
- `player.col` or `player.row` is outside the bounds of the map's grid

On corruption: log the error with details, treat the file as absent, and fall through
to the next file (auto-save or new game).

---

## play_time_seconds

Incremented by the engine every second while the game is in the OVERWORLD or BATTLE
screen. Does not increment while menus are open or the game is paused. Stored as an
integer number of seconds. Displayed in the menu as `HH:MM:SS`. Maximum stored value
is `359999` (99:59:59); the display caps at that value but the counter continues.
