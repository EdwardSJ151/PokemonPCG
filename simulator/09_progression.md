# Progression System — Badges, HM Unlocks, Elite Four Gate

---

## Badge manager

```java
class BadgeManager {
    private final EnumSet<Badge> earned = EnumSet.noneOf(Badge.class);

    enum Badge { B1, B2, B3, B4, B5, B6, B7, B8 }

    enum FieldMove {
        CUT, ROCK_SMASH, SURF, STRENGTH, FLY, WATERFALL, ROCK_CLIMB
    }

    boolean has(Badge b)       { return earned.contains(b); }
    void earn(Badge b)         { earned.add(b); save(); }
    boolean canUse(FieldMove m){ return UNLOCK_MAP.get(m).stream().anyMatch(earned::contains); }
    boolean eliteFourOpen()    { return earned.size() == 8; }
}
```

`UNLOCK_MAP` maps each `FieldMove` to the `Badge` that unlocks it. This map is loaded
from `progression.json` at boot (see below). If no file exists, defaults to the fixed
order in the table below.

---

## `progression.json` format

Written by the PCG system. The engine reads it at boot from `assets/data/progression.json`.

```json
{
  "badges": [
    { "id": "B1", "gym_map": "route_3_gym", "unlocks": "CUT" },
    { "id": "B2", "gym_map": "cave_city_gym", "unlocks": "ROCK_SMASH" },
    { "id": "B3", "gym_map": "lake_town_gym", "unlocks": "SURF" },
    { "id": "B4", "gym_map": "forest_gym", "unlocks": "STRENGTH" },
    { "id": "B5", "gym_map": "mountain_gym", "unlocks": "FLY" },
    { "id": "B6", "gym_map": "snow_gym", "unlocks": "WATERFALL" },
    { "id": "B7", "gym_map": "desert_gym", "unlocks": "ROCK_CLIMB" },
    { "id": "B8", "gym_map": "final_gym", "unlocks": null }
  ],
  "elite_four_map": "elite_four_hall"
}
```

If `progression.json` is absent, the engine falls back to the default fixed mapping:

| Badge | Default HM unlock | Field effect |
|---|---|---|
| B1 | CUT | `♣` trees become passable |
| B2 | ROCK_SMASH | `⊗` rocks become passable |
| B3 | SURF | `≈` `~` tiles passable while surfing |
| B4 | STRENGTH | `▣` boulders pushable |
| B5 | FLY | Fast travel to visited towns |
| B6 | WATERFALL | `↓` waterfalls climbable upward |
| B7 | ROCK_CLIMB | `R` walls passable |
| B8 | *(none)* | Opens Elite Four |

---

## Earning a badge

Badge is awarded after the gym leader battle ends in a player victory:

1. `BattleEngine` fires `onPlayerVictory(TrainerData trainer)`
2. If `trainer.isLeader` and the leader's gym map matches a badge entry in
   `progression.json`, `BadgeManager.earn(badgeId)` is called
3. Dialogue: "You got the [Badge Name]!" + "[HM Name] can now be used outside of battle."
4. Save triggered

The gym → badge mapping uses the `gym_map` field in `progression.json`, matched against
the current map's constant.

---

## HM usage in the field

When the player tries to interact with a blocked tile:

| Tile char | Required HM | Player action |
|---|---|---|
| `♣` | CUT | Face tree, press A → "Cut?" prompt → tree removed, `♣` → ` ` on grid |
| `⊗` | ROCK_SMASH | Face rock, press A → smash animation → `⊗` → ` ` |
| `▣` | STRENGTH | Walk into boulder → push in facing direction (if target cell passable) |
| `≈` `~` | SURF | Step off land onto water tile → Pokémon surfaces, player mounts |
| `↓` | WATERFALL | Face waterfall tile from below, press A → climb up |
| `R` | ROCK_CLIMB | Face R tile, press A → "Rock Climb?" → player traverses wall |

Tree removal and rock smash are permanent within a session (reset on map reload or
save/load cycle — same as original games). Grid is updated in `MapData` and the
`TiledMapTileLayer` is rebuilt for the affected tile.

Surfing changes the player's movement mode: `PlayerState.SURFING`. In this mode:
- `≈` and `~` are passable
- Land tiles trigger dismounting
- Water wild encounters fire instead of grass encounters

---

## Elite Four gate

The Elite Four map constant is stored in `progression.json` (`elite_four_map`). If not
present, the engine looks for any map whose constant contains `ELITE_FOUR` or `POKEMON_LEAGUE`.

The gate is enforced at the warp level: the warp leading to the Elite Four map is
intercepted before transition. `WorldGraph.transition(warp)` checks:

```java
if (isEliteFourEntry(warp.destConstant()) && !badgeManager.eliteFourOpen()) {
    dialogueBox.show("You need all 8 badges to challenge the Elite Four!");
    return; // cancel transition
}
```

`isEliteFourEntry` matches `destConstant` against the `elite_four_map` value from
`progression.json`. No transition occurs; the player stays on the current tile.

---

## Boot validation

If the PCG map set is missing gyms, the engine refuses to boot:

```
[BOOT ERROR] Only 6/8 gyms reachable from start.
Missing: B7 (route_7_gym), B8 (volcano_gym)
Check that all gym maps are connected to the world graph.
```

This ensures the HM progression system has a valid unlock sequence before the player
ever enters the world.

---

## Fly (fast travel)

Fly is unlocked by Badge B5 (default). When unlocked:
- Any town or city map the player has previously visited is registered in `FlyRegistry`
- The player can open the Fly menu (bound to a key) and select a destination
- Transition to the destination map's fly-spawn tile (first `P`-warp in the town, or
  a designated spawn coord from `progression.json`)

Fly does not require a Pokémon with the Fly move in the party — it is treated as a
world navigation tool, not a battle move, for simplicity.
