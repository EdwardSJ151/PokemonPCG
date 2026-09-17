# Omitted Per-Map Data

Data categories that exist in the decomps but are deliberately not represented in the
renderer.  Documented here so the decision is on record and agents don't re-surface them
as gaps.

---

## All games — Roaming Pokémon eligibility

Each game has a fixed table of routes that roaming Pokémon can appear on:

| Game | Roamers | Source |
|---|---|---|
| Emerald | Latias / Latios | `src/roamer.c` |
| HeartGold | Raikou, Entei, Latias, Latios | `include/constants/roamer.h` (`ROAMER_LOC_*`) |
| Platinum | Mesprit, Cresselia | `src/roaming_pokemon.c` (`sRoamingMapIdTable`) |

**Why omitted:** The eligibility table is a game-wide mechanic, not a property of any
individual map.  It describes where a roamer *can* land after being released, not what a
player will encounter on a specific route.  The trigger event that *releases* roamers is
already rendered as a static encounter on the relevant map.

---

## Emerald — Trick House completion rewards

Eight one-time puzzle rewards given on the roof of the Trick House (Route 110
TrickHouseEnd) after each room is cleared.

**Why omitted:** Out of scope.

---

## Emerald — Battle Tent prizes

Three tents (Slateport City, Fallarbor Town, Verdanturf Town) award random items from
pools controlled by custom opcodes.  There is no static prize list in the scripts.

**Why omitted:** Out of scope.  The pools are dynamic and cannot be represented as a fixed
per-map list.

---

## Emerald — Trainer Hill time-based prize

The prize awarded at Trainer Hill Roof depends on the player's completion time.

**Why omitted:** Out of scope.  The prize is entirely runtime-dependent.

---

## HeartGold — Ruins of Alph sliding-tile puzzle

Each of the four entrance chambers contains a sliding-tile puzzle triggered by interacting
with the back wall.  Solving it causes the floor to open, dropping the player into the
underground hall below.  The puzzle is a minigame overlay with no tile presence — the
chamber grid looks identical before and after it is solved.  The warp to the second room
is always present in the data regardless of puzzle state.  Source: `src/alph_puzzle.c`,
`src/alph_checks.c`.

**Why omitted:** The puzzle is a minigame mechanic with no map-level representation.  The
NPC dialogue explaining it and the warps to adjacent rooms are already rendered.

---

## HeartGold — Bug Catching Contest

Held in National Park on Tuesday, Thursday, and Saturday.  Ten NPC opponents; prize
ladder: 1st = random Evo/Shiny Stone, 2nd = Everstone, 3rd = Sitrus Berry, 4th = Shed
Shell.  Source: `src/overlay_bug_contest.c`.

**Why omitted:** The contest is a game-wide recurring mechanic rather than static map
data.  The prize ladder is fixed but has no tie to a specific tile or NPC position.

---

## Platinum — Pokétch app locations

Fifteen-plus maps each contain an NPC that gives one or more Pokétch apps.  Source:
various `res/field/scripts/scripts_*.s` files (Pokétch Company 1F, Route 207 gate,
Eterna City Pokémon Center, Solaceon Town, Route 213 house, Celestic Town, Route 208
house, Sunyshore City east house, Pal Park lobby, Day Care, Veilstone Dept Store 2F,
Pastoria City observatory gate).

**Why omitted:** The apps are a player-progression mechanic.  Rendering "this NPC gives
the Pokétch app" adds little to a map representation; the NPC and their dialogue are
already in the NPC section.

---

## Platinum — Amity Square

MAP_AMITY_SQUARE; allowed follower species and per-species accessory pools (6 pools of 10
accessories each).  Source: `src/scrcmd_amity_square.c`.

**Why omitted:** The allowlist and accessory pools are a game-wide mechanic, not specific
to map geometry.

---

## Platinum — Feebas tiles

Four fishing tiles in MAP_MT_CORONET_B1F are randomised per save from 228 total fishing
tile positions.  Source: `src/overlay006/feebas_fishing.c` + `arc/encdata_ex.narc`.

**Why omitted:** Save-file dependent — the tile positions change each game.  The encounter
data already shows Feebas in the fishing table, which is the relevant map-level fact.

---

## Platinum — Sunyshore City Market daily seal stock

Day-of-week seal inventory in `SunyshoreMarketDailyStocks[]` (Mon–Sun arrays).  Source:
`include/data/mart_items.h`.

**Why omitted:** `mart_items.h` is absent from the checkout on this machine, so the mart
parser cannot reach it.  If the file is ever added, this could be surfaced as a specialty
mart in the existing mart-rendering path.

---

## Platinum — Veilstone Store B1F Poffin vendor

Sells one poffin per purchase for ₱6,400 (requires Poffin Case in bag).  Menu offers
ten flavour combinations.  Source: `VeilstoneStoreB1F_PoffinVendor` in
`res/field/scripts/scripts_veilstone_store_b1f.s`.

**Why omitted:** Poffins are created by an internal engine call (`ScrCmd_PoffinCreate`)
with no `AddItem` opcode — the item constant never appears in the script.  Static script
analysis cannot determine what is given, so no exchange pairs can be extracted.

---

## HeartGold — Pokéathlon Dome AP prize shop

Prize shop in `MAP_POKEATHLON_DOME_INTERIOR_1F`.  Athlete Points (AP) are spent on
drinks: Fresh Water (50 AP), Soda Pop (80 AP), Lemonade (100 AP).  Source:
`scr_seq_D49R0101_014` in `scr_seq_0123_D49R0101.s`.  Detection and pair extraction
both work correctly (confirmed in testing).

**Why omitted:** The NPC's event entry uses a numeric `scriptId` (9850–9852 range)
rather than the `_EV_scr_seq_*` symbol format that the HG renderer resolves.  Numeric
script IDs are not mapped to zone-script labels, so this NPC is never passed to
`_hg_detect_service_flags`.  Fixing this requires a global HG script-ID table lookup
that is out of scope for the current renderer.
