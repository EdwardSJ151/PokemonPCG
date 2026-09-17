'use strict';
// Gen 4 Platinum trainer AI — engine core.
//
// Faithful port of the decomp's battle math (pokeplatinum/src/battle/battle_lib.c,
// trainer_ai/trainer_ai.c) evaluated against the live fork battle
// (pokemon-showdown, require'd — read-only). Decomp quirks are ported, not
// fixed — see NOTES.md and PLAN.md "Deviations & engine facts".
//
//   eff(att, def, moveId, variant, opts)
//     variant 'active'   = BattleSystem_ApplyTypeChart (scaled damage + mask)
//     variant 'switch'   = BattleSystem_CalcEffectiveness (mask only)
//     opts.ignoreType / opts.ignoreImmunities = the SYSCTL_IGNORE_* bits (the
//     flat-damage path): no STAB, SE/NVE cleared post-chart.

const { AICpuLcg } = require('./rand.js');
const { Observer } = require('./observer.js');
const MOVES = require('./data_moves.js');
const ITEMS = require('./data_items.js');
const { rows: CHART_ROWS, sentinel: CHART_SENTINEL } = require('./data_typechart.js');

// decomp MOVE_STATUS bits (include/constants/battle/moves.h)
const SE = 2, NVE = 4, INEFF = 8;
const LEVITATED = 1 << 11, WONDER_GUARD = 1 << 18, MAGNET_RISE = 1 << 20;
const IMMUNE = INEFF | LEVITATED | WONDER_GUARD | MAGNET_RISE;
const BASIC_EFFECTIVENESS = SE | NVE;

const STAT_KEYS = [null, 'atk', 'def', 'spe', 'spa', 'spd', 'accuracy', 'evasion'];
// decomp ITEM_RECOVER_* condition bits (FlagIndex: sleep 1<<5 … confusion 1<<0)
const CURE_BIT = { slp: 32, psn: 16, tox: 16, brn: 8, frz: 4, par: 2, confusion: 1 };
// decomp BATTLE_STAT_* index stored in the STAT_BOOSTER condition
const BOOST_STAT_IDX = { atk: 1, def: 2, spatk: 4, spdef: 5, speed: 3, acc: 6 };
// fork boost key → data_items.js stages key
const STAGES_KEYS = { atk: 'atk', def: 'def', spe: 'speed', spa: 'spatk', spd: 'spdef', accuracy: 'acc' };

function s8(x) { return ((x & 0x80) ? (x - 0x100) : (x & 0xFF)); }

// fork spells type names capitalized ("Ground"); normalize to the decomp index ids
const normType = (t) => (t ? String(t).toLowerCase() : '');
// order must match the decomp type table (battle_lib.c:2399); '???' is the
// decomp's mystery type (untyped moves such as Curse)
const TYPE_IDS = ['normal', 'fighting', 'flying', 'poison', 'ground', 'rock', 'bug', 'ghost', 'steel', '???', 'fire', 'water', 'grass', 'electric', 'psychic', 'ice', 'dragon', 'dark'];
const TYPE_IDX = {};
TYPE_IDS.forEach((t, i) => { TYPE_IDX[t] = i; });
const T = { FLYING: 2, GROUND: 4, GHOST: 7, DARK: 17 };

// decomp MoveIsOnDamagingTurn (battle_lib.c:7588): these charge effects are not
// on their damaging turn while charging. Quirks ported: Fire Fang IS listed
// (bypasses Wonder Guard); Hyper Beam / Giga Impact (RECHARGE_AFTER) and Focus
// Punch (HIT_LAST_WHIFF_IF_HIT) are NOT (treated as damaging).
const CHARGE_EFFECTS = new Set([
	'BATTLE_EFFECT_BIDE',
	'BATTLE_EFFECT_CHARGE_TURN_HIGH_CRIT',
	'BATTLE_EFFECT_CHARGE_TURN_HIGH_CRIT_FLINCH',
	'BATTLE_EFFECT_CHARGE_TURN_DEF_UP',
	'BATTLE_EFFECT_SKIP_CHARGE_TURN_IN_SUN',
	'BATTLE_EFFECT_FLY',
	'BATTLE_EFFECT_DIVE',
	'BATTLE_EFFECT_DIG',
	'BATTLE_EFFECT_BOUNCE',
	'BATTLE_EFFECT_FLINCH_BURN_HIT',
]);

// decomp BattleSystem_Divide (battle_lib.c:3599): truncating; a nonzero dividend
// truncating to zero returns ±1.
function decompDivide(x, y) {
	if (y === 0) return 0;
	const q = (x / y) | 0;
	return (q === 0 && x !== 0) ? (x > 0 ? 1 : -1) : q;
}

// decomp UpateMoveStatusForTypeMul (battle_lib.c:2781)
function typeMulMask(mul, mask) {
	if (mul === 0) {
		mask |= INEFF;
		mask &= ~(NVE | SE);
	} else if (mul === 5) {
		if (mask & SE) mask &= ~SE;
		else mask |= NVE;
	} else {
		if (mask & NVE) mask &= ~NVE;
		else mask |= SE;
	}
	return mask;
}

// decomp BasicTypeMulApplies (battle_lib.c:2529) — active-variant row gate.
// Quirk ported: the roost clause carries no multiplier check.
function rowAppliesActive(def, defItemEffect, gravity, row) {
	const defI = row[1], mul = row[2];
	if ((defItemEffect === 'speeddowngrounded' || def.volatiles.ingrain)
		&& defI === T.FLYING && mul === 0) return false;
	if (def.volatiles.roost && defI === T.FLYING) return false;
	if (gravity && defI === T.FLYING && mul === 0) return false;
	if (def.volatiles.miracleeye && defI === T.DARK && mul === 0) return false;
	return true;
}

// decomp NoImmunityOverrides (battle_lib.c:2756) — switch-variant row gate.
// Asymmetric: no ingrain / roost / miracle-eye clauses here.
function rowAppliesSwitch(defItemEffect, gravity, row) {
	return !((defItemEffect === 'speeddowngrounded' || gravity)
		&& row[1] === T.FLYING && row[2] === 0);
}

// decomp BattleSystem_TypeMatchupMultiplier (battle_lib.c:3009): starts at 40
// and walks the RAW chart — no sentinel/ability handling (the 0xFE marker
// never matches an attacking type; the batched rows past it are real
// entries); integer mul/10 per matched defender type, in decomp order.
function typeMatchup40(atk, def1, def2) {
	let mul = 40;
	for (const [a, d, m] of CHART_ROWS) {
		if (a !== atk) continue;
		if (d === def1) mul = (mul * m) / 10;
		if (d === def2 && def1 !== def2) mul = (mul * m) / 10;
	}
	return mul;
}

class Engine {
	constructor(battle, opts = {}) {
		this.battle = battle;
		this.dex = battle.dex;
		// decomp AICpuLcg seeded from the battle seed (deliberately NOT battle.prng).
		// battle.prngSeed is the joined word string ("7,3,11,5"); decomp battle_main.c:1034
		// builds the 32-bit AI seed from words 1..0.
		const words = typeof battle.prngSeed === 'number'
			? [battle.prngSeed]
			: String(battle.prngSeed).split(',').map((w) => Number(w) | 0);
		const seed = (words.length > 1
			? ((words[1] & 0xFFFF) << 16) | (words[0] & 0xFFFF)
			: words[0]) >>> 0;
		this.rand = new AICpuLcg(seed || 1);
		this.initialItems = ((opts && opts.items) || []).slice(0, 4); // decomp: max 4 bag slots
		this.usedItem = null;
		this.usedItemType = 'MAX';
		this.usedItemCondition = 0;
		this.lastActive = {}; // per side: active mon seen at the last decision (fakeOut proxy)
	}

	randNext() { return this.rand.randNext(); }
	randN(n) { return this.randNext() % n; }

	// fork ground truth, prng-frozen, transcript-suppressed (documented deviation)
	dmg(src, def, moveId, opts = {}) {
		const b = this.battle;
		if (!moveId || def.fainted || (src.fainted && !opts.allowFainted)) return 0;
		const seed = b.prng.rng.seed;
		const saved = [seed[0], seed[1], seed[2], seed[3]];
		const origAdd = b.add;
		b.add = () => {};
		let n;
		try {
			const move = this.dex.getActiveMove(moveId);
			move.willCrit = !!opts.crit;
			n = b.actions.getDamage(src, def, move, true);
		} finally {
			b.add = origAdd;
			seed[0] = saved[0]; seed[1] = saved[1]; seed[2] = saved[2]; seed[3] = saved[3];
		}
		return (typeof n === 'number') ? n : 0;
	}

	// decomp Battler_Ability: suppressed → none (Multitype never suppressible);
	// gravity and ingrain kill Levitate.
	abilityOf(p) {
		// fork stores the raw team string ('' = no ability; dex spelling is
		// capitalized) — normalize the way the fork's Dex does before comparing
		let a = p.ability;
		if (a) a = String(a).toLowerCase().replace(/[^a-z0-9]/g, '');
		if (a === 'multitype') return a;
		if (this.battle.suppressingAbility(p)) return '';
		if (a === 'levitate' && (this.battle.field.pseudoWeather.gravity || p.volatiles.ingrain)) return '';
		return a;
	}

	// decomp Battler_IgnorableAbility (battle_lib.c:3108) — quirk ported: the
	// Mold Breaker branch only raises a per-turn flag and returns FALSE, so an
	// MB holder never ignores the defender's ability here, while every other
	// attacker does.
	ignorable(att, def, abilityId) {
		if (this.abilityOf(att) !== 'moldbreaker')
			return this.abilityOf(def) === abilityId;
		return false;
	}

	// decomp TrainerAI_MoveType (trainer_ai.c:3127): only Natural Gift, Judgment,
	// Hidden Power and Weather Ball are variable; every other move returns
	// "own type" (''). Hidden Power uses the decomp's own IV-bit formula
	// (bits*15/63+1, skipping the mystery index) rather than the fork's
	// getHiddenPower, whose type table differs at that edge.
	moveTypeOf(p, moveId) {
		const id = String(moveId).toLowerCase();
		const it = p.item ? this.dex.items.get(p.item) : null;
		if (id === 'naturalgift') {
			const t = it && it.naturalGift ? it.naturalGift.type : null;
			return t ? normType(t) : 'normal';
		}
		if (id === 'judgement') {
			return normType((it && it.plateType) || (it && it.orbType) || 'normal');
		}
		if (id === 'hiddenpower') {
			const iv = p.set.ivs;
			const bits = (iv.hp & 1) | ((iv.atk & 1) << 1) | ((iv.def & 1) << 2)
				| ((iv.spe & 1) << 3) | ((iv.spa & 1) << 4) | ((iv.spd & 1) << 5);
			let idx = (bits * 15 / 63 + 1) | 0;
			if (idx >= TYPE_IDX['???']) idx++;
			return TYPE_IDS[idx];
		}
		if (id === 'weatherball') {
			// decomp: no weather (or Cloud Nine / Air Lock) → uninitialized; ported as Normal
			const w = this.battle.field.weather;
			if (!w || this.cloudNineOrAirLock()) return 'normal';
			return w === 'raindance' ? 'water' : w === 'sandstorm' ? 'rock' :
				w === 'sunnyday' ? 'fire' : w === 'hail' ? 'ice' : 'normal';
		}
		return '';
	}

	// battle-wide flag: any alive active mon with un-suppressed Cloud Nine /
	// Air Lock (the decomp's NO_CLOUD_NINE)
	cloudNineOrAirLock() {
		const b = this.battle;
		for (const side of b.sides) {
			for (const p of side.active || []) {
				if (!p || p.fainted) continue;
				const a = this.abilityOf(p);
				if (a === 'cloudnine' || a === 'airlock') return true;
			}
		}
		return false;
	}

	// decomp BattlerIsGrounded (battle_lib.c:5500)
	isGrounded(p) {
		const ab = this.abilityOf(p);
		const grounded = ab !== 'levitate'
			&& !p.volatiles.magnetrise
			&& !p.getTypes().map(normType).includes('flying');
		return grounded
			|| (p.item && ITEMS[p.item] && ITEMS[p.item].hold === 'speeddowngrounded')
			|| !!this.battle.field.pseudoWeather.gravity;
	}

	// decomp Battler_IsTrapped (battle_lib.c:5514); CountAbilityTheirSide = the
	// opposing side's alive ACTIVE battlers (singles: its one active).
	isTrapped(p) {
		if (p.item && ITEMS[p.item] && ITEMS[p.item].hold === 'switch') return false;
		if (p.volatiles.trapped || p.volatiles.partiallytrapped || p.volatiles.ingrain) return true;
		const myIdx = this.battle.sides.indexOf(p.side);
		const foe = this.battle.sides[1 - myIdx].active[0];
		if (!foe || foe.fainted) return false;
		const foeAb = this.abilityOf(foe);
		if (this.abilityOf(p) !== 'shadowtag' && foeAb === 'shadowtag') return true;
		if (p.getTypes().map(normType).includes('steel') && foeAb === 'magnetpull') return true;
		if (this.isGrounded(p) && foeAb === 'arenatrap') return true;
		return false;
	}

	moveEffectOf(moveId) { return MOVES[String(moveId).toLowerCase()] || null; }

	// decomp MoveIsOnDamagingTurn: enumerated charge moves answer with the
	// LAST_OF_MULTI_TURN bit — false while charging (fork: the twoturnmove
	// volatile for this move), true on the landing turn; everything else true.
	onDamagingTurn(att, moveId) {
		if (!CHARGE_EFFECTS.has(this.moveEffectOf(moveId))) return true;
		const t = att.volatiles.twoturnmove;
		return !(t && t.effectState && t.effectState.move === moveId);
	}

	// decomp TrainerAI_GetStats: HP → current, everything else raw unboosted.
	rawStat(p, idx) {
		return idx === 0 ? p.hp : p.calculateStat(STAT_KEYS[idx], 0, 1);
	}

	// decomp AICmd_LoadBattlerSpeedRank / CompareBattlerSpeed (battle_lib.c:1188):
	// alive actives of both sides, fastest first (rank 0). Fork action speed
	// (stages, weather abilities, tailwind included) is the fork-faithful stand-in.
	speedRank() {
		const act = [];
		for (const side of this.battle.sides)
			for (const p of side.active || [])
				if (p && !p.fainted) act.push(p);
		act.sort((a, b) => b.getActionSpeed() - a.getActionSpeed());
		return act;
	}

	eff(att, def, moveId, variant, opts = {}) {
		const move = this.dex.moves.get(String(moveId).toLowerCase());
		// decomp struggle short-circuits before anything else (battle_lib.c:2573);
		// unknown moves (missing from the fork) degrade to a 40 Normal — both
		// leave the caller's zero mask untouched
		if (!move || !move.id || move.id === 'struggle')
			return { mask: 0, scaled: 40, class: 40, type: 'normal' };

		const attAb = this.abilityOf(att);
		const defAb = opts.rawDef ? this.rawAbilityOf(def) : this.abilityOf(def);
		// decomp: Normalize forces the move type to Normal (checked before inType)
		const type = normType(attAb === 'normalize' ? 'normal'
			: (this.moveTypeOf(att, move.id) || move.type || '???'));
		const atkIdx = TYPE_IDX[type];
		const attTypes = att.getTypes().map(normType);
		const dTypes = def.getTypes().map(normType);
		const defIdx1 = TYPE_IDX[dTypes[0]];
		const defIdx2 = dTypes.length > 1 ? TYPE_IDX[dTypes[1]] : defIdx1;
		const movePower = move.basePower || 0;
		const defItemEffect = (def.item && ITEMS[def.item] && ITEMS[def.item].hold) || '';
		const attItemEffect = (att.item && ITEMS[att.item] && ITEMS[att.item].hold) || '';
		const attItemPower = (att.item && ITEMS[att.item] && ITEMS[att.item].effectParam) || 0;
		const gravity = !!this.battle.field.pseudoWeather.gravity;

		if (variant === 'switch') {
			// decomp BattleSystem_CalcEffectiveness (battle_lib.c:2681)
			let mask = 0;
			// quirks ported: sets INEFF (not LEVITATED); no magnet-rise branch
			if (attAb !== 'moldbreaker' && defAb === 'levitate' && type === 'ground'
				&& !gravity && defItemEffect !== 'speeddowngrounded') {
				mask |= INEFF;
			} else {
				for (let i = 0; i < CHART_ROWS.length; i++) {
					// the decomp's 0xFE marker sits at the sentinel: break on the
					// override, otherwise the batched ghost-immunity rows at/after
					// it are part of the walk
					if (i === CHART_SENTINEL && attAb === 'scrappy') break; // decomp: no foresight here
					const row = CHART_ROWS[i];
					if (row[0] !== atkIdx) continue;
					if (!rowAppliesSwitch(defItemEffect, gravity, row)) continue;
					if (row[1] === defIdx1) mask = typeMulMask(row[2], mask);
					if (row[1] === defIdx2 && defIdx2 !== defIdx1) mask = typeMulMask(row[2], mask);
				}
			}
			// quirks ported: sets INEFF (not WONDER_GUARD); no movePower gate
			if (attAb !== 'moldbreaker' && defAb === 'wonderguard'
				&& this.onDamagingTurn(att, move.id)
				&& ((mask & SE) === 0 || (mask & BASIC_EFFECTIVENESS) === BASIC_EFFECTIVENESS)) {
				mask |= INEFF;
			}
			return { mask, scaled: 0, class: 0, type };
		}

		// decomp BattleSystem_ApplyTypeChart (battle_lib.c:2560)
		let scaled = 40; // decomp AI callers always start from 40
		let mask = 0;

		// STAB: MON_HAS_TYPE (Multitype/plate-aware via getTypes), before the
		// pre-branch, gated by SYSCTL_IGNORE_TYPE_CHECKS
		if (!opts.ignoreType && (attTypes[0] === type || attTypes[1] === type)) {
			scaled = attAb === 'adaptability' ? scaled * 2 : (scaled * 15 / 10) | 0;
		}

		// pre-branch, exact decomp order
		if (this.ignorable(att, def, 'levitate') && type === 'ground'
			&& defItemEffect !== 'speeddowngrounded') {
			mask |= LEVITATED;
		} else if (def.volatiles.magnetrise && !def.volatiles.ingrain
			&& type === 'ground' && defItemEffect !== 'speeddowngrounded') {
			mask |= MAGNET_RISE;
		} else {
			// chart walk; the 0xFE marker at the sentinel breaks the walk (skipping
			// the batched ghost immunities) on foresight / scrappy, else the batched
			// rows at the sentinel index are walked
			for (let i = 0; i < CHART_ROWS.length; i++) {
				if (i === CHART_SENTINEL && (def.volatiles.foresight || attAb === 'scrappy')) break;
				const row = CHART_ROWS[i];
				if (row[0] !== atkIdx) continue;
				if (!rowAppliesActive(def, defItemEffect, gravity, row)) continue;
				if (row[1] === defIdx1) {
					scaled = (scaled * row[2] / 10) | 0;
					if (movePower) mask = typeMulMask(row[2], mask);
				}
				if (row[1] === defIdx2 && defIdx2 !== defIdx1) {
					scaled = (scaled * row[2] / 10) | 0;
					if (movePower) mask = typeMulMask(row[2], mask);
				}
			}
		}

		// post-chart, exact decomp if / else-if / else
		if (this.ignorable(att, def, 'wonderguard') && this.onDamagingTurn(att, move.id)
			&& ((mask & SE) === 0 || (mask & BASIC_EFFECTIVENESS) === BASIC_EFFECTIVENESS)
			&& movePower) {
			mask |= WONDER_GUARD;
		} else if (!opts.ignoreType && !opts.ignoreImmunities) {
			if ((mask & SE) && movePower) {
				// decomp: Filter or Solid Rock halves SE
				if (this.ignorable(att, def, 'filter') || this.ignorable(att, def, 'solidrock'))
					scaled = decompDivide(scaled * 3, 4);
				if (attItemEffect === 'powerupse')
					scaled = (scaled * (100 + attItemPower) / 100) | 0;
			}
			// Tinted Lens (decomp: NVE ×2) is absent from this fork — omitted
		} else {
			mask &= ~(SE | NVE);
		}

		// decomp class map (AICmd_CalcMaxEffectiveness): 120→80, 240→160, 30→20,
		// 15→10; anything immune → 0. 320 (Adaptability double-SE) passes
		// unmapped — decomp behavior, ported.
		let cls = scaled;
		if (cls === 120) cls = 80;
		else if (cls === 240) cls = 160;
		else if (cls === 30) cls = 20;
		else if (cls === 15) cls = 10;
		if (mask & IMMUNE) cls = 0;

		return { mask, scaled, class: cls, type };
	}

	// decide() — the entry the daemon/smoke calls per request. Incremental
	// replacement: forced switch → PostKOSwitchIn, voluntary switch →
	// shouldSwitch, item → shouldUseItem, move → module scoring.
	//
	// Fork request shapes (battle.js getRequests):
	//   switch : { forceSwitch: bool[] per active, side }            — no active/wait
	//   preview: { teamPreview: true, maxChosenTeamSize?, side }
	//   move   : { active: [moveRequestData], side }
	//   wait   : { wait: true, side }                               — "opponent still choosing"
	// `side` is an OBJECT: {name, id: 'p1'|'p2', pokemon[]}. The pokemon[] order is
	// the CURRENT side.pokemon order — the fork reorders it in place on switch-in
	// (active slot first; pokemon.position tracks the index) — so `switch N`
	// choices index that order, and live `.fainted` can lag the faint queue:
	// the request `condition` ('0 fnt' / 'N/N') is ground truth for fainted state.
	decide(req) {
		const b = this.battle;
		if (req.wait) return null;
		const myIdx = (req.side && req.side.id === 'p2') ? 1 : 0;
		const side = b.sides[myIdx];
		if (!side) return null;
		this.origTeam();
		if (req.teamPreview) {
			const n = side.pokemon.length;
			if (!n) return null;
			const slots = [];
			for (let i = 1; i <= n; i++) slots.push(i);
			return 'team ' + (n > 9 ? slots.join(', ') : slots.join(''));
		}
		if (req.forceSwitch && req.forceSwitch[0]) {
			const slot = this.postKOSwitchIn(myIdx);
			if (slot < 6) {
				// orig team slot → current side.pokemon order (the array reorders
				// on switch-in; the choice indexes that order)
				const cur = side.pokemon.indexOf(this.origTeam()[myIdx === 0 ? 'p1' : 'p2'][slot]);
				if (cur >= 0) return `switch ${cur + 1}`;
			}
			return 'default';
		}
		const live = side.active[0];
		if (!live || live.fainted) return null;
		const foe = b.sides[1 - myIdx].active[0];
		if (!foe) return null;

		// PickCommand (trainer_ai.c:3989): the voluntary switch decision
		// precedes items and move choice. Slot 6 = "run the post-KO logic
		// instead"; if that finds nothing either, the first alive bench
		// mon in party order.
		const slot = this.shouldSwitch(myIdx);
		if (slot >= 0) {
			let s = slot;
			if (s === 6) {
				s = this.postKOSwitchIn(myIdx);
				if (s === 6) {
					const party = this.origTeam()[myIdx === 0 ? 'p1' : 'p2'];
					s = party.findIndex((m) => m && !m.fainted && m !== live);
				}
			}
			if (s >= 0) {
				const cur = side.pokemon.indexOf(this.origTeam()[myIdx === 0 ? 'p1' : 'p2'][s]);
				if (cur >= 0) return `switch ${cur + 1}`;
			}
			return 'default';
		}
		// (2) Items (decomp PickCommand 4032-4040): a declined voluntary switch
		// falls to the item check before move choice. The fork has no bag
		// action — the effect is applied directly; the zeroed bag slot persists.
		if (this.shouldUseItem(myIdx)) this.executeUsedItem(myIdx);
		let best = -1, bestSlot = -1;
		for (let i = 0; i < live.moveSlots.length; i++) {
			const ms = live.moveSlots[i];
			if (!ms || ms.pp === 0 || ms.disabled) continue;
			const n = this.dmg(live, foe, ms.id);
			if (n > best) { best = n; bestSlot = i; }
		}
		return bestSlot < 0 ? 'default' : `move ${bestSlot + 1}`;
	}

	// decomp BattleAI_PostKOSwitchIn (battle_lib.c:7923-8089): the side's
	// active just fainted (singles) — pick the incoming mon. Returns the
	// ORIGINAL team slot (0..n-1) or 6 = none.
	//
	// Stage 1: repeatedly — the highest raw type-matchup score wins (u8 wrap —
	// the documented Post-KO Scoring Overflow; mono types score twice; ties
	// keep the earlier party slot) but only if the pick owns an SE move
	// against the defender (switch variant; NO pp check — decomp). SE-less
	// picks are permanently disregarded; the scan repeats until 0x3F.
	// Stage 2: re-score EVERY alive pick (the disregard mask is NOT consulted);
	// per move: decomp power != 1 (fork: category Status) → score = fork dmg()
	// with the fainted battler as the decomp's attacker (fork getDamage accepts
	// a fainted source — probed; the fork's damage subsumes the decomp's
	// CalcMoveDamage+ApplyTypeChart, documented deviation). `score` is
	// function-scoped and never reset per mon/move — a status or missing move
	// leaves the previous value to the comparison (decomp quirk; the
	// uninitialized-C case is unreachable: any stage-2-eligible mon was
	// scored in stage 1 pass 1) — and u8-wraps (bug). Ties → earlier party
	// slot.
	postKOSwitchIn(myIdx) {
		const b = this.battle;
		const side = b.sides[myIdx];
		const foe = b.sides[1 - myIdx].active[0];
		const active = side ? side.active[0] : null;
		if (!side || !active || !foe || foe.fainted) return 6;
		const party = this.origTeam()[myIdx === 0 ? 'p1' : 'p2'];
		const partySize = party.length;
		const elig = (p) => !!p && !p.fainted && p !== active;

		const dTypes = foe.getTypes().map(normType);
		const d1 = TYPE_IDX[dTypes[0]];
		const d2 = dTypes.length > 1 ? TYPE_IDX[dTypes[1]] : d1; // mono: type2 = type1 (decomp storage)

		// u8 in the decomp — one `score` shared by BOTH stages
		let score = 0, maxScore = 0, picked = 6, disregarded = 0;

		while (disregarded !== 0x3F) {
			maxScore = 0;
			picked = 6;
			for (let i = 0; i < partySize; i++) {
				const p = party[i];
				if (!elig(p) || (disregarded & (1 << i))) {
					disregarded |= 1 << i;
					continue;
				}
				const t = p.getTypes().map(normType);
				const m1 = TYPE_IDX[t[0]];
				const m2 = t.length > 1 ? TYPE_IDX[t[1]] : m1; // mono types score twice (decomp)
				score = typeMatchup40(m1, d1, d2);
				score = (score + typeMatchup40(m2, d1, d2)) & 0xFF; // u8 wrap (bug)
				if (maxScore < score) {
					maxScore = score;
					picked = i;
				}
			}
			if (picked !== 6) {
				const p = party[picked];
				let hasSE = false;
				for (let j = 0; j < 4; j++) {
					const ms = p.moveSlots[j];
					if (!ms || !ms.id) continue; // decomp: `if (move)` only — NO pp check
					if (this.eff(p, foe, ms.id, 'switch').mask & SE) {
						hasSE = true;
						break;
					}
				}
				if (hasSE) return picked;
				disregarded |= 1 << picked;
			} else {
				disregarded = 0x3F;
			}
		}

		maxScore = 0;
		picked = 6;
		for (let i = 0; i < partySize; i++) {
			const p = party[i];
			if (!elig(p)) continue;
			for (let j = 0; j < 4; j++) {
				const ms = p.moveSlots[j];
				if (ms && ms.id) {
					const mv = this.dex.moves.get(String(ms.id).toLowerCase());
					if (!mv || mv.category !== 'Status')
						score = this.dmg(active, foe, ms.id, { allowFainted: true }) & 0xFF;
				}
				if (maxScore < score) {
					maxScore = score;
					picked = i;
				}
			}
		}
		return picked;
	}

	// decomp MON_DATA_ABILITY: as stored — no suppression, no
	// gravity/ingrain Levitate nulling (a bench mon's raw ability).
	rawAbilityOf(p) {
		return p.ability ? String(p.ability).toLowerCase().replace(/[^a-z0-9]/g, '') : '';
	}

	// The decomp's party index is the ORIGINAL team slot (side.pokemon
	// reorders on switch-in). Snapshot on first use: the first request
	// (team preview) precedes any reorder, and both teams are registered
	// by then.
	origTeam() {
		const b = this.battle;
		if (!this.origParty)
			this.origParty = {
				p1: b.sides[0] ? b.sides[0].pokemon.slice() : [],
				p2: b.sides[1] ? b.sides[1].pokemon.slice() : [],
			};
		return this.origParty;
	}

	// The decomp's party index is the ORIGINAL team slot (side.pokemon
	// reorders on switch-in); the bench is the team as first seen.
	bench(myIdx) {
		const party = this.origTeam()[myIdx === 0 ? 'p1' : 'p2'];
		const p = this.battle.sides[myIdx].active[0];
		return { party, activeSlot: p ? party.indexOf(p) : -1 };
	}

	// decomp BattleAI_ShouldSwitch (trainer_ai.c:3894-3987): the voluntary
	// switch decision, checked before items and move choice (PickCommand).
	// Returns the original team slot to switch to, 6 = "run the post-KO
	// logic instead", or -1 = stay.
	//
	// The decomp's three CountAbility guards all count the FOE side
	// (THEIR_SIDE / EXCEPT_ME) — in singles, its one active. The decomp
	// comment admits the naivety: no Shed Shell, no
	// Flying-vs-Arena-Trap, no own-Shadow-Tag exemption — ported as is.
	shouldSwitch(myIdx) {
		const side = this.battle.sides[myIdx];
		const p = side && side.active[0];
		if (!p || p.fainted) return -1;
		const foe = this.battle.sides[1 - myIdx].active[0];
		if (!foe) return -1;

		// 3906-3912: cannot switch
		if (p.volatiles.trapped || p.volatiles.ingrain
			|| this.abilityOf(foe) === 'shadowtag'
			|| this.abilityOf(foe) === 'arenatrap'
			|| (this.abilityOf(foe) === 'magnetpull'
				&& p.getTypes().map(normType).includes('steel'))) {
			return -1;
		}

		const { party, activeSlot } = this.bench(myIdx);

		// 3915-3938: need an alive bench (active excluded; GetPartner is
		// the same battler in singles and no aiSwitched slot is set at a
		// fresh decision)
		let alive = 0;
		for (let i = 0; i < party.length; i++)
			if (party[i] && !party[i].fainted && i !== activeSlot) alive++;
		if (!alive) return -1;

		// 3941-3983, exact order
		const s1 = this.aiPerishSongKO(p);
		if (s1 >= 0) return s1;
		const s2 = this.aiCannotDamageWonderGuard(myIdx, p, foe);
		if (s2 >= 0) return s2;
		const s3 = this.aiOnlyIneffectiveMoves(myIdx, p, foe);
		if (s3 >= 0) return s3;
		const s4 = this.aiHasAbsorbAbilityInParty(myIdx, p, foe);
		if (s4 >= 0) return s4;
		const s5 = this.aiIsAsleepWithNaturalCure(myIdx, p, foe);
		if (s5 >= 0) return s5;
		if (this.aiHasSuperEffectiveMove(p, foe, false)) return -1;
		if (this.aiIsHeavilyStatBoosted(p)) return -1;
		const s8 = this.aiHasPartyMemberWithSE(myIdx, p, foe, INEFF, 2);
		if (s8 >= 0) return s8;
		return this.aiHasPartyMemberWithSE(myIdx, p, foe, NVE, 3);
	}

	// 3263-3272: PERISH_SONG mask with a zero counter → the post-KO
	// fallback. Dead code in decomp and fork alike: the counter hits 0 at
	// turn end and the mon faints before the next check, so the condition
	// is unobservable.
	aiPerishSongKO(p) {
		const ps = p.volatiles.perishsong;
		return (ps && ps.layer <= 0) ? 6 : -1;
	}

	// 3286-3350: the foe's Wonder Guard blocks every non-SE move. If none
	// of ours is SE, switch (66%) to the first bench mon that owns one.
	aiCannotDamageWonderGuard(myIdx, p, foe) {
		if (this.abilityOf(foe) !== 'wonderguard') return -1;
		for (let j = 0; j < 4; j++) {
			const ms = p.moveSlots[j];
			if (ms && ms.id && this.eff(p, foe, ms.id).mask & SE) return -1;
		}
		const { party, activeSlot } = this.bench(myIdx);
		for (let i = 0; i < party.length; i++) {
			const m = party[i];
			if (!m || m.fainted || i === activeSlot) continue;
			for (let j = 0; j < 4; j++) {
				const ms = m.moveSlots[j];
				if (!ms || !ms.id) continue;
				if (this.eff(m, foe, ms.id, 'switch').mask & SE
					&& this.randNext() % 3 < 2) return i;
			}
		}
		return -1;
	}

	// 3361-3546: all of our damaging moves are ineffective (the decomp
	// runs each move against BOTH defender slots — the same foe in
	// singles — and a fainted foe leaves effectiveness at 0) → switch to
	// a bench mon with a better matchup. numMoves < 2 never triggers
	// (quirk). The bench is walked twice: first for an SE move (66%, two
	// rolls per move), then for a move with NO effectiveness flags at all
	// (50%, two rolls per move).
	aiOnlyIneffectiveMoves(myIdx, p, foe) {
		if (foe.fainted) return -1;
		let numMoves = 0;
		for (let j = 0; j < 4; j++) {
			const ms = p.moveSlots[j];
			if (!ms || !ms.id) continue;
			const mv = this.dex.moves.get(String(ms.id).toLowerCase());
			if (!mv || !mv.basePower) continue; // decomp: MOVE_DATA.power != 0
			numMoves++;
			if (!(this.eff(p, foe, ms.id).mask & INEFF)) return -1;
		}
		if (numMoves < 2) return -1;
		const { party, activeSlot } = this.bench(myIdx);
		for (let i = 0; i < party.length; i++) {
			const m = party[i];
			if (!m || m.fainted || i === activeSlot) continue;
			for (let j = 0; j < 4; j++) {
				const ms = m.moveSlots[j];
				if (!ms || !ms.id) continue;
				const mv = this.dex.moves.get(String(ms.id).toLowerCase());
				if (!mv || !mv.basePower) continue;
				for (let d = 0; d < 2; d++) { // the decomp's two defender slots
					if (this.eff(m, foe, ms.id, 'switch').mask & SE
						&& this.randNext() % 3 < 2) return i;
				}
			}
		}
		for (let i = 0; i < party.length; i++) {
			const m = party[i];
			if (!m || m.fainted || i === activeSlot) continue;
			for (let j = 0; j < 4; j++) {
				const ms = m.moveSlots[j];
				if (!ms || !ms.id) continue;
				const mv = this.dex.moves.get(String(ms.id).toLowerCase());
				if (!mv || !mv.basePower) continue;
				for (let d = 0; d < 2; d++) {
					if (this.eff(m, foe, ms.id, 'switch').mask === 0
						&& this.randNext() % 2 === 0) return i;
				}
			}
		}
		return -1;
	}

	// 3560-3624: do we hold a super-effective move? flag=TRUE → any of
	// them (used by the absorb check); FALSE → 90% per SE move. The
	// decomp skips the whole check while the defender is mid-switch
	// (battlersSwitchingMask) — at a request boundary no switch is in
	// progress, so it always proceeds.
	aiHasSuperEffectiveMove(p, foe, flag) {
		if (foe.fainted) return false;
		for (let j = 0; j < 4; j++) {
			const ms = p.moveSlots[j];
			if (!ms || !ms.id) continue;
			if (this.eff(p, foe, ms.id).mask & SE) {
				if (flag) return true;
				if (this.randNext() % 10 != 0) return true; // 90%
			}
		}
		return false;
	}

	// 3640-3714: the last hit on us was Fire / Water / Electric and a
	// bench mon owns the matching absorb ability → switch to it (50%) —
	// but only ~33% of the time if we ourselves hold an SE move, and
	// never if we hold that ability. The decomp uses the move's TABLE
	// type here, not the resolved variable type (Natural Gift, Judgment,
	// Hidden Power, Weather Ball read as their dex types).
	aiHasAbsorbAbilityInParty(myIdx, p, foe) {
		if (this.aiHasSuperEffectiveMove(p, foe, true) && this.randNext() % 3 != 0) return -1;
		const hit = p.getLastAttackedBy();
		if (!hit) return -1;
		const mv = this.dex.moves.get(hit.move);
		if (!mv || !mv.basePower) return -1;
		const t = normType(mv.type);
		let check;
		if (t === 'fire') check = 'flashfire';
		else if (t === 'water') check = 'waterabsorb';
		else if (t === 'electric') check = 'voltabsorb';
		else return -1;
		if (this.abilityOf(p) === check) return -1;
		const { party, activeSlot } = this.bench(myIdx);
		for (let i = 0; i < party.length; i++) {
			const m = party[i];
			if (!m || m.fainted || i === activeSlot) continue;
			if (this.rawAbilityOf(m) === check && (this.randNext() & 1)) return i;
		}
		return -1;
	}

	// 3817-3859: asleep, Natural Cure, and above half HP → the cure is
	// coming; rotate. Slot 6 = the post-KO fallback. The decomp's "50%"
	// comments on the two inner calls don't match their rand=1 (no roll)
	// — the code is ported.
	aiIsAsleepWithNaturalCure(myIdx, p, foe) {
		if (p.status !== 'slp' || this.abilityOf(p) !== 'naturalcure'
			|| p.hp < ((p.maxhp / 2) | 0)) return -1;
		const hit = p.getLastAttackedBy();
		const power = hit ? ((this.dex.moves.get(hit.move) || {}).basePower || 0) : 0;
		if (!hit && (this.randNext() & 1)) return 6;
		if (power === 0 && (this.randNext() & 1)) return 6;
		const s = this.aiHasPartyMemberWithSE(myIdx, p, foe, INEFF, 1);
		if (s >= 0) return s;
		const s2 = this.aiHasPartyMemberWithSE(myIdx, p, foe, NVE, 1);
		if (s2 >= 0) return s2;
		if (this.randNext() & 1) return 6;
		return -1;
	}

	// 3872-3884: four or more positive stat stages total → stay. The
	// decomp stores stages 0..12 (6 = neutral) and sums all 8 stats (HP
	// never boosts); the fork's p.boosts are stage deltas over 7.
	aiIsHeavilyStatBoosted(p) {
		let n = 0;
		for (let i = 1; i < STAT_KEYS.length; i++) {
			const s = p.boosts[STAT_KEYS[i]];
			if (s > 0) n += s;
		}
		return n >= 4;
	}

	// 3727-3807: a bench mon that is (check) against the move that last
	// hit us — immune (0x8) or resists (0x4) — and owns an SE move of its
	// own. The decomp's comments claim 33% / 25%; the code rolls
	// RandNext % rand == 0 = 50% / 33% — the code is ported. The
	// attacker is resolved through its BATTLER SLOT (moveHitBattler):
	// after a foe switch it is the slot's current occupant, not the mon
	// that hit — ported: the current foe active. The defender-slot item
	// in the inner call is the attacker's own live item effect (3791),
	// which for our foe is simply its item.
	aiHasPartyMemberWithSE(myIdx, p, foe, check, rand) {
		const hit = p.getLastAttackedBy();
		if (!hit || !foe || foe.fainted) return -1;
		const mv = this.dex.moves.get(hit.move);
		if (!mv || !mv.basePower) return -1;
		const { party, activeSlot } = this.bench(myIdx);
		for (let i = 0; i < party.length; i++) {
			const m = party[i];
			if (!m || m.fainted || i === activeSlot) continue;
			if (this.eff(foe, m, hit.move, 'switch', { rawDef: true }).mask & check) {
				for (let j = 0; j < 4; j++) {
					const ms = m.moveSlots[j];
					if (!ms || !ms.id) continue;
					if (this.eff(m, foe, ms.id, 'switch').mask & SE
						&& this.randNext() % rand === 0) return i;
				}
			}
		}
		return -1;
	}
	// decomp TrainerAI_ShouldUseItem (trainer_ai.c:4056-4199): does the AI use a
	// bag item this turn? On true the caller runs executeUsedItem().
	//
	// Ported quirks (all deliberate):
	//   - a slot is examined while `i == 0 || aliveMons <= initialCount - i + 1`
	//     (initialCount = trainerItemCounts = the INITIAL non-empty slot count);
	//     a NONE slot `continue`s BEFORE the chain and is never zeroed;
	//   - there is NO break after a match: once `result` is true, every
	//     subsequently examined non-NONE slot is zeroed and `usedItem` repointed
	//     at it, while usedItemType/usedItemCondition can be clobbered by a
	//     non-matching slot (the final unrecognized else writes MAX);
	//   - the decomp fakeOut guard ("only the turn a mon entered play") has no
	//     fork field at a request boundary (newlySwitched is vestigial;
	//     activeTurns is already >= 1 by the first move request), so the engine
	//     tracks entry turns itself via this.lastActive — see the inline note;
	//   - the GUARD_SPEC Mist check is hard-coded to sideConditionsMask[1]: the AI
	//     occupies the decomp's player-side battlers, so side 1 is the
	//     OPPONENT's side (the fork's sides[0] when we are p2). The fork has no
	//     Mist side condition, so the guard passes.
	//   - the AI-partner guard (4070) does not apply: we are the trainer.
	shouldUseItem(myIdx) {
		const b = this.battle;
		const p = b.sides[myIdx].active[0];
		this.usedItemCondition = 0; // decomp per-call reset (4066)
		if (!p || p.fainted) return false;
		if (p.volatiles.embargo) return false; // 4076
		// fakeOut proxy (decomp: only the turn a mon entered play): the fork has
		// no per-switch entry-turn stamp at a request boundary — newlySwitched is
		// vestigial and activeTurns is already >= 1 by the first move request (the
		// fork's preview phase runs an endTurn). The decomp guard is a pure
		// function of the turn, so the engine tracks it: the active mon is new to
		// the side (never seen, or changed since the last decision) => entry turn.
		const entryTurn = !this.lastActive[myIdx] || this.lastActive[myIdx] !== p;
		this.lastActive[myIdx] = p;
		let result = false;
		const side = b.sides[myIdx];
		let aliveMons = 0;
		for (const m of side.pokemon) if (!m.fainted && m.maxhp > 0) aliveMons++;
		const initialCount = this.initialItems.filter((i) => i).length; // trainerItemCounts
		for (let i = 0; i < 4; i++) {
			if (i !== 0 && aliveMons > initialCount - i + 1) continue; // 4092
			const id = this.initialItems[i];
			if (!id) continue; // ITEM_NONE
			const rec = ITEMS[id];
			const cures = rec && rec.cures;
			const hasCure = (c) => !!cures && cures.includes(c);
			if (rec && rec.category === 'FULL_RESTORE') { // 4099, by item id
				if (p.hp < (p.maxhp >> 2) && p.hp) { this.usedItemType = 'FULL_RESTORE'; result = true; }
			} else if (rec && rec.category === 'RECOVER_HP' && rec.hp) { // 4105-4117
				if (p.hp && (p.hp < (p.maxhp >> 2) || (p.maxhp - p.hp) > rec.hp)) {
					this.usedItemType = 'RECOVER_HP';
					result = true;
				}
			} else if (hasCure('slp') && p.status === 'slp') {
				this.usedItemCondition |= CURE_BIT.slp; this.usedItemType = 'RECOVER_STATUS'; result = true;
			} else if (hasCure('psn') && (p.status === 'psn' || p.status === 'tox')) {
				this.usedItemCondition |= CURE_BIT.psn; this.usedItemType = 'RECOVER_STATUS'; result = true;
			} else if (hasCure('brn') && p.status === 'brn') {
				this.usedItemCondition |= CURE_BIT.brn; this.usedItemType = 'RECOVER_STATUS'; result = true;
			} else if (hasCure('frz') && p.status === 'frz') {
				this.usedItemCondition |= CURE_BIT.frz; this.usedItemType = 'RECOVER_STATUS'; result = true;
			} else if (hasCure('par') && p.status === 'par') {
				this.usedItemCondition |= CURE_BIT.par; this.usedItemType = 'RECOVER_STATUS'; result = true;
			} else if (hasCure('confusion') && p.volatiles.confusion) {
				this.usedItemCondition |= CURE_BIT.confusion; this.usedItemType = 'RECOVER_STATUS'; result = true;
			} else if (entryTurn) { // fakeOut guard: entry turn only
				// decomp chain order: ATK, DEF, SPATK, SPDEF, SPEED, ACC
				const st = rec && rec.stages;
				if (st && st.atk) { this.usedItemCondition = BOOST_STAT_IDX.atk; this.usedItemType = 'STAT_BOOSTER'; result = true; }
				else if (st && st.def) { this.usedItemCondition = BOOST_STAT_IDX.def; this.usedItemType = 'STAT_BOOSTER'; result = true; }
				else if (st && st.spatk) { this.usedItemCondition = BOOST_STAT_IDX.spatk; this.usedItemType = 'STAT_BOOSTER'; result = true; }
				else if (st && st.spdef) { this.usedItemCondition = BOOST_STAT_IDX.spdef; this.usedItemType = 'STAT_BOOSTER'; result = true; }
				else if (st && st.speed) { this.usedItemCondition = BOOST_STAT_IDX.speed; this.usedItemType = 'STAT_BOOSTER'; result = true; }
				else if (st && st.acc) { this.usedItemCondition = BOOST_STAT_IDX.acc; this.usedItemType = 'STAT_BOOSTER'; result = true; }
				else if (rec && rec.guardSpec && !b.sides[1 - myIdx].getSideCondition('mist')) {
					this.usedItemType = 'GUARD_SPEC'; result = true;
				}
				// decomp has no inner final else: an entry-turn mon holding an
				// unrecognized item does nothing (result unchanged)
			} else {
				this.usedItemType = 'MAX'; // 4186, unrecognized item type
			}
			// 4191-4193: no break — every examined non-NONE slot after a match
			// is zeroed and usedItem repointed at it (last examined slot wins).
			if (result) { this.usedItem = id; this.initialItems[i] = ''; }
		}
		return result;
	}

	// The fork has no consumable bag action: apply the standard gen4 effect of
	// the chosen item directly to the live Pokemon, with one transcript line.
	// The decomp's effect scripts are GBA script subscripts (the controller at
	// battle_controller_player.c:1905-1975 only carries the message plumbing) —
	// the fork effect is the documented execution deviation; the decision in
	// shouldUseItem above is the faithful part.
	executeUsedItem(myIdx) {
		const b = this.battle;
		const p = b.sides[myIdx].active[0];
		const id = this.usedItem;
		if (!p || !id) return;
		b.add('-item', p, (b.dex.items.get(id) || {}).name || id);
		const rec = ITEMS[id];
		switch (this.usedItemType) {
		case 'FULL_RESTORE':
			p.heal(p.maxhp - p.hp); // fork heal(undefined) is a no-op (trunc NaN)
			p.cureStatus();
			break;
		case 'RECOVER_HP':
			if (rec && rec.hp) p.heal(rec.hp);
			break;
		case 'RECOVER_STATUS':
			if ((this.usedItemCondition & CURE_BIT.confusion) && p.volatiles.confusion) {
				p.removeVolatile('confusion');
			}
			{
				const match = { 32: 'slp', 16: 'psn', 8: 'brn', 4: 'frz', 2: 'par' }[this.usedItemCondition];
				if (p.status && (p.status === match || (match === 'psn' && p.status === 'tox'))) p.cureStatus();
			}
			break;
		case 'STAT_BOOSTER':
			{
				const key = STAT_KEYS[this.usedItemCondition];
				const n = rec && rec.stages ? rec.stages[STAGES_KEYS[key]] : 0;
				if (key && n) b.boost({ [key]: n }, p);
			}
			break;
		case 'GUARD_SPEC':
			break; // no Mist in the fork — consume only
		}
	}
}

module.exports = {
	Engine,
	s8,
	SE, NVE, INEFF,
	LEVITATED, WONDER_GUARD, MAGNET_RISE, IMMUNE,
	BASIC_EFFECTIVENESS,
	STAT_KEYS,
	normType,
	TYPE_IDX,
	decompDivide,
};
