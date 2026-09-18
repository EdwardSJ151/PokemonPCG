// Per-decision log replay against the fork's battle.log.
//
// The decomp keeps three state classes the fork does not expose as fields:
//  - the turn number each active mon has been facing (`fakeOutTurnNumber`),
//  - the observed last-used moves per side (`battlerMoves`, dedup, max 4),
//  - the last move that hit each side (`moveHit`/`moveHitBattler` — the fork
//    keeps this per pokemon in `attackedBy`, reset on switch-in).
//
// gen4 has no team preview: the fork auto-switches each side's first pokemon
// at battle start, so the initial `|switch|` lines appear before the first
// request and seed entry turns during the first sync().
'use strict';

function sideOf(src) {
	// src like "p1a: Name" / "p2b: Name"
	if (!src) return -1;
	const m = src.match(/^p([12])\w*/);
	return m ? Number(m[1]) - 1 : -1;
}

function slotOf(src) {
	if (!src) return 0;
	const m = src.match(/^p[12]([ab])\b/);
	return m ? (m[1] === 'b' ? 1 : 0) : 0;
}

class Observer {
	constructor(battle) {
		this.battle = battle;
		this.logIdx = 0;
		this.turn = 0;
		// entryTurn[s][slot] = battle.turn at which the current occupant first faced a request
		this.entryTurn = [[0, 0], [0, 0]];
		// observedMoves[s] = distinct last-used moves of side s's current active, first-seen order, max 4
		this.observedMoves = [[], []];
		// lastHit[s] = { move, source } of the last move that hit side s's active (fork attackedBy)
		this.lastHit = [null, null];
	}

	sync() {
		const log = this.battle.log;
		const fresh = log.slice(this.logIdx);
		this.logIdx = log.length;
		this.turn = this.battle.turn;

		for (const line of fresh) {
			// battle.log lines carry the LEADING '|': "switch|src|…" is
			// parts[0]; split('|') alone yields '' there (observer bug found
			// by ExpertModule — entryTurn/observedMoves were dead before).
			const parts = line.slice(1).split('|');
			switch (parts[0]) {
			case 'switch':
			case 'replace': {
				const s = sideOf(parts[1]);
				if (s < 0) break;
				const slot = slotOf(parts[1]);
				this.entryTurn[s][slot] = this.turn;
				this.observedMoves[s] = [];
				break;
			}
			case 'move': {
				// |move|src|Move Name|target|…
				const s = sideOf(parts[1]);
				if (s < 0) break;
				const name = parts[2];
				if (!name) break;
				const list = this.observedMoves[s];
				if (list.includes(name)) break; // decomp RecordLastMove dedup
				if (list.length < 4) list.push(name);
				break;
			}
			}
		}
	}

	// entry turn of a (possibly bench) pokemon: bench members entered at battle start
	// decomp moveHit/moveHitBattler: the last move that hit this pokemon (or null)
	lastHitOf(p) {
		const s = p.side === this.battle.sides[1] ? 1 : 0;
		const live = p.getLastAttackedBy ? p.getLastAttackedBy() : undefined;
		this.lastHit[s] = live ? { move: live.move, source: live.source, damage: live.damage } : null;
		return this.lastHit[s];
	}
}

module.exports = { Observer };
