# Task 1 — Pokemon Showdown as the Gen 4 battle backend

The Java side needs an authoritative Gen 4 battle engine. We use the local
clone `pokemon-showdown/` (v0.11.11, `dist/` prebuilt): it carries the full
Gen 4 dex (moves, abilities, items, type chart, damage formula) and a
`gen4customgame` format (`config/formats.ts:5588-5596`) with no legality
checks and no team preview, so any team of real species runs as-is.

Deliverables (this directory):

- `battle_test.js` — six tiny one-mechanic battles, run directly
- `showdown_daemon.js` — long-lived stdio daemon the Java trainer AI talks to

Both are read-only on the clone; scratch only in `/tmp`.

## 1. Running the mechanic tests

```
node qwenwork/task1/battle_test.js
```

Expected: `6/6 mechanics confirmed`, exit 0, < 1 s, byte-identical output
across runs (fixed seed `[7,3,11,5]` per battle, zero IVs, explicit
natures/abilities). Each test is one battle, 1–2 Pokémon per side, ending
by knockout; a test PASSes when a specific raw protocol line appears:

| # | battle | proves |
|---|--------|--------|
| 1 | Gengar Thunderbolt → Gyarados | 4× type chart: `|-supereffective|p2a: Gyarados` (Electric 2× Water × 2× Flying) |
| 2 | Lucario Close Combat (Blissey cannot hurt Steel) | self stat drops on the *user*: `|-unboost|p1a: Lucario|def|1` and `\|spd\|1` (this engine's data: def+spd) |
| 3 | Garchomp Swords Dance | 2-stage boost: `|-boost|p1a: Garchomp|atk|2` |
| 4 | Gyarados Dragon Dance | atk+1 and spe+1: `|-boost|…|atk|1`, `|-boost|…|spe|1` |
| 5 | Blissey holding Leftovers vs Gengar | item recovery 1/16 max HP per turn: `|-heal|p1a: Blissey|296/315|[from] item: Leftovers` |
| 6 | Garchomp vs Rotom→Gyarados switch-in | Intimidate on entry: `|-ability|p2a: Gyarados|Intimidate|boost` + `|-unboost|p1a: Garchomp|atk|1` |

## 2. The sim API surface used

Everything comes from `require('<repo>/pokemon-showdown/dist/sim')`:

- `Sim.BattleStream` — the base stream. Write commands, read updates:
  - `>start {"formatid":"gen4customgame","seed":[7,3,11,5]}`
  - `>player p1 {"name":"A","team":[…PokemonSet…]}` (p2 next)
  - `>p1 <choice>` / `>p2 <choice>` — a player's choice for the turn
  - `>forcewin pN`, `>forcetie`, `>forcelose pN` — end the battle
  - `>reseed [a,b,c,d]` — reseed the PRNG mid-battle
  Read side: `for await (const chunk of base)`; chunks are
  `type\ndata` with type `update`/`sideupdate`/`end`.
- `Sim.getPlayerStreams(base)` — returns `{omniscient, spectator, p1,
  p2, p3, p4}`. Writes to a `pN` substream are auto-prefixed `>pN `.
  **`|request|` JSON is delivered only to that player's own substream** —
  the Java side reads the p1 stream to get the user's turn request.
  **One battle per BattleStream**: the stream's `atEOF` is sticky once a
  battle ends, so each daemon battle id owns a fresh stream pair.

Protocol facts the Java side must handle (observed in runs):

- Secondary messages are dash-prefixed: `|-supereffective|`,
  `|-resisted|`, `|-immune|`, `|-boost|…|atk|2`, `|-unboost|…|atk|1`,
  `|-heal|…|[from] item: Leftovers`, `|-ability|…|Intimidate|boost`,
  `|-damage|…|0 fnt`, `|-status|`, `|-enditemeffect|`.
- Choices are **1-based** (Gen 4): `move 1..4` (a move id/name also works),
  `switch N`, `team N`. An invalid choice produces
  `|error|[Invalid choice] …` on that player's stream and the battle
  **stalls** — the engine has no choice timeout.
- Battle end: `|win|<winner name>` (trailing pipe after name) or `|tie`
  (**no** trailing pipe). `|end|` is dropped by the stream router and
  never reaches a player stream.
- Team sets: `PokemonSet` objects; **always pass explicit
  `ivs:{hp:0,atk:0,def:0,spa:0,spd:0,spe:0}`** — missing IVs default to 31
  and inflate HP/stats. Level 50 throughout (project convention).

Engine crash caveat (clone bug, worked around, not fixed — the clone is
read-only): `Battle.sendUpdates` dereferences `this.sides[0].name`, and
sides are created lazily by `setPlayer`. Any log line flushed before p1
has a player (e.g. `>forcetie` right after `>start`) throws. The daemon
avoids this by registering p1 → p2 in order during `start`; Java must only
send `request`/`cancel` after the `started` event.

## 3. The daemon protocol

```
node qwenwork/task1/showdown_daemon.js
```

Line-delimited JSON on stdio; many battles concurrently, one battle id =
one internal stream pair. Every battle has exactly two players: p1
(the Java-controlled side) and p2 (the CPU side); both teams arrive in
the `start` request.

Requests (stdin):

| op | fields | effect |
|----|--------|--------|
| `start` | `id`, `formatid?` (default `gen4customgame`), `seed?` (default `[7,3,11,5]`), `name1`, `team1`, `name2`, `team2` | create the battle, register p1 then p2 |
| `request` | `id`, `side: p1\|p2`, `choice` | submit that side's choice for the turn |
| `cancel` | `id`, `side?` | `>forcetie` (no side) or `>forcewin <side>` |

Events (stdout, one JSON per line): `{"type":"started"|"line"|"request"|
"cancelled"|"win"|"error","id",…}` — `line` carries one raw protocol line
with `side: omni|p1|p2`; `win.result` is the winner's name, or `"tie"` /
`"ended"`; `error` never kills the process.

Example session (abridged):

```
→ {"id":"b1","op":"start","name1":"You","team1":[…],"name2":"CPU","team2":[…]}
← {"type":"started","id":"b1"}
← {"type":"line","id":"b1","side":"omni","line":"|turn|1"}
← {"type":"line","id":"b1","side":"p1","line":"|request|{…user turn…}"}
→ {"id":"b1","op":"request","side":"p1","choice":"move 1"}
→ {"id":"b1","op":"request","side":"p2","choice":"switch 2"}
← {"type":"line","id":"b1","side":"omni","line":"|-damage|…"}
…
← {"type":"win","id":"b1","result":"CPU"}
```

Verified (smoke test): battle to win via request ops; two concurrent
battle ids; `cancel` → `|tie`; `cancel` with `side` → `|win|<that side>`;
unknown op and request-after-end both produce `error` events with the
process staying alive.

## 4. What the Java side does with it

Java owns both sides' decisions: it parses its own `|request|` JSON from
the p1 stream, picks a choice string (1-based indices), and forwards it.
The CPU side (p2) is driven by the reimplementation documented in
`qwenwork/task2/TRAINER_AI.md` — for now the daemon tests drive p2 with
trivial policies, which is enough to exercise the protocol end to end.

The Java trainer AI should be
tuned to match *this* engine, and where it diverges from the decomp, that
is documented in Task 2.
