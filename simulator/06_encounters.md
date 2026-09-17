# Encounter System

---

## Encounter trigger rules

Every movement step the player takes, the engine runs an encounter check on the tile
the player just moved onto. The check fires AFTER the player's position updates.

### Which tiles trigger encounters

| Tile char | Biome | Encounter method | Condition |
|---|---|---|---|
| `"` | any except CAVE | `grass` | Always (if map has `encounters.grass`) |
| `≈` | any | `surf` | Only while player is in SURF state |
| ` `, `"`, `.`, `:`, `m`, `^`, `=`, `¿`, `~`, `i` | CAVE | `grass` | Any passable tile in a CAVE-biome map; `grass` table used regardless of char name |
| `~` | non-CAVE | none | Shallow water is walkable with no encounters when not in a cave |
| All other chars | any | none | No encounter check |

**The cave rule in full:** If the map's `primaryBiome` is `CAVE`, every movement step on
any passable tile triggers an encounter check using the map's `encounters.grass` table.
This includes stairs (`¿`), bridges (`=`), shallow water (`~`), and plain floor (` `).
It does not include impassable tiles (the player cannot step on them), warp tiles
(the warp fires first, no encounter), or furniture tiles (treated as impassable for
movement purposes).

**Fishing:** Does not trigger from movement. Triggered explicitly by the player pressing
A while facing `≈` or `~`. Plays a fishing animation, then draws from the map's
`encounters.fishing` table based on which rod the player has (best available rod is
used automatically). See `05_player.md` for fishing trigger details.

**Static encounters:** The `@` tile triggers a battle on the first step onto it.
Removed from the map after the battle (win or loss). If the player loses and
re-enters the map, the `@` tile is still gone — static encounters are one-time.
Tracked in save as `"static_encounters_used": ["MAP_CONST:col:row"]`.

---

## Encounter rate

The `rate` field in `encounters.grass` / `encounters.surf` controls how often the
check succeeds:

```
encounterChance = rate / 187.5   (capped at 1.0)
```

Each step: `random(0.0, 1.0) < encounterChance` → encounter fires.

This formula matches the Gen 3/4 step-based probability. Standard grass `rate: 10`
gives approximately 1 encounter per 19 steps on average.

---

## Species selection

When an encounter fires, the engine picks a species from the table:

1. Determine the active slot list:
   - Check time of day (morning / day / night based on system clock)
   - If `time_overrides.<period>` is non-null in the encounters JSON, use that list
   - Otherwise use the base `slots` list
2. Check for active swarm: if `encounters.grass.swarm` is set and the swarm flag is
   active in the save, replace slots 1 and 2 (indices 0 and 1) with the swarm species
   at the table's min and max level. No swarm is active by default.
3. Pick a slot using weighted random selection: each slot's `rate` is its weight.
   `random(0, sum_of_all_rates)` → select slot whose cumulative weight contains that value.
4. Level: `random_int(min_level, max_level)` inclusive.
5. Construct the wild Pokémon from species base stats + random IVs (each 0–31) +
   zero EVs + moveset from level-up learnset at that level.

---

## Time of day

- Morning: 04:00 – 09:59
- Day: 10:00 – 19:59
- Night: 20:00 – 03:59
- Based on the system clock at the time of the encounter check.
- If `time_overrides` has a non-null entry for the current period, it replaces the
  base slot list entirely (not merged — full replacement).

---

## Encounter rate suppression

The encounter counter resets to a minimum cooldown of 2 steps after any battle ends
(win, loss, or run). No encounters trigger for 2 steps after leaving a battle. This
prevents back-to-back encounters on exit.

After a step that triggers an encounter, the cooldown applies regardless of outcome.

---

## Repel mechanic

If the player has an active Repel item in their inventory (tracked in save as
`"repel_steps_remaining": N`), encounter checks are suppressed entirely. Each step
decrements the counter by 1. When it reaches 0, the engine displays "Repel's effect
wore off." No encounter fires on the step that depletes the repel.

Repel items:
- Repel: 100 steps
- Super Repel: 200 steps
- Max Repel: 250 steps

Only one repel is active at a time. Using a second repel while one is active replaces
the remaining count with the new repel's value.

---

## Encounter data source

All encounter data is loaded from the map's JSON `encounters` field at map load time.
There is no separate encounter file. The engine does not modify encounter data at
runtime (repel suppresses the check, it does not alter the table).

If the `encounters` field is absent from the map JSON, no encounter can ever fire on
that map, regardless of tile chars or biome.
