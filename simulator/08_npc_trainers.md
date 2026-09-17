# NPCs and Trainers

---

## NPC loading

NPCs are loaded from `MAP_NAME.json` → `"npcs"` array. Each entry has the following fields:

```json
{
  "npc_id": "NPC_HIKER_DANIEL",
  "col": 5,
  "row": 48,
  "name": "Hiker Daniel",
  "sprite": "HIKER_M",
  "facing": "SOUTH",
  "movement": "NONE",
  "is_trainer": false,
  "dialogue_states": [
    {
      "flag": null,
      "text": "What a surprise! I didn't expect anyone here!"
    }
  ]
}
```

All fields are required. `"gives_item"` is optional (see Item-giving NPCs below).

---

## NPC movement

The `"movement"` field in the NPC JSON (see `11_game_folder_format.md`) is the
`MOVEMENT_TYPE_*` name with the prefix stripped. If absent, default to `"NONE"`.
**Gym leaders, Elite Four, and Champion always use `"NONE"` regardless of decomp data.**

| Category | Types | Behaviour |
|---|---|---|
| Stationary | `NONE` | Never moves. Faces the direction in `"facing"`. |
| Fixed look | `LOOK_NORTH`, `LOOK_SOUTH`, `LOOK_EAST`, `LOOK_WEST`, `LOOK_NORTH_AND_SOUTH`, `LOOK_WEST_AND_EAST`, `LOOK_NORTH_AND_EAST`, `LOOK_NORTH_AND_WEST`, `LOOK_SOUTH_AND_EAST`, `LOOK_SOUTH_AND_WEST`, `LOOK_NORTH_SOUTH_AND_EAST`, `LOOK_NORTH_SOUTH_AND_WEST`, `LOOK_NORTH_WEST_AND_EAST`, `LOOK_SOUTH_WEST_AND_EAST`, `LOOK_AROUND` | Rotates facing among the listed directions on a fixed 2.0 s / 120-frame timer. Never steps to a different tile. |
| Wander | `WANDER_AROUND`, `WANDER_NORTH_AND_SOUTH`, `WANDER_WEST_AND_EAST` | Moves one tile at a time in the allowed directions on a 2.0 s timer. Steps are subject to collision (see Collision section below). |
| Walk sequence | `WALK_BACK_AND_FORTH` and all 24 `WALK_*_*_*_*` permutations | Follows the named direction sequence cyclically, one tile per 2.0 s tick. Blocked steps are skipped (NPC waits and retries next tick). |
| Rotate | `ROTATE_CLOCKWISE`, `ROTATE_COUNTERCLOCKWISE` | Rotates one cardinal direction per 2.0 s tick. Never steps. |
| Special | `FOLLOW_PLAYER`, `VS_SEEKER_SPIN`, `BERRY_SOIL`, `DISGUISE_*` | Engine-specific; implement only if the relevant feature is active. |

### NPC collision rules

NPCs are bound by the same tile-movement rules as the player.

- Before stepping, a wandering or walk-sequence NPC checks `TileKind` of the target tile
  (using `CollisionMap.classify()` from `03_map_engine.md`). If the result is IMPASSABLE,
  FURNITURE, or WARP, the NPC does not step — it faces the blocked direction and picks the
  next step in its sequence on the next tick.
- An NPC cannot step onto a tile occupied by the player.
- An NPC cannot step onto a tile occupied by another NPC.
- An NPC cannot step off the map edge (out-of-bounds counts as IMPASSABLE).
- **Exception**: a trainer walking toward the player at battle start (see Trainer Battle
  Trigger Sequence) ignores all blocking — it is an animation, not a physics interaction.
  This exception applies only to that walk, never to normal wandering.

Look-type and rotate-type NPCs never move tiles; no collision check is needed for them.

---

## Dialogue

When the player is adjacent to an NPC and presses the A button while facing the NPC:

1. Player movement is locked immediately. No further player input moves the player until dialogue ends.
2. The NPC faces toward the player. If the NPC was `"LOOK_AROUND"`, its rotation timer is paused for the duration of dialogue and resumes after.
3. The correct dialogue state is selected (see Dialogue State Selection below).
4. The dialogue box renders at the bottom of the screen.
5. Text renders letter by letter at 40 characters per second. The player may press A to skip to the end of the current page instantly.
6. When text fills the box, it pauses and shows an advance indicator. Player presses A to continue to the next page.
7. After the final page, the dialogue box closes. Player movement is unlocked. The NPC resumes its movement behavior.
8. If the NPC has a `"gives_item"` field and the item has not yet been given (flag not set), the item is added to the player's inventory after the final dialogue page. The flag is set in `save.json → flags → npc_flags` to prevent re-giving.

### Dialogue state selection

`"dialogue_states"` is an ordered array. The first entry must always have `"flag": null` — this is the default state shown when no flags are set.

Selection rule: iterate the array from **last to first**. Use the text of the first entry whose `"flag"` value is a key present in `save.json → flags → npc_flags` with a value of `true`. If no flagged state matches, fall through to the entry with `"flag": null` and use its text.

This means the last entry in the array is checked first (most-progressed story state wins).

Example with three states:
```json
"dialogue_states": [
  { "flag": null,                        "text": "I wonder if the cave is safe..." },
  { "flag": "FLAG_ENTERED_CAVE",         "text": "You made it inside! Be careful." },
  { "flag": "FLAG_CLEARED_CAVE",         "text": "You cleared it! Amazing." }
]
```
If `FLAG_CLEARED_CAVE` is set, the third text is shown. If only `FLAG_ENTERED_CAVE` is set, the second text is shown. If neither is set, the first text is shown.

---

## Item-giving NPCs

An NPC that gives an item has an additional field:

```json
{
  "npc_id": "NPC_FISHERMAN_DAVE",
  "gives_item": {
    "item_id": "OLD_ROD",
    "quantity": 1,
    "flag": "FLAG_RECEIVED_OLD_ROD"
  },
  "dialogue_states": [
    { "flag": null,                       "text": "Take this Old Rod, kid!" },
    { "flag": "FLAG_RECEIVED_OLD_ROD",    "text": "Use that rod well!" }
  ]
}
```

On first interaction: the default dialogue fires, the item is added to inventory, and `FLAG_RECEIVED_OLD_ROD` is written to `npc_flags`. On all subsequent interactions: the flagged dialogue fires and no item is given.

---

## Service NPCs

Some NPCs carry a `"service_flags"` array in their JSON entry alongside the standard
fields. The array lists zero or more strings identifying the service the NPC provides.
An NPC with no service flags simply omits the field.

Supported flags, grouped by mechanic class:

| Mechanic class | Flags that activate it | Data field | Notes |
|---|---|---|---|
| **Healer** | `"healer"` | — | Heals whole party; flag alone is the trigger |
| **Money shop** | `"vendor"`, `"money_item_vendor"`, `"decor_vendor"` | `shop_items` | `"decor_vendor"` sells decorations — not implemented in v1 |
| **Exchange** | `"checkitem_exchange"`, `"shard_berry"`, `"item_give_exchange"`, `"berry_powder_vendor"`, `"ash_vendor"`, `"star_piece_exchange"` | `exchanges` | Player gives the `"gives"` item, receives the `"receives"` item; driven entirely by the `exchanges` pairs |
| **Exchange (special currency)** | `"ap_vendor"`, `"bp_vendor"`, `"villa_catalog"` | `exchanges` | `"gives"` is a non-bag currency (AP, BP) or free — not implemented in v1 |
| **Trade** | `"trader"` | `trade` | In-game species swap; see `trade` field |
| **Move Reminder** | `"move_reminder"` | `exchanges` | Always one pair: `{"gives": "Heart Scale", "receives": "Any forgotten move"}`; player picks which forgotten move to restore |
| **Move Deleter** | `"move_deleter"` | — | Free; flag alone is the trigger |
| **Move Tutor** | `"move_tutor"` | `exchanges` (if cost) | `exchanges` lists move name and cost; absent when the tutor is free (check `exchanges` first) |
| **Name Rater** | `"name_rater"` | — | Allows renaming a party Pokémon; flag alone is the trigger |

**Dispatch rule**: when implementing the interaction loop, check fields first — if `exchanges` is present, run exchange logic; if `shop_items` is present, run shop logic; if `trade` is present, run trade logic. The flags provide context and categorisation but the data fields drive the mechanic.

---

## Healer NPCs

A healer NPC has `"service_flags": ["healer"]`. Its `"dialogue_states"` carry the
greeting as a YES/NO prompt; the `declined_msg` field on the dialogue state holds the
NO-branch response.

```json
{
  "npc_id": "NPC_LOCALID_JUBILIFE_NURSE",
  "col": 8, "row": 4,
  "name": "Pokecenter Nurse",
  "sprite": "OBJ_EVENT_GFX_POKECENTER_NURSE",
  "facing": "SOUTH",
  "movement": "NONE",
  "is_trainer": false,
  "gives_item": null,
  "dialogue_states": [
    {
      "flag": null,
      "text": "Hello, and welcome to the Pokémon Center. We restore your tired Pokémon to full health. Would you like to rest your Pokémon?",
      "yesno": true,
      "declined_msg": "We hope to see you again!"
    }
  ],
  "service_flags": ["healer"]
}
```

Interaction sequence:

1. Dialogue fires (the greeting prompt).
2. YES/NO menu appears.
3. **YES**: healing animation plays → all party Pokémon restored (HP max, PP max, all
   status cleared) → `"We hope to see you again!"` → `save.last_center_map/col/row`
   updated to the current map and the nurse's tile.
4. **NO**: `"We hope to see you again!"` → interaction ends, nothing healed.

---

## Vendor NPCs

A vendor NPC has `"service_flags": ["vendor"]`. When `"shop_items"` is present, it lists
every item the shop stocks. Prices are **not yet included** — they will be added when
item data is exposed in a future pipeline pass.

```json
{
  "npc_id": "NPC_LOCALID_CASHIER_F",
  "col": 3, "row": 5,
  "name": "Cashier F",
  "sprite": "OBJ_EVENT_GFX_CASHIER_F",
  "facing": "SOUTH",
  "movement": "NONE",
  "is_trainer": false,
  "gives_item": null,
  "dialogue_states": [{ "flag": null, "text": "" }],
  "service_flags": ["vendor"],
  "shop_items": [
    { "item_id": "ITEM_POKE_BALL" },
    { "item_id": "ITEM_POTION" },
    { "item_id": "ITEM_ANTIDOTE" }
  ]
}
```

Interaction sequence:

1. Greeting (`"Welcome! What do you need?"`) is shown.
2. Shop menu opens listing items from `"shop_items"` (price shown as `???` until
   price data is available).
3. Player selects an item and quantity → YES/NO confirmation.
4. **YES**: if `save.money >= price * qty`, deduct money and add items to bag.
5. **NO** or insufficient funds: cancel, return to menu.
6. Player selects **Quit** to close the shop.

When `"shop_items"` is absent or empty the NPC still opens the game's standard
Poké Mart UI (specialty vendors, department stores). Treat the item list as exhaustive
only when the field is present.

---

## Exchange NPCs

An exchange NPC has `"exchanges"` in its JSON entry. This field lists every item-for-item
barter the NPC offers. Each entry has `"gives"` (what the player hands over) and
`"receives"` (what the player gets back). Both sides may be item names, move names, or
descriptive strings (e.g. `"Heart Scale"`, `"Any forgotten move"`, `"2 Red Shard"`).

```json
{
  "npc_id": "NPC_LOCALID_KURT",
  "service_flags": ["checkitem_exchange"],
  "exchanges": [
    { "gives": "Red Apricorn",    "receives": "Level Ball" },
    { "gives": "Yellow Apricorn", "receives": "Moon Ball" },
    { "gives": "Blue Apricorn",   "receives": "Lure Ball" },
    { "gives": "Green Apricorn",  "receives": "Friend Ball" },
    { "gives": "Pink Apricorn",   "receives": "Love Ball" },
    { "gives": "White Apricorn",  "receives": "Fast Ball" },
    { "gives": "Black Apricorn",  "receives": "Heavy Ball" }
  ]
}
```

Exchange NPCs with `"service_flags": ["move_reminder"]` always have exactly one entry:
`{ "gives": "Heart Scale", "receives": "Any forgotten move" }`.

Interaction sequence:

1. Dialogue fires. If the NPC has multiple exchange options (e.g. Kurt), a menu opens
   listing each option by its `"gives"` item.
2. Player selects an option. If the required `"gives"` item is in the bag, it is removed
   and the `"receives"` item or move is granted.
3. If the item is not present, the NPC shows a `"You don't have that item."` message and
   the interaction ends.
4. `"exchanges"` entries are not flagged individually — the player may repeat them on any
   subsequent visit.

---

## Trainer loading

Trainers are loaded from `MAP_NAME.json` → `"trainers"` array. Each entry:

```json
{
  "trainer_id": "TRAINER_HIKER_RUSSEL",
  "col": 20,
  "row": 51,
  "name": "Hiker Russel",
  "sprite": "HIKER_M",
  "trainer_class": "HIKER",
  "is_leader": false,
  "gym_badge": null,
  "gives_item": null,
  "sight_direction": "SOUTH",
  "sight_range": 4,
  "prize_money": 256,
  "pre_battle": "You're headed to Azalea? Let my Pokémon see if you're good enough.",
  "defeat_text": "Oh, oh, oh!",
  "post_battle": "All right! I'm not leaving until my Pokémon get tougher!",
  "party": [
    { "species": "GEODUDE", "level": 4, "ivs": 0 },
    { "species": "GEODUDE", "level": 6, "ivs": 0 },
    { "species": "GEODUDE", "level": 8, "ivs": 0 }
  ]
}
```

All fields are required except `"gym_badge"` (null for non-leaders) and `"gives_item"` (null when the trainer gives nothing).

---

## Trainer sight

On every player movement step, every trainer on the current map is evaluated in order. For each trainer whose `"trainer_id"` is NOT in `save.json → flags → defeated_trainers`:

1. Determine the sight line: starting from the trainer's `(col, row)`, project a ray in `sight_direction` for `sight_range` tiles. The tiles checked are exactly `(trainer_col + dx * i, trainer_row + dy * i)` for `i = 1` to `sight_range`, where `(dx, dy)` is the unit vector for the direction.
2. If `sight_direction` is `"ALL"`, the four cardinal lines are all projected.
3. For each tile in the sight line: if the tile char is `█` or `▓`, the line is blocked at that tile. No tiles beyond it are checked in that direction.
4. If the player's tile is within the unblocked portion of any sight line, a battle is triggered.

Sight check runs after every single-tile movement step. If multiple trainers would trigger simultaneously, the one with the smallest Manhattan distance to the player goes first.

### Trainer battle trigger sequence

1. Player movement is locked.
2. An exclamation mark sprite appears above the trainer and holds for 0.4 seconds.
3. The trainer walks toward the player one tile at a time at the player's walk speed until it occupies the tile adjacent to the player in the direction facing the player. Walls and other NPCs do not block this walk — the trainer slides through (gameplay conceit, not a physics interaction).
4. The trainer faces the player.
5. `"pre_battle"` text renders in the dialogue box. Player presses A to advance.
6. Battle starts.

---

## Gym leader flow

Gym leaders have `"is_leader": true`. Their `"sight_direction"` is always `"ALL"` and their `"sight_range"` is always 0 — gym leaders do not initiate battle on sight. The player must walk adjacent to the gym leader and press A to initiate.

After the player wins:
1. `BadgeManager.earn(gym_badge)` is called.
2. Dialogue box shows: `"You got the [Badge Name]!"` on one page, then `"[HM Name] can now be used outside of battle!"` on the next.
3. Badge name and HM name are looked up from `progression.json` using `gym_badge` as the key. If `progression.json` is absent, the topological solver's runtime assignment is used.
4. If `gives_item` is non-null and the item has not already been given (key `"trainer_item_<trainer_id>"` absent from `save.json → flags`), the item is added to the player's inventory, the flag is set, and the dialogue box shows `"[Leader Name] gave you [Item Name]!"`.
5. The trainer's ID is added to `defeated_trainers`.
6. `"post_battle"` text fires on any subsequent interaction.

---

## Trainer items

A trainer may carry up to 4 items in `"items"`. These are used automatically on the trainer's turn when the trigger condition is met. Each item can be used at most once per battle (discarded after use).

| Item category | Trigger condition |
|---|---|
| Full Restore / Full Heal | Active Pokémon HP ≤ 25% of its max HP |
| Potions (Potion, Super Potion, Hyper Potion, Max Potion) | Active Pokémon HP ≤ 25% max HP, OR the item heals more than the current HP gap |
| Status cures (Antidote, Paralyz Heal, etc.) | Active Pokémon has the matching status condition |
| X-items (X Attack, X Defend, etc.) | Trainer's first turn in the battle only |
| Guard Spec. | Trainer's first turn, and Mist is not already active on the trainer's side |

Item use replaces the trainer's move on that turn. If `"items"` is empty or absent the trainer never uses items. Items are evaluated in list order; later entries are held back until the trainer has fewer remaining Pokémon than items remaining (conserve for the end).

---

## Mid-battle trainer dialogue

Two optional dialogue strings fire at specific battle milestones. Both are empty for most trainers and only populated for gym leaders and a small number of notable trainers in HG and PT.

| Field | Trigger |
|---|---|
| `"last_pokemon"` | Trainer's party is reduced to exactly one Pokémon with HP > 0 (i.e. the trainer just sent out or revealed their last alive Pokémon). Fires at most once per battle. |
| `"half_hp"` | Trainer is on their last Pokémon AND that Pokémon's HP drops to ≤ 50% of its max HP. Fires at most once per battle. |

When the trigger condition is met, the dialogue fires as an interruption — the trainer's name tab appears, the line is shown, and the player presses A before the next action resolves. If both conditions would fire on the same event (`last_pokemon` fires and that Pokémon is already below 50% HP), `last_pokemon` fires first; `half_hp` fires on the subsequent HP-drop event.

If either string is empty, that interruption is silently skipped.

---

## Double battles

A trainer with `"double": true` starts a 2v2 battle instead of a 1v1. Both players send out two Pokémon simultaneously. Double battle mechanics are **not implemented in v1** — treat `"double": true` trainers as unenterable (do not initiate battle when the player walks adjacent). In the map, the trainer's sight line still draws normally.

---

## VS Seeker rematches

`"vs_rematches"` carries the trainer's rematch tiers for the VS Seeker item. Each tier is a stronger version of the original trainer. **VS Seeker rematches are not implemented in v1** — the field is present for future use and the data is ignored.

Each tier entry shape:
```json
{
  "trainer_id": "TRAINER_LEADER_FALKNER_FALKNER_2",
  "party": [
    { "species": "Staraptor", "level": 50, "ivs": 0 }
  ],
  "prize": 6720
}
```

---

## Post-battle state

After the player wins any trainer battle:
- `"trainer_id"` is appended to `save.json → flags → defeated_trainers`.
- The trainer returns to its original `(col, row)` and `facing` direction (it does not stay adjacent to the player).
- If the player approaches the trainer again, no sight check fires. Instead, pressing A while adjacent shows `"post_battle"` text immediately.
- The trainer does not walk toward the player on re-approach.

---

## Dialogue box rendering

- The dialogue box is a fixed panel at the bottom of the screen, spanning the full viewport width and occupying approximately the bottom 20% of screen height (3 tile-heights at 48px display scale = 144px).
- The box has a solid dark border and a light interior, styled after the Gen 3 Pokémon games.
- Font: Gen 3 Pokémon font from `pokeemerald/graphics/fonts/`. Each character is 8×8 px rendered at native size (no scaling within the dialogue box).
- A speaker name tab appears above the top-left of the box when the NPC has a non-empty `"name"` field. The tab is 4 characters tall and just wide enough for the name.
- Text renders letter by letter. The time per character is constant: 1 character per 1.5 frames at 60 fps (≈ 40 characters/second). This rate is not configurable in v1.
- Pressing A while text is animating skips the animation and shows the full page immediately. Pressing A while the full page is displayed advances to the next page or closes the box.
- The maximum characters per line is determined by the box width minus 2-character padding on each side. Lines wrap at word boundaries. The box holds exactly 3 lines. A fourth line triggers a new page.
