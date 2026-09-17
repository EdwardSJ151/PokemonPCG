# Item System

Items are stored in the player's **bag** (save field `inventory`) as `{id: string, qty: number}` entries.
Pokémon may hold one item each (save field `party[i].held_item`).

Every item has exactly one **scope**: field-only, battle-only, both, or consumable-once.

---

## Pockets

The bag has five pockets. Items sit in exactly one:

| Pocket | Key in save | Contains |
|---|---|---|
| Items | `bag.items` | Healing, PP, status cure, battle bag items |
| Poké Balls | `bag.balls` | All ball types |
| TMs/HMs | `bag.tms` | Technical and Hidden Machines |
| Berries | `bag.berries` | All berry types |
| Key Items | `bag.key_items` | Bike, rods, Repels, Escape Rope, special items |

---

## 1. Healing items

All healing items target a single Pokémon selected from the party menu.
Usable from bag (field) and from bag mid-battle (player's turn; uses up the turn).
Not usable in trainer battles in Gen 4 (only wild battles and field).

### HP restore

| Item | HP restored | Notes |
|---|---|---|
| Potion | 20 HP | |
| Super Potion | 50 HP | |
| Hyper Potion | 200 HP | |
| Max Potion | Full HP | |
| Full Restore | Full HP + cures all status | |

Capped at max HP. Cannot use on full-HP Pokémon (field only; battle allows it).

### Revive

| Item | Effect | Condition |
|---|---|---|
| Revive | Revive fainted Pokémon at half HP | Target must be fainted |
| Max Revive | Revive fainted Pokémon at full HP | Target must be fainted |
| Sacred Ash | Revive **all** fainted Pokémon to full HP | Consumed on use; one Sacred Ash exists per game |

Revived Pokémon are no longer fainted — they can participate in battles immediately.
HP/PP state as of fainting is restored to the revive HP, then the revive HP is their new current HP.

### Status cure

| Item | Cures |
|---|---|
| Antidote | Poison |
| Burn Heal | Burn |
| Ice Heal | Freeze |
| Awakening | Sleep |
| Paralyze Heal | Paralysis |
| Full Heal | Any single status condition |
| Full Restore | Any status condition + full HP |
| Lava Cookie / Old Gateau | Any single status (regional equivalents of Full Heal) |

---

## 2. PP items

All PP items target one Pokémon, then one of its four moves.

| Item | PP restored | Notes |
|---|---|---|
| Ether | 10 PP to one move | Capped at move's max PP |
| Max Ether | Full PP to one move | |
| Elixir | 10 PP to all moves | |
| Max Elixir | Full PP to all moves | |

Usable field only. Cannot use during battle.

### PP Up / PP Max (permanent upgrade)

| Item | Effect | Notes |
|---|---|---|
| PP Up | +1 PP tier to one move | Applies up to 3 times per move; increases max PP |
| PP Max | Max PP tier to one move | Sets to max as if 3 PP Ups were applied |

These modify the move's `pp_ups` field permanently on the Pokémon. Applied field only.
PP Max is equivalent to three PP Ups applied at once — it does not stack with existing PP Ups,
it sets the counter to 3 regardless of current count.

PP tier formula (matches Gen 4):
- 0 PP Ups: base PP
- 1 PP Up: base × 6/5 (round down)
- 2 PP Ups: base × 7/5
- 3 PP Ups / PP Max: base × 8/5

---

## 3. Battle bag items (X-items)

Used from the bag **during battle only**. Field use is blocked.
Use consumes the turn. Stat boosts are in-battle only; cleared when the battle ends.

| Item | Stat affected | Boost stages |
|---|---|---|
| X Attack | Attack | +1 |
| X Defend | Defense | +1 |
| X Speed | Speed | +1 |
| X Special | Sp. Atk (Gen 4 name: X Sp. Atk) | +1 |
| X Sp. Def | Sp. Def | +1 |
| X Accuracy | Accuracy | +1 |
| Dire Hit | Critical hit ratio | Raises to stage 2 (high crit rate) |
| Guard Spec. | All stats of all your Pokémon | Prevents stat reduction by opponent for 5 turns |

X-items apply the stat change to the user's active Pokémon.
Stat stages cap at +6 / −6 (same as any other stat change).
Dire Hit sets a "high crit" flag; it does not stack if already active.

---

## 4. Poké Balls

Used from bag in battle (wild battles only; trainer battles block Poké Ball use).
Each ball type has a **catch rate modifier**:

```
catchSuccess = floor(
  (3 × maxHP − 2 × currentHP) × catchRate × ballModifier
  / (3 × maxHP)
) + statusBonus
```

If `catchSuccess ≥ 255`, catch succeeds without shakes.
Otherwise: 4 shakes, each with probability `p = floor(65536 / sqrt(sqrt(255/catchSuccess)))`.

| Ball | Modifier | Special condition |
|---|---|---|
| Poké Ball | ×1 | — |
| Great Ball | ×1.5 | — |
| Ultra Ball | ×2 | — |
| Master Ball | ×255 (guaranteed) | — |
| Net Ball | ×3.5 | Only if target is Water or Bug type |
| Nest Ball | 1 + (40 − level) × 0.1, min ×1 | Better at lower levels |
| Repeat Ball | ×3 | Only if species already in Pokédex |
| Timer Ball | 1 + turns × 0.3, max ×4 | Better as turns increase |
| Luxury Ball | ×1 | Raises friendship gain after capture |
| Quick Ball | ×4 | Only on turn 1 |
| Heal Ball | ×1 | Restores HP and cures status after capture |
| Dusk Ball | ×3.5 | Only in caves (CAVE biome) or at night |
| Dive Ball | ×3.5 | Only while surfing (player in SURF state) |
| Safari Ball | ×1.5 | Safari Zone only (not used in simulator v1) |

Status bonuses: Sleep or Freeze = +10; Poison, Burn, Paralysis = +5.

---

## 5. Repels (Key Items pocket — field only)

Track remaining steps in save: `repel_steps_remaining`.

| Item | Steps | Notes |
|---|---|---|
| Repel | 100 | |
| Super Repel | 200 | |
| Max Repel | 250 | |

Repels suppress all encounter checks. Only one active at a time.
When `repel_steps_remaining` hits 0, display "Repel's effect wore off." — see `06_encounters.md`.
Using a new Repel while one is active replaces the remaining count.

---

## 6. Escape Rope (Key Items — field only)

Returns player to the last recorded Pokémon Center they visited.
Save fields: `last_center_map`, `last_center_col`, `last_center_row`.
Plays teleport-warp animation. Cannot be used in buildings (same restriction as Fly).
Consumed on use: reduces `qty` by 1.

---

## 7. TMs and HMs

### Technical Machines (TMs)

- Single-use consumable: teaching a TM to a Pokémon removes it from the bag.
- Teach via party menu: select Pokémon → confirm move → if the Pokémon has 4 moves,
  prompt to replace one (show move summary before replacing).
- If the Pokémon cannot learn the move (not in learnset), display "But [species] can't
  learn [move]!" — no use consumed.
- TM ID → Move ID table is defined in `PokemonData.TM_MOVE_TABLE[]`.

### Hidden Machines (HMs)

- Not consumed on use (permanent — HMs cannot be removed from the bag once obtained).
- Teach to Pokémon the same way as TMs.
- Field effects are badge-gated (see `09_progression.md`): the move can be taught to
  any Pokémon at any time, but using it on the field requires the corresponding badge.
- HM moves cannot be deleted via the Move Deleter in v1 (simplification).

**Badge-unlock rule**: in this simulator, HM field effects do NOT require a Pokémon to
know the move. A player with the CUT badge can use Cut on any tree, regardless of party.
See `05_player.md` and `09_progression.md` for field move execution.

---

## 8. Evolution stones (field only)

Select from bag → choose target Pokémon. If the Pokémon can evolve with that stone, evolution
triggers immediately (see `07_battle_system.md` → Evolution section).
If the stone is not applicable to the selected Pokémon, display the item's description and abort.
Consumed on use.

| Stone | Examples |
|---|---|
| Fire Stone | Eevee→Flareon, Vulpix→Ninetales, Growlithe→Arcanine |
| Water Stone | Eevee→Vaporeon, Poliwhirl→Poliwrath, Shellder→Cloyster |
| Thunder Stone | Eevee→Jolteon, Pikachu→Raichu |
| Leaf Stone | Exeggcute→Exeggutor, Gloom→Vileplume, Weepinbell→Victreebel |
| Moon Stone | Clefairy→Clefable, Jigglypuff→Wigglytuff, Nidorina→Nidoqueen, Nidorino→Nidoking |
| Sun Stone | Gloom→Bellossom, Sunkern→Sunflora |
| Shiny Stone | Togetic→Togekiss, Roselia→Roserade |
| Dusk Stone | Misdreavus→Mismagius, Murkrow→Honchkrow |
| Dawn Stone | Snorunt(F)→Froslass, Kirlia(M)→Gallade |

---

## 9. Vitamins (EV items — field only)

Target a single Pokémon. Raise EVs permanently. Cannot raise an EV stat above 100 with
vitamins (the hard cap from vitamins; EV battle gains can exceed this).
Each vitamin raises its EV stat by 10. If already at or above 100, display "It won't have
any effect." — no use consumed.

| Item | EV stat |
|---|---|
| HP Up | HP |
| Protein | Attack |
| Iron | Defense |
| Calcium | Sp. Atk |
| Zinc | Sp. Def |
| Carbos | Speed |

---

## 10. Rare Candy (field only)

Raises target Pokémon's level by 1. If at level 100, no effect (display: "It won't have
any effect."). Triggers evolution check after level-up (same as in-battle level-up).
Consumed on use.

---

## 11. Held items

Pokémon hold items. The held item activates automatically — the player never manually
triggers it. Source of truth: Gen 4 `HOLD_EFFECT_*` constants in
`pokeheartgold/include/constants/items.h`.

### Auto-trigger during battle

| Category | Examples | Trigger condition |
|---|---|---|
| Gradual HP restore | Leftovers | End of each turn: restore 1/16 max HP |
| Pinch HP restore | Sitrus Berry (≤½ HP), Oran Berry (≤½ HP) | Auto-use when HP ≤ threshold; Berry consumed |
| Status cure (auto) | Lum Berry (any status), Cheri Berry (paralysis), Pecha Berry (poison), Rawst Berry (burn), Aspear Berry (freeze), Chesto Berry (sleep) | Auto-use on the turn the status is inflicted; Berry consumed |
| Pinch stat boost | Liechi (Atk), Ganlon (Def), Salac (Spd), Apicot (SpDef), Petaya (SpAtk), Lansat (crit rate), Starf (random +2), Micle (accuracy) | Auto-use at ≤¼ HP; Berry consumed |
| Type-resist | Occa (Fire), Passho (Water), Wacan (Electric), Rindo (Grass), Yache (Ice), Chople (Fighting), Kebia (Poison), Shuca (Ground), Coba (Flying), Payapa (Psychic), Tanga (Bug), Charti (Rock), Kasib (Ghost), Haban (Dragon), Colbur (Dark), Babiri (Steel), Chilan (Normal) | Halves damage from a super-effective hit of that type; Berry consumed |
| Recoil | Life Orb | Deals 1/10 max HP recoil to holder after each damaging move; boosts damage by 30% |
| Choice lock | Choice Band (Atk ×1.5), Choice Specs (SpAtk ×1.5), Choice Scarf (Speed ×1.5) | Holder locked into first move used; switching removes lock |
| Flinch chance | King's Rock | Gives moves a 10% flinch chance |
| Type amplifier | Plates (each type), Incenses, type gems | Boosts moves of matching type by 20%; consumed for gems after first hit |
| Stat at full HP | Griseous Orb (Giratina), Adamant Orb (Dialga), Lustrous Orb (Palkia) | Strengthens specific legendary's moves |
| Evasion/accuracy | Wide Lens (+10% acc), Zoom Lens (+20% acc if moves last), Bright Powder (−10% opponent acc) | Passive modifier |
| Weather extenders | Icy Rock (hail 8 turns), Heat Rock (sun), Smooth Rock (sand), Damp Rock (rain) | Extends weather duration from 5 to 8 turns |
| Speed reduction | Iron Ball (halves speed, grounds Flying types) | Passive |
| Misc battle | Expert Belt (SE ×1.2), Muscle Band (physical ×1.1), Wise Glasses (special ×1.1), Scope Lens (crit rate up), Razor Claw (crit rate up), Focus Sash (survive 1-hit KO at full HP, consumed), Focus Band (10% chance survive lethal hit), Shell Bell (1/8 damage dealt → HP), Flame Orb (burns holder at end of turn 1), Toxic Orb (poisons holder), Black Sludge (Poison-type: gradual heal; other: gradual damage) | Various |

### HP threshold for pinch items

- **Sitrus Berry**: triggers at ≤50% HP (Gen 4). Restores 1/4 max HP.
- **Oran Berry**: triggers at ≤50% HP. Restores 10 HP flat.
- **Pinch stat berries**: trigger at ≤25% HP.
- **Focus Sash**: only works when holder is at full HP; brings HP to 1 instead of fainting.

### EV-training held items (field effect only)

These items raise a specific EV stat after each battle by 4 extra EVs. They halve the holder's
Speed in battle as a downside (HOLD_EFFECT_EVS_UP_SPEED_DOWN).

| Item | EV raised |
|---|---|
| Macho Brace | All EVs ×2 (halves Speed) |
| Power Weight | HP +4 |
| Power Bracer | Attack +4 |
| Power Belt | Defense +4 |
| Power Lens | Sp. Atk +4 |
| Power Band | Sp. Def +4 |
| Power Anklet | Speed +4 |

### Evolution-trigger held items

Held when traded → triggers evolution. In this simulator, trading is not implemented in v1;
evolution from these items is skipped. Document them for completeness:
- Dragon Scale → Seadra → Kingdra
- King's Rock → Slowpoke → Slowking, Poliwhirl → Politoed
- Metal Coat → Scyther → Scizor, Onix → Steelix
- Upgrade → Porygon → Porygon2
- Dubious Disc → Porygon2 → Porygon-Z
- Electirizer → Electabuzz → Electivire
- Magmarizer → Magmar → Magmortar
- Protector → Rhydon → Rhyperior
- Reaper Cloth → Dusclops → Dusknoir

---

## 12. Berries — field use (from bag)

Berries can also be used directly from the bag on a Pokémon (field use, not as held items).
The effect mirrors the held-item trigger but is player-initiated and consumes the berry.

| Berry | Field use effect |
|---|---|
| Oran Berry | Restore 10 HP |
| Sitrus Berry | Restore 1/4 max HP |
| Leppa Berry | Restore 10 PP to one move (prompts move select) |
| Lum Berry | Cure any status condition |
| Cheri Berry | Cure Paralysis |
| Chesto Berry | Cure Sleep |
| Pecha Berry | Cure Poison |
| Rawst Berry | Cure Burn |
| Aspear Berry | Cure Freeze |
| Persim Berry | Cure Confusion |
| Pinch stat berries (Liechi, Salac, etc.) | Apply the stat boost directly (field: treat as manual use of the boost; battle: auto-trigger) |

Berries used from bag consume one berry from `bag.berries`.

---

## 13. Fishing rods (Key Items — field only)

Fishing rods are used by pressing A while facing `≈` or `~` and selecting "Use Rod" from
the interaction menu (or automatically when the A-press targets deep water with a rod in key items).
The best rod the player has is used automatically.

| Rod | Tier | Notes |
|---|---|---|
| Old Rod | 1 | Lowest tier; mostly Magikarp and Tentacool |
| Good Rod | 2 | Mid-tier species |
| Super Rod | 3 | Rarest species; matches `encounters.fishing.super` table |

Each rod draws from the corresponding entry in the map's `encounters.fishing` JSON field.
If the current map has no fishing encounters for that rod tier, display "There's no response." and abort.

---

## 14. Other key items

These are in the Key Items pocket, non-consumable unless noted:

| Item | Effect |
|---|---|
| Bike | Toggles cycling state; required for `k`, `r`, `≡` tiles |
| Pokéflute | Wakes sleeping Pokémon blocking a tile (scripted event — static NPC encounter) |
| Silph Scope | Required to reveal ghost-type Pokémon blocking a tile (scripted) |
| Devon Scope | Same function as Silph Scope in Gen 3 |
| Vs. Seeker | No effect in v1 (re-battles not implemented) |
| Itemfinder / Dowsing MCHN | No effect in v1 (hidden items are pre-placed, not found via item finder) |
| Coin Case | Holds coins from Game Corner; no effect in v1 |
| Exp. Share | When held by a non-battling Pokémon: that Pokémon gains half the earned EXP (base formula, not full-party distribution) |
| Amulet Coin | Doubles money earned from trainer battles |
| Lucky Egg | Held item: EXP gain ×1.5 for holder |

---

## 15. Inventory in the save file

```json
"bag": {
  "items":     [{"id": "ITEM_POTION", "qty": 3}, ...],
  "balls":     [{"id": "ITEM_POKE_BALL", "qty": 10}, ...],
  "tms":       [{"id": "ITEM_TM01", "qty": 1}, ...],
  "berries":   [{"id": "ITEM_ORAN_BERRY", "qty": 5}, ...],
  "key_items": [{"id": "ITEM_BIKE", "qty": 1}, ...]
}
```

`qty` is always ≥ 1; removing the last unit removes the entry from the array.
TMs are single-use: qty is always 1 in the bag (once taught, the entry is gone).
HMs are never removed from `bag.key_items` once added.

Max bag size per pocket: 999 items (qty cap per entry), no slot count limit in v1.

---

## 16. Item use blocked cases

- Cannot use items on a Pokémon at full HP (potions) — display "It won't have any effect."
- Cannot use Revive on a non-fainted Pokémon — display "It won't have any effect."
- Cannot use field-only items in battle (X-items are the reverse — battle-only).
- Cannot use TM on a Pokémon that cannot learn the move — display the species name + "can't learn [move]!"; item not consumed.
- Cannot use a vitamin when the target EV stat is already at or above 100.
- PP Up/PP Max on a move already at max PP tier (3 PP Ups applied) — display "It won't have any effect."
- Escape Rope cannot be used inside trainer battles.
- Poké Balls cannot be used in trainer battles — display "You can't catch [trainer]'s Pokémon!"

---

## 17. Implementation notes

**Item ID strings** match the decomp constant names (`ITEM_POTION`, `ITEM_LIFE_ORB`, etc.)
for direct cross-reference. Both Emerald (`pokeemerald/include/constants/items.h`) and Gen 4
(`pokeheartgold/include/constants/items.h`) define these; for Gen 4 items not in Emerald,
use the HG constants.

**Held item evaluation** runs at specific trigger points in the battle engine:
1. After taking damage (Berries, Focus Sash, Shell Bell, Life Orb recoil)
2. End of each turn (Leftovers, Black Sludge, Flame/Toxic Orb, weather damage)
3. After using a move (Choice lock, Life Orb recoil, Gems)
4. When a status is inflicted (cure berries)
5. Priority bracket check (Quick Claw, Lagging Tail)

**Choice lock**: tracked per Pokémon as `choiceLocked: string | null` (move ID). Set on
first move used while holding a Choice item. Cleared when the Pokémon switches out.

**EV cap**: total EVs across all stats capped at 510; individual stat capped at 255. The
vitamin 100-cap is separate from the hard battle/item gain cap. Both apply.
