'use strict';
// ============================================================================
// AI_FLAG_EXPERT (bit 2) — faithful port of Expert_Main (pokeplatinum
// src/battle/trainer_ai/script.s:1623-6349): the move-effect jump table +
// the expert routine bodies. Code wins over comments everywhere; decomp
// quirks are preserved deliberately. Each decomp routine is one JS function;
// PopOrEnd = return from the routine (next slot/flag continues); decomp
// labels are mirrored as local closures. Exactly one ctx.aiRand(256) call is
// made where IfRandom* would consume a randNext, in script order.
//
// EXPERT-DEV (fork deviations, all forced by engine/fork data; also reported):
//  1  speed compares: engine.speedRank() stand-in (rank order; ties follow
//     side order — decomp CompareBattlerSpeed ties are coin-tossed and NEVER
//     SLOWER). Consumes no PRNG.
//  2  DEEP_FOG unreachable: no fog weather in this fork.
//  3  BATTLE_TYPE_DOUBLES never set (singles protocol) → Protect doubles +2
//     and TrickRoom doubles-skip are inactive; IfTargetIsPartner Terminate
//     (script.s:1624) can never fire (ctx.foeActive is always the enemy side).
//  4  LoadProtectChain approximated: chain 1 iff the pokemon's last used move
//     was Protect/Detect/Endure; consecutive-turn count tracked in a WeakMap.
//  5  LoadRecycleItem approximated: last item observed lost from that pokemon
//     (prev item non-empty → empty since last decide).
//  6  IfCanUseLastResort approximated: distinct moves seen used (lastUsedOf
//     sampled each decide) >= knownMoves-1 && knownMoves > 1.
//  7  MOVE_NONE: LoadDefenderLastUsedMoveClass with no previous move yields
//     'STATUS' (decomp MOVE_DATA(0).class unreadable — see report);
//     LoadEffectOfLoadedMove yields 'NONE' (decomp BATTLE_EFFECT_NONE = 0).
//  8  last-used move is per-mon in the fork (decomp movePrevByBattler is
//     per-slot and survives a target switch — stale-read divergence).
//  9  IfPartyMemberDealsMoreDamage: bench members scored with their OWN stats
//     (decomp passes the active's battleMons stats with the bench member's
//     moves/item/ability/ivs — documented in NOTES).
// 10  varyDamage flags are identical in the fork: getDamage has no variance
//     roll (decomp USE_RANDOM_DAMAGE used a stored 85..100 roll; engine
//     precedent pins fixed 100).
// 11  Assurance recoil-berry table: decomp compares the loaded HELD-ITEM
//     EFFECT against ITEM_JABOCA/ROWAP constants (never equal) — dead branch,
//     ported dead.
// 12  Lock-On "always hits" volatile: no lockon/futuresearch volatile exists
//     in this fork's data → those checks read false (Protect/Feint).
// 13  Yawn/Perish Song/Curse/Attract volatiles exist under fork ids below.
// 14  UTurn "last party member" branch jumps to End with NO score change
//     (script.s:5101; the task-2 doc prose claimed +2 — code wins).
// 15  LoadTurnCount = battle turn - 1 (decomp totalTurns increments at turn
//     end; first decision = 0); LoadBattlerTurnCount(p) = turn - entryTurn(p)
//     + 1 (NOTES-pinned fork idiom).
// ============================================================================

// decomp AddToMoveScore: s8 wrap, saturating at 0 below
function add(ctx, v) {
	let s = ctx.s8(ctx.scores[ctx.slot] + v);
	if (s < 0) s = 0;
	ctx.scores[ctx.slot] = s;
}
// IfRandomLessThan n → true = jump taken (exactly one randNext)
function rnd(ctx, n) { return ctx.aiRand(256) < n; }

const E = (s) => 'BATTLE_EFFECT_' + s;
// --- battler views ---------------------------------------------------------
const hpA = (ctx) => (ctx.active.hp * 100 / ctx.active.maxhp) | 0;
const hpD = (ctx) => (ctx.foeActive.hp * 100 / ctx.foeActive.maxhp) | 0;
// internal stat stage (0..12, 6 = +0)
function stg(ctx, who, stat) {
	const p = who === 'atk' ? ctx.active : ctx.foeActive;
	return ((p.boosts && p.boosts[stat]) || 0) + 6;
}
function typesOf(ctx, p) {
	const t = p.getTypes().map((x) => String(x).toLowerCase());
	return [t[0] || '', t[1] || ''];
}
function wx(ctx) {
	switch (ctx.weather) {
		case 'raindance': return 'RAINING';
		case 'sunnyday': return 'SUNNY';
		case 'sandstorm': return 'SANDSTORM';
		case 'hail': return 'HAILING';
		default: return 'CLEAR';
	}
}
// status conditions (decomp MON_CONDITION masks)
const SLEEP = (p) => p.status === 'slp';
const TOXIC = (p) => p.status === 'tox';
const ANYSTATUS = (p) => !!p.status;
const STATUS_MATCH = {
	MON_CONDITION_ANY: ANYSTATUS, MON_CONDITION_SLEEP: SLEEP, MON_CONDITION_TOXIC: TOXIC,
	MON_CONDITION_PARALYSIS: (p) => p.status === 'par',
	STATUS_SLEEP: SLEEP, STATUS_BADLY_POISON: TOXIC, STATUS_PARALYZE: (p) => p.status === 'par',
};
function hasStatus(ctx, who, cond) {
	return STATUS_MATCH[cond](who === 'atk' ? ctx.active : ctx.foeActive);
}
// volatiles
const VOL = {
	CURSE: 'curse', ATTRACT: 'attract', CONFUSION: 'confusion', SUBSTITUTE: 'substitute',
	FOCUS_ENERGY: 'focusenergy',
};
const MEFF = {
	LEECH_SEED: 'leechseed', PERISH_SONG: 'perishsong', YAWN: 'yawn',
	AQUA_RING: 'aquaring', INGRAIN: 'ingrain', MAGNET_RISE: 'magnetrise',
};
function hasVolatile(ctx, who, v) {
	const p = who === 'atk' ? ctx.active : ctx.foeActive;
	return !!(p.volatiles && p.volatiles[VOL[v] || String(v).toLowerCase()]);
}
function hasMoveEffect(ctx, who, m) {
	const p = who === 'atk' ? ctx.active : ctx.foeActive;
	if (m === 'LOCK_ON') return !!(p.volatiles && (p.volatiles.lockon || p.volatiles.futuresearch)); // EXPERT-DEV 12
	return !!(p.volatiles && p.volatiles[MEFF[m] || String(m).toLowerCase()]);
}
const taunted = (p) => !!(p.volatiles && (p.volatiles.taunt || p.volatiles.torque));
const disabledFoe = (ctx) => ctx.foeMoves.some((ms) => ms && ms.disabled);
function sideCond(ctx, who, c) {
	const s = who === 'atk' ? ctx.side : ctx.foe;
	const id = { SPIKES: 'spikes', STEALTH_ROCK: 'stealthrock', TOXIC_SPIKES: 'toxicspikes',
		SAFEGUARD: 'safeguard', REFLECT: 'reflect', LIGHT_SCREEN: 'lightscreen' }[c] || String(c).toLowerCase();
	return !!(s.sideConditions && s.sideConditions[id]);
}
// speed compares (EXPERT-DEV 1)
function speedCmp(ctx) {
	const r = ctx.engine.speedRank();
	const a = r.indexOf(ctx.active), f = r.indexOf(ctx.foeActive);
	return { faster: a < f, slower: a > f };
}
const faster = (ctx) => speedCmp(ctx).faster;
const slower = (ctx) => speedCmp(ctx).slower;
// EXPERT-DEV 16 (observer gap): observer.sync() parses battle.log lines with
// split('|') but every line starts with '|', so its switch/move cases never
// fire — entryTurn and observedMoves stay empty all battle (reported to Main).
// Faithful module-local re-implementation of the pinned observer semantics:
// entryTurn = fork turn at first sight of the |switch| (lead → turn 1),
// observedMoves = deduped ≤4 move names per side (RecordLastMove equivalent),
// |drag| treated as a switch (decomp parity).
const logScan = new WeakMap(); // battle -> { idx, turn, entry, obs }
function scan(ctx) {
	const b = ctx.battle;
	let st = logScan.get(b);
	if (!st) { st = { idx: 0, entry: [[0, 0], [0, 0]], obs: [[], []] }; logScan.set(b, st); }
	if (st.idx >= b.log.length) return st;
	for (let i = st.idx; i < b.log.length; i++) {
		const parts = String(b.log[i]).slice(1).split('|');
		if (parts[0] !== 'switch' && parts[0] !== 'replace' && parts[0] !== 'drag' && parts[0] !== 'move') continue;
		const m = /^p([12])([a-z])(\d*)/.exec(parts[1] || '');
		if (!m) continue;
		const s = m[1] === '2' ? 1 : 0;
		const slot = m[2].charCodeAt(0) - 97;
		if (parts[0] === 'move') {
			const name = parts[2];
			const list = st.obs[s];
			if (!name || list.includes(name) || list.length >= 4) continue; // RecordLastMove dedup
			list.push(name);
		} else {
			if (slot > 0) continue; // singles: engine contract exposes slot 0 only
			st.entry[s][slot] = ctx.turn; // turn of first observation
			st.obs[s] = [];
		}
	}
	st.idx = b.log.length;
	return st;
}
let scanSt = null; // state for the current decide()
function sideIdxOf(ctx, p) { return p.side === ctx.battle.sides[1] ? 1 : 0; }
// knowledge (decomp IfMove*Known): attacker = own 4 slots (pp irrelevant),
// defender = observed side moves (decomp battlerMoves)
function knows(ctx, who, moveId) {
	if (who === 'atk') return ctx.moves.some((ms) => ms && ms.id === moveId);
	return foeIds(ctx).includes(moveId);
}
let foeIdsCache = { foe: null, turn: -1, list: null };
function foeIds(ctx) {
	if (foeIdsCache.foe === ctx.foe && foeIdsCache.turn === ctx.turn) return foeIdsCache.list;
	const list = scanSt.obs[ctx.side === ctx.battle.sides[0] ? 1 : 0].map((n) => {
		const m = ctx.move(n);
		return m && m.exists ? m.id : '';
	});
	foeIdsCache = { foe: ctx.foe, turn: ctx.turn, list };
	return list;
}
function knowsEffect(ctx, who, eff) {
	if (who === 'atk') return ctx.moves.some((ms) => ms && ms.id && ctx.effectOf(ms.id) === eff);
	return foeIds(ctx).some((id) => id && ctx.effectOf(id) === eff);
}
const CLS = { IMMUNE: 0, QUARTER_DAMAGE: 10, HALF_DAMAGE: 20, DOUBLE_DAMAGE: 80, QUADRUPLE_DAMAGE: 160 };
function clsNow(ctx, id) { return ctx.eff(id, ctx.foeActive).class; }
const noDmg = (ctx, id) => { const c = clsNow(ctx, id); return c === 0 || c === 10 || c === 20; };
// held items: real item both sides (pinned engine deviation)
const holdOf = (ctx, p) => (ctx.itemRec(p.item || '') || {}).hold || '';
// decomp movePrevByBattler (EXPERT-DEV 8)
function lastId(ctx, who) {
	const p = who === 'atk' ? ctx.active : ctx.foeActive;
	const raw = ctx.lastUsedOf(p);
	if (!raw) return null;
	return typeof raw === 'string' ? raw : (raw.id || null);
}
const NONE_EFFECT = 'NONE'; // EXPERT-DEV 7
const NONE_CLASS = 'STATUS'; // EXPERT-DEV 7
function lastMoveData(ctx) {
	const id = lastId(ctx, 'def');
	const mv = id ? ctx.move(id) : null;
	return {
		id: id || '',
		power: mv ? (mv.basePower || 0) : 0,
		effect: id ? (ctx.effectOf(id) || NONE_EFFECT) : NONE_EFFECT,
		cls: mv ? mv.category : NONE_CLASS,
	};
}
// party helpers
const aliveParty = (ctx, who) => {
	const side = who === 'atk' ? ctx.side : ctx.foe;
	const self = who === 'atk' ? ctx.active : ctx.foeActive;
	return side.pokemon.filter((m) => m !== self && m.hp > 0 && !m.fainted);
};
function partyMemberStatus(ctx) {
	return aliveParty(ctx, 'atk').some((m) => !!m.status);
}
// EXPERT-DEV 16 (observer gap): observer.entryTurn stays 0 for the lead — its
// |switch| line is processed while battle.turn === 0, so "0" means "from battle
// start". NOTES pins LoadBattlerTurnCount = totalTurns − fakeOutTurnNumber:
// lead → turn−1 at turn T; a mid-battle switch recorded at turn E → T−E+1.
// LoadIsFirstTurnInBattle ⇔ turnCount ≤ 1 (decomp fakeOutTurnNumber < totalTurns).
function turnCount(ctx, p) {
	const e = scanSt ? scanSt.entry[sideIdxOf(ctx, p)][0] : ctx.entryTurn(p);
	return e === 0 ? ctx.turn - 1 : ctx.turn - e + 1;
}
const globalTurn = (ctx) => ctx.turn - 1; // LoadTurnCount: totalTurns (fork turn−1)
const firstTurnInBattle = (ctx, p) => turnCount(ctx, p) <= 1;
// ---- damage model (decomp TrainerAI_CalcAllDamage candidate lists) ---------
const RISKY = new Set([
	E('HALVE_DEFENSE'), E('RECOVER_DAMAGE_SLEEP'), E('CHARGE_TURN_HIGH_CRIT'),
	E('CHARGE_TURN_HIGH_CRIT_FLINCH'), E('RECHARGE_AFTER'), E('CHARGE_TURN_DEF_UP'),
	E('SKIP_CHARGE_TURN_IN_SUN'), E('SPIT_UP'), E('HIT_LAST_WHIFF_IF_HIT'),
	E('LOWER_OWN_ATK_AND_DEF'), E('DECREASE_POWER_WITH_LESS_USER_HP'),
	E('HIT_FIRST_IF_TARGET_ATTACKING'), E('RECOIL_HALF'),
].map(String));
const ALT_POWER = new Set([
	E('RANDOM_POWER_BASED_ON_IVS'), E('POWER_BASED_ON_LOW_SPEED'), E('NATURAL_GIFT'),
	E('JUDGEMENT'), E('40_DAMAGE_FLAT'), E('LEVEL_DAMAGE_FLAT'),
	E('RANDOM_DAMAGE_1_TO_150_LEVEL'), E('POWER_BASED_ON_FRIENDSHIP'),
	E('POWER_BASED_ON_LOW_FRIENDSHIP'), E('20_DAMAGE_FLAT'), E('INCREASE_POWER_WITH_WEIGHT'),
].map(String));
function bestDamage(ctx, src, target, moves) {
	let best = 0;
	for (const ms of moves) {
		const id = ms && ms.id;
		if (!id) continue;
		const eff = ctx.effectOf(id);
		const mv = ctx.move(id);
		const ok = ALT_POWER.has(eff) || (mv && mv.basePower > 1 && !RISKY.has(eff));
		if (!ok) continue;
		const d = ctx.dmg(src, target, id);
		if (d > best) best = d;
	}
	return best;
}
// IfBattlerDealsMoreDamage(DEFENDER): jump iff defender's last move beats attacker's max
function foeDealsMore(ctx) {
	const mine = bestDamage(ctx, ctx.active, ctx.foeActive, ctx.moves);
	const id = lastId(ctx, 'def');
	const theirs = id ? ctx.dmg(ctx.foeActive, ctx.active, id) : 0;
	return theirs > mine;
}
// IfPartyMemberDealsMoreDamage (EXPERT-DEV 9)
function partyDealsMore(ctx) {
	const mine = bestDamage(ctx, ctx.active, ctx.foeActive, ctx.moves);
	return aliveParty(ctx, 'atk').some((m) => bestDamage(ctx, m, ctx.foeActive, m.moveSlots || []) > mine);
}
const hasDamagingMoves = (ctx) => ctx.moves.some((ms) => ms && ms.id && ctx.move(ms.id).basePower > 0);
// DiffStatStages(DEFENDER, stat) = stage(defender) - stage(attacker)
const diffStages = (ctx, stat) => stg(ctx, 'def', stat) - stg(ctx, 'atk', stat);
// ---- cross-routine observables (EXPERT-DEV 4/5/6) --------------------------
const protectSeen = new WeakMap();   // p -> { turn, chain, last }
const itemPrev = new WeakMap();      // p -> { turn, prev, lost }
const usedSeen = new WeakMap();      // p -> { turn, set }
function protectChain(ctx, who) {
	const p = who === 'atk' ? ctx.active : ctx.foeActive;
	const id = lastId(ctx, who) || '';
	const isProt = id === 'protect' || id === 'detect' || id === 'endure';
	const st = protectSeen.get(p);
	if (st && st.turn === ctx.turn) return st.chain;
	const chain = isProt ? (st && st.last ? st.chain + 1 : 1) : 0;
	protectSeen.set(p, { turn: ctx.turn, chain, last: isProt });
	return chain;
}
function recycleItem(ctx, who) {
	const p = who === 'atk' ? ctx.active : ctx.foeActive;
	const cur = p.item || '';
	const st = itemPrev.get(p);
	if (st && st.turn === ctx.turn) return st.lost;
	let lost = st ? st.lost : '';
	if (st && st.prev && cur === '') lost = st.prev; // item vanished → recyclable
	if (cur !== '') lost = ''; // regained / still held
	itemPrev.set(p, { turn: ctx.turn, prev: cur, lost });
	return lost;
}
function canUseLastResort(ctx) {
	const p = ctx.active;
	const id = lastId(ctx, 'atk');
	let st = usedSeen.get(p);
	if (!st) { st = { turn: ctx.turn, set: new Set(id ? [id] : []) }; usedSeen.set(p, st); }
	else if (st.turn !== ctx.turn) { st.turn = ctx.turn; if (id) st.set.add(id); }
	const known = ctx.moves.filter((ms) => ms && ms.id).length;
	return known > 1 && st.set.size >= known - 1;
}
const isDoubles = (ctx) => ctx.battle.gameType !== 'singles';

// ===========================================================================
// Expert routines (decomp label order). curId = the move being scored.
// ===========================================================================
function Expert_StatusSleep(ctx) {
	const TryPlus1 = () => { if (!rnd(ctx, 128)) add(ctx, 1); };
	if (knowsEffect(ctx, 'atk', E('RECOVER_DAMAGE_SLEEP'))) return TryPlus1(); // Dream Eater
	if (knowsEffect(ctx, 'atk', E('STATUS_NIGHTMARE'))) return TryPlus1();
}
function Expert_DrainMove(ctx, id) {
	if (!noDmg(ctx, id)) return;
	if (!rnd(ctx, 50)) add(ctx, -3);
}
function Expert_Explosion(ctx) {
	const TryMinus1 = () => { if (!rnd(ctx, 50)) add(ctx, -1); };
	const CheckLow = () => {
		if (hpA(ctx) > 30) return;
		if (!rnd(ctx, 50)) add(ctx, 1);
	};
	const CheckMedium = () => {
		if (hpA(ctx) > 50) return TryMinus1();
		if (!rnd(ctx, 128)) add(ctx, 1);
		CheckLow();
	};
	const CheckHigh = () => {
		if (hpA(ctx) < 80) return CheckMedium();
		if (slower(ctx)) return CheckMedium();
		if (!rnd(ctx, 50)) add(ctx, -3); // GoTo global ScoreMinus3
	};
	if (stg(ctx, 'def', 'evasion') < 7) return CheckHigh();
	add(ctx, -1);
	if (stg(ctx, 'def', 'evasion') < 10) return CheckHigh();
	if (rnd(ctx, 128)) return CheckHigh();
	add(ctx, -1);
	CheckHigh();
}
function Expert_DreamEater(ctx, id) {
	if (noDmg(ctx, id)) return add(ctx, -1);
	if (!SLEEP(ctx.foeActive)) return;
	if (!rnd(ctx, 51)) add(ctx, 3);
}
const MOVE_TABLE_MIRROR = new Set(['sleeppowder', 'lovelykiss', 'spore', 'hypnosis', 'sing',
	'grasswhistle', 'shadowpunch', 'sandattack', 'smokescreen', 'toxic', 'guillotine', 'horndrill',
	'fissure', 'sheercold', 'crosschop', 'aeroblast', 'confuseray', 'sweetkiss', 'screech',
	'cottonspore', 'scaryface', 'faketears', 'metalsound', 'thunderwave', 'glare', 'poisonpowder',
	'shadowball', 'dynamicpunch', 'hyperbeam', 'extremespeed', 'thief', 'covet', 'attract',
	'swagger', 'torment', 'flatter', 'trick', 'superpower', 'skillswap', 'psychoshift',
	'powerswap', 'guardswap', 'suckerpunch', 'heartswap', 'switcheroo', 'captivate', 'darkvoid']);
function Expert_MirrorMove(ctx) {
	const TryMinus1 = () => {
		const lm = lastId(ctx, 'def');
		if (lm && MOVE_TABLE_MIRROR.has(lm)) return;
		if (!rnd(ctx, 80)) add(ctx, -1);
	};
	if (slower(ctx)) return TryMinus1();
	const lm = lastId(ctx, 'def');
	if (!lm || !MOVE_TABLE_MIRROR.has(lm)) return TryMinus1();
	if (!rnd(ctx, 128)) add(ctx, 2);
}
// Status{Attack,SpAttack}Up — identical bodies (terminal skip-N differs)
function statusUpA(ctx, stat, nRange) {
	const HPRange = () => {
		if (hpA(ctx) > 70) return;
		if (hpA(ctx) < 40) return add(ctx, -2);
		if (!rnd(ctx, nRange)) add(ctx, -2);
	};
	const AtMax = () => {
		if (hpA(ctx) !== 100) return HPRange();
		if (!rnd(ctx, 128)) add(ctx, 2);
		HPRange();
	};
	if (stg(ctx, 'atk', stat) < 9) return AtMax();
	if (!rnd(ctx, 100)) add(ctx, -1);
	HPRange();
}
// Status{Defense,SpDefense}Up — identical bodies, class check differs
function statusUpD(ctx, stat, wantClass) {
	const Minus2 = () => add(ctx, -2);
	const LowRoll = () => { if (!rnd(ctx, 60)) Minus2(); };
	const Medium = () => {
		if (hpA(ctx) < 40) return Minus2();
		const lm = lastMoveData(ctx);
		if (lm.power === 0) return LowRoll();
		if (lm.cls === wantClass) return Minus2();
		if (!rnd(ctx, 60)) Minus2();
	};
	const High = () => {
		if (hpA(ctx) < 70) return Medium();
		if (!rnd(ctx, 200)) return Medium(); // 78.1% suppress
	};
	const AtMax = () => {
		if (hpA(ctx) !== 100) return High();
		if (!rnd(ctx, 128)) add(ctx, 2);
		High();
	};
	if (stg(ctx, 'atk', stat) < 9) return AtMax();
	if (!rnd(ctx, 100)) add(ctx, -1);
	High();
}
const Expert_StatusAttackUp = (ctx) => statusUpA(ctx, 'atk', 40);
const Expert_StatusSpAttackUp = (ctx) => statusUpA(ctx, 'spa', 70);
const Expert_StatusDefenseUp = (ctx) => statusUpD(ctx, 'def', 'Special');
const Expert_StatusSpDefenseUp = (ctx) => statusUpD(ctx, 'spd', 'Physical');
// (script.s ~2063) undocumented faster-path −3
const Expert_StatusSpeedUp = (ctx) => {
	if (!slower(ctx)) return add(ctx, -3);
	if (!rnd(ctx, 70)) add(ctx, 3);
};
function Expert_StatusAccuracyUp(ctx) {
	const TryMinus2 = () => { if (hpA(ctx) <= 70) add(ctx, -2); };
	if (stg(ctx, 'atk', 'accuracy') < 9) return TryMinus2();
	if (rnd(ctx, 50)) return TryMinus2();
	add(ctx, -2);
	TryMinus2();
}
function Expert_StatusEvasionUp(ctx) {
	const CheckCursed = () => {
		if (!hasVolatile(ctx, 'def', 'CURSE')) return CheckHPRanges();
		if (!rnd(ctx, 70)) add(ctx, 3);
		CheckHPRanges();
	};
	const CheckAqua = () => {
		if (!hasMoveEffect(ctx, 'atk', 'AQUA_RING')) return CheckCursed();
		if (!rnd(ctx, 128)) { add(ctx, 2); return CheckCursed(); }
		CheckCursed();
	};
	const CheckIngrain = () => {
		if (!hasMoveEffect(ctx, 'atk', 'INGRAIN')) return CheckAqua();
		if (!rnd(ctx, 128)) { add(ctx, 2); return CheckCursed(); }
		CheckCursed();
	};
	const CheckSeeded = () => {
		if (!hasMoveEffect(ctx, 'def', 'LEECH_SEED')) return CheckIngrain();
		if (!rnd(ctx, 70)) add(ctx, 3);
		CheckIngrain();
	};
	const CheckToxic = () => {
		if (!hasStatus(ctx, 'def', 'MON_CONDITION_TOXIC')) return CheckSeeded();
		if (hpA(ctx) > 50) {
			if (!rnd(ctx, 50)) add(ctx, 3);
			return CheckSeeded();
		}
	if (rnd(ctx, 80)) return CheckSeeded(); // low-HP path: FIRST roll skips
	if (!rnd(ctx, 50)) add(ctx, 3); // then falls into TryScorePlus3: second roll
	CheckSeeded();
	};
	const CheckStatStage = () => {
		if (stg(ctx, 'atk', 'evasion') < 9) return CheckToxic();
		if (!rnd(ctx, 128)) add(ctx, -1);
		CheckToxic();
	};
	const CheckHPRanges = () => {
		if (hpA(ctx) > 70) return;
		if (stg(ctx, 'atk', 'evasion') === 6) return;
		if (hpA(ctx) < 40 || hpD(ctx) < 40) return add(ctx, -2);
		if (!rnd(ctx, 70)) add(ctx, -2);
	};
	if (hpA(ctx) < 90) return CheckStatStage();
	if (!rnd(ctx, 100)) add(ctx, 3);
	CheckStatStage();
}
function Expert_BypassAccuracyMove(ctx) {
	const TryPlus1 = () => { if (!rnd(ctx, 100)) add(ctx, 1); };
	const Plus1 = () => { add(ctx, 1); TryPlus1(); };
	if (stg(ctx, 'def', 'evasion') > 10) return Plus1();
	if (stg(ctx, 'atk', 'accuracy') < 2) return Plus1();
	if (stg(ctx, 'def', 'evasion') > 8) return TryPlus1();
	if (stg(ctx, 'atk', 'accuracy') < 4) return TryPlus1();
}
function statusDownA(ctx, stat, wantClass) {
	const CheckLast = () => {
		const lm = lastMoveData(ctx);
		if (lm.cls !== wantClass) return;
		if (!rnd(ctx, 128)) add(ctx, -2);
	};
	const CheckTargetHP = () => {
		if (hpD(ctx) <= 70) add(ctx, -2);
		CheckLast();
	};
	const CheckStage = () => {
		if (stg(ctx, 'def', stat) > 3) return CheckTargetHP();
		if (!rnd(ctx, 50)) add(ctx, -2);
		CheckTargetHP();
	};
	if (stg(ctx, 'def', stat) === 6) return CheckTargetHP();
	add(ctx, -1);
	if (hpA(ctx) > 90) return CheckStage();
	add(ctx, -1);
	CheckStage();
}
const Expert_StatusAttackDown = (ctx) => statusDownA(ctx, 'atk', 'Special');
const Expert_StatusSpAttackDown = (ctx) => statusDownA(ctx, 'spa', 'Physical');
function statusDownD(ctx, stat) {
	const CheckTargetHP = () => { if (hpD(ctx) <= 70) add(ctx, -2); };
	const TryMinus2 = () => { if (!rnd(ctx, 50)) add(ctx, -2); CheckTargetHP(); };
	if (hpA(ctx) < 70) return TryMinus2();
	if (stg(ctx, 'def', stat) > 3) return CheckTargetHP();
	TryMinus2();
}
const Expert_StatusDefenseDown = (ctx) => statusDownD(ctx, 'def');
const Expert_StatusSpDefenseDown = (ctx) => statusDownD(ctx, 'spd');
const Expert_StatusEvasionDown = (ctx) => statusDownD(ctx, 'evasion');
function Expert_StatusAccuracyDown(ctx) {
	const CheckCursed = () => {
		if (!hasVolatile(ctx, 'def', 'CURSE')) return CheckHPRanges();
		if (!rnd(ctx, 70)) add(ctx, 2);
		CheckHPRanges();
	};
	const CheckAqua = () => {
		if (!hasMoveEffect(ctx, 'atk', 'AQUA_RING')) return CheckCursed();
		if (!rnd(ctx, 128)) add(ctx, 1); // no GoTo in script: same fallthrough
		CheckCursed();
	};
	const CheckIngrain = () => {
		if (!hasMoveEffect(ctx, 'atk', 'INGRAIN')) return CheckAqua();
		if (!rnd(ctx, 128)) { add(ctx, 1); return CheckCursed(); }
		CheckCursed();
	};
	const CheckSeeded = () => {
		if (!hasMoveEffect(ctx, 'def', 'LEECH_SEED')) return CheckIngrain();
		if (!rnd(ctx, 70)) add(ctx, 2);
		CheckIngrain();
	};
	const CheckToxic = () => {
		if (!hasStatus(ctx, 'def', 'MON_CONDITION_TOXIC')) return CheckSeeded();
		if (!rnd(ctx, 70)) add(ctx, 2);
		CheckSeeded();
	};
	const CheckAccuracy = () => {
		if (stg(ctx, 'atk', 'accuracy') > 4) return CheckToxic();
		if (!rnd(ctx, 80)) add(ctx, -2);
		CheckToxic();
	};
	const CheckHPRanges = () => {
		if (hpA(ctx) > 70) return;
		if (stg(ctx, 'def', 'accuracy') === 6) return;
		if (hpA(ctx) < 40 || hpD(ctx) < 40) return add(ctx, -2);
		if (!rnd(ctx, 70)) add(ctx, -2);
	};
	if (hpA(ctx) < 70) {
		if (!rnd(ctx, 100)) add(ctx, -1);
		return CheckAccuracy();
	}
	if (hpD(ctx) > 70) return CheckAccuracy();
	if (!rnd(ctx, 100)) add(ctx, -1); // falls into TryScoreMinus1 label: roll applies
	CheckAccuracy();
}
function Expert_Haze(ctx) {
	const hi = (who, stats, n) => stats.some((k) => stg(ctx, who, k) > n);
	const lo = (who, stats, n) => stats.some((k) => stg(ctx, who, k) < n);
	const TryPlus3 = () => { if (!rnd(ctx, 50)) add(ctx, 3); };
	const CheckToEncourage = () => {
		if (hi('def', ['atk', 'def', 'spa', 'spd', 'evasion'], 8)) return TryPlus3();
		if (lo('atk', ['atk', 'def', 'spa', 'spd', 'accuracy'], 4)) return TryPlus3();
		if (!rnd(ctx, 50)) add(ctx, -1);
	};
	const TryMinus3 = () => { if (!rnd(ctx, 50)) add(ctx, -3); CheckToEncourage(); };
	if (hi('atk', ['atk', 'def', 'spa', 'spd', 'evasion'], 8)) return TryMinus3();
	if (lo('def', ['atk', 'def', 'spa', 'spd', 'accuracy'], 4)) return TryMinus3();
	CheckToEncourage();
}
const Expert_Bide = (ctx) => { if (hpA(ctx) <= 90) add(ctx, -2); };
function Expert_ForceSwitch(ctx) {
	const P50 = () => { if (!rnd(ctx, 128)) add(ctx, 2); };
	const P75 = () => { if (!rnd(ctx, 64)) return P50(); add(ctx, 2); P50(); };
	if (turnCount(ctx, ctx.foeActive) > 3) return P75();
	if (sideCond(ctx, 'def', 'SPIKES') || sideCond(ctx, 'def', 'STEALTH_ROCK') || sideCond(ctx, 'def', 'TOXIC_SPIKES')) return P50();
	if (['atk', 'def', 'spa', 'spd', 'evasion'].some((k) => stg(ctx, 'def', k) > 8)) return P50();
	add(ctx, -3);
}
function Expert_Conversion(ctx) {
	if (hpA(ctx) <= 90) add(ctx, -2);
	if (globalTurn(ctx) === 0) return;
	if (rnd(ctx, 200)) add(ctx, -2); // GoTo global ScoreMinus2 (applies + PopOrEnd)
}
function Expert_Synthesis(ctx) {
	const w = wx(ctx);
	if (w === 'HAILING' || w === 'RAINING' || w === 'SANDSTORM') add(ctx, -2);
	Expert_Recovery(ctx); // fallthrough (Expert_Synthesis_ScoreMinus2 runs into it)
}
function Expert_Recovery(ctx) {
	const TryPlus2 = () => { if (!rnd(ctx, 20)) add(ctx, 2); };
	const ForSnatch = () => {
		if (!knowsEffect(ctx, 'def', E('STEAL_STATUS_MOVE'))) return TryPlus2();
		if (rnd(ctx, 100)) return; // knows Snatch: 39% abort
		TryPlus2();
	};
	const CheckHP = () => {
		if (hpA(ctx) < 70) return ForSnatch();
		if (rnd(ctx, 30)) return add(ctx, -3);
		ForSnatch();
	};
	if (hpA(ctx) === 100) return add(ctx, -3);
	if (!slower(ctx)) return add(ctx, -8);
	CheckHP();
}
// dead decomp label (no branch reaches it) — kept for parity:
// function Expert_Recovery_Unused(ctx) { if (hpA(ctx) < 50) ForSnatch(); if (hpA(ctx) > 80) -3End; if (!rnd(70)) ForSnatch(); }
function Expert_ToxicLeechSeed(ctx) {
	const TryPlus2 = () => { if (!rnd(ctx, 60)) add(ctx, 2); };
	const CheckEffects = () => {
		if (knowsEffect(ctx, 'atk', E('SP_DEF_UP'))) return TryPlus2();
		if (knowsEffect(ctx, 'atk', E('PROTECT'))) return TryPlus2();
	};
	if (hasDamagingMoves(ctx)) {
		if (hpA(ctx) <= 50 && !rnd(ctx, 50)) add(ctx, -3);
		if (hpD(ctx) <= 50 && !rnd(ctx, 50)) add(ctx, -3);
	}
	CheckEffects();
}
function screenReflect(ctx, wantClass) {
	const CheckLast = () => {
		if (lastMoveData(ctx).cls !== wantClass) return;
		if (!rnd(ctx, 64)) add(ctx, 1);
	};
	if (hpA(ctx) < 50) return add(ctx, -2);
	if (hpA(ctx) >= 90) { if (!rnd(ctx, 128)) add(ctx, 1); }
	CheckLast();
}
const Expert_LightScreen = (ctx) => screenReflect(ctx, 'Special');
const Expert_Reflect = (ctx) => screenReflect(ctx, 'Physical');
function Expert_Rest(ctx) {
	const TryPlus3 = () => { if (!rnd(ctx, 10)) add(ctx, 3); };
	const ForSnatch = () => {
		if (!knowsEffect(ctx, 'def', E('STEAL_STATUS_MOVE'))) return TryPlus3();
		if (rnd(ctx, 50)) return;
		TryPlus3();
	};
	const Faster = () => {
		if (hpA(ctx) < 40) return ForSnatch();
		if (hpA(ctx) > 50) return add(ctx, -3);
		if (rnd(ctx, 70)) return ForSnatch();
		add(ctx, -3);
	};
	const Slower = () => {
		if (hpA(ctx) < 60) return ForSnatch();
		if (hpA(ctx) > 70) return add(ctx, -3);
		if (rnd(ctx, 50)) return ForSnatch();
		add(ctx, -3);
	};
	if (!slower(ctx)) {
		if (hpA(ctx) !== 100) return Faster();
		return add(ctx, -8);
	}
	Slower();
}
const Expert_OHKOMove = (ctx) => { if (!rnd(ctx, 192)) add(ctx, 1); };
const Expert_SuperFang = (ctx) => { if (hpD(ctx) <= 50) add(ctx, -1); };
function Expert_BindingMove(ctx) {
	const d = ctx.foeActive;
	if (!TOXIC(d) && !hasVolatile(ctx, 'def', 'CURSE') && !hasMoveEffect(ctx, 'def', 'PERISH_SONG') && !hasVolatile(ctx, 'def', 'ATTRACT')) return;
	if (!rnd(ctx, 128)) add(ctx, 1);
}
function Expert_HighCritical(ctx, id) {
	const TryPlus1 = () => { if (!rnd(ctx, 128)) add(ctx, 1); };
	if (noDmg(ctx, id)) return;
	const c = clsNow(ctx, id);
	if (c === 80 || c === 160) return TryPlus1();
	if (rnd(ctx, 128)) return; // normal damage: TWO rolls → 25% (script fallthrough)
	TryPlus1();
}
function Expert_StatusConfuse(ctx) {
	if (hpD(ctx) > 70) return;
	if (rnd(ctx, 128)) return;
	add(ctx, -1);
	if (hpD(ctx) > 50) return;
	add(ctx, -1);
	if (hpD(ctx) > 30) return;
	add(ctx, -1);
}
const Expert_Flatter = (ctx) => {
	if (!rnd(ctx, 128)) return Expert_StatusConfuse(ctx);
	add(ctx, 1);
	Expert_StatusConfuse(ctx);
};
function Expert_Swagger(ctx) {
	if (!knows(ctx, 'atk', 'psychup')) return Expert_Flatter(ctx);
	if (stg(ctx, 'def', 'atk') > 3) return add(ctx, -5);
	add(ctx, 3);
	if (globalTurn(ctx) !== 0) return;
	add(ctx, 2);
}
function Expert_StatusPoison(ctx) {
	if (hpA(ctx) < 50) return add(ctx, -1);
	if (hpD(ctx) > 50) return;
	add(ctx, -1);
}
function Expert_StatusParalyze(ctx) {
	if (slower(ctx)) {
		if (!rnd(ctx, 20)) add(ctx, 3);
		return;
	}
	if (hpA(ctx) <= 70) add(ctx, -1);
}
function Expert_VitalThrow(ctx) {
	if (slower(ctx)) return;
	if (hpA(ctx) > 60) return;
	if (hpA(ctx) < 40) { if (!rnd(ctx, 50)) add(ctx, -1); return; }
	if (rnd(ctx, 180)) return; // falls into TryScoreMinus1
	if (!rnd(ctx, 50)) add(ctx, -1);
}
function Expert_Substitute(ctx) {
	const TryPlus1 = () => { if (!rnd(ctx, 100)) add(ctx, 1); };
	const TargetLastMove = () => {
		if (slower(ctx)) return;
		const e = lastMoveData(ctx).effect;
		if (e === E('STATUS_SLEEP') || e === E('STATUS_BADLY_POISON') || e === E('STATUS_POISON') ||
			e === E('STATUS_PARALYZE') || e === E('STATUS_BURN')) {
			// code: bonus when the target is NOT statused (comment says otherwise)
			if (!hasStatus(ctx, 'def', 'MON_CONDITION_ANY')) TryPlus1();
			return;
		}
		if (e === E('STATUS_CONFUSE')) {
			if (!hasVolatile(ctx, 'def', 'CONFUSION')) TryPlus1();
			return;
		}
		if (e === E('STATUS_LEECH_SEED')) {
			// code: bonus when the target is NOT seeded (comment says otherwise)
			if (!hasMoveEffect(ctx, 'def', 'LEECH_SEED')) TryPlus1();
		}
	};
	const FinalRound = () => {
		if (rnd(ctx, 100)) return TargetLastMove();
		add(ctx, -1);
		TargetLastMove();
	};
	const SecondRound = () => {
		if (rnd(ctx, 100)) return FinalRound();
		add(ctx, -1);
		FinalRound();
	};
	const CheckUserHP = () => {
		if (hpA(ctx) > 90) return TargetLastMove();
		if (hpA(ctx) > 70) return FinalRound();
		if (hpA(ctx) > 50) return SecondRound();
		if (rnd(ctx, 100)) return SecondRound();
		add(ctx, -1);
		SecondRound();
	};
	if (knows(ctx, 'atk', 'focuspunch') && !rnd(ctx, 96)) add(ctx, 1);
	CheckUserHP();
}
function Expert_RechargeTurn(ctx, id) {
	const TryPlus1 = () => { if (!rnd(ctx, 80)) add(ctx, 1); };
	const CheckUserHP = () => { if (hpA(ctx) >= 60) add(ctx, -1); };
	if (noDmg(ctx, id)) return add(ctx, -1);
	if (ctx.abilityOf(ctx.active) === 'truant') return TryPlus1();
	if (slower(ctx)) return CheckUserHP();
	if (hpA(ctx) > 40) add(ctx, -1);
}
function Expert_Disable(ctx) {
	if (slower(ctx)) return;
	const lm = lastMoveData(ctx);
	if (lm.power === 0) { if (!rnd(ctx, 100)) add(ctx, -1); return; }
	add(ctx, 1);
}
const PHYS_TYPES = new Set(['normal', 'fighting', 'flying', 'poison', 'ground', 'rock', 'bug', 'ghost', 'steel']);
const SPEC_TYPES = new Set(['fire', 'water', 'grass', 'electric', 'psychic', 'ice', 'dragon', 'dark']);
function counterMirrorCoat(ctx, pairMove, typeSet, wantClass) {
	const d = ctx.foeActive;
	if (SLEEP(d) || hasVolatile(ctx, 'def', 'ATTRACT') || hasVolatile(ctx, 'def', 'CONFUSION')) return add(ctx, -1);
	if (hpA(ctx) <= 30) { if (!rnd(ctx, 10)) add(ctx, -1); }
	if (hpA(ctx) <= 50) { if (!rnd(ctx, 100)) add(ctx, -1); }
	const TryPlus4 = () => { if (!rnd(ctx, 100)) add(ctx, 4); };
	if (knows(ctx, 'atk', pairMove)) return TryPlus4();
	const lm = lastMoveData(ctx);
	const CheckTypes = () => {
		const [t1, t2] = typesOf(ctx, d);
		if (typeSet.has(t1) || typeSet.has(t2)) return;
		if (!rnd(ctx, 50)) TryPlus4(); // script adjacency: falls into TryScorePlus4
	};
	if (lm.power === 0) { // last move was a status move
		if (!taunted(d)) return CheckTypes();
		if (!rnd(ctx, 100)) return CheckTypes();
		add(ctx, 1);
		return CheckTypes();
	}
	const CheckClass = () => {
		if (lm.cls !== wantClass) return add(ctx, -1);
		if (!rnd(ctx, 100)) add(ctx, 1);
	};
	if (!taunted(d)) return CheckClass();
	// damaging + taunted: 60.9% +1, then the class check
	if (!rnd(ctx, 100)) add(ctx, 1);
	CheckClass();
}
const Expert_Counter = (ctx) => counterMirrorCoat(ctx, 'mirrorcoat', PHYS_TYPES, 'Physical');
const Expert_MirrorCoat = (ctx) => counterMirrorCoat(ctx, 'counter', SPEC_TYPES, 'Special');
const ENCORE_EFFECTS = new Set([
	E('RECOVER_DAMAGE_SLEEP'), E('ATK_UP'), E('DEF_UP'), E('SPEED_UP'), E('SP_ATK_UP'),
	E('RESET_STAT_CHANGES'), E('FORCE_SWITCH'), E('CONVERSION'), E('STATUS_BADLY_POISON'),
	E('SET_LIGHT_SCREEN'), E('REST'), E('HALVE_HP'), E('SP_DEF_UP_2'), E('STATUS_CONFUSE'),
	E('STATUS_POISON'), E('STATUS_PARALYZE'), E('STATUS_LEECH_SEED'), E('DO_NOTHING'),
	E('ATK_UP_2'), E('ENCORE'), E('CONVERSION2'), E('NEXT_ATTACK_ALWAYS_HITS'),
	E('CURE_PARTY_STATUS'), E('PREVENT_ESCAPE'), E('STATUS_NIGHTMARE'), E('PROTECT'),
	E('SWITCH_ABILITIES'), E('FORESIGHT'), E('ALL_FAINT_3_TURNS'), E('WEATHER_SANDSTORM'),
	E('SURVIVE_WITH_1_HP'), E('ATK_UP_2_STATUS_CONFUSION'), E('INFATUATE'), E('PREVENT_STATUS'),
	E('WEATHER_RAIN'), E('WEATHER_SUN'), E('MAX_ATK_LOSE_HALF_MAX_HP'), E('COPY_STAT_CHANGES'),
	E('HIT_IN_3_TURNS'), E('ALWAYS_FLINCH_FIRST_TURN_ONLY'), E('STOCKPILE'), E('SPIT_UP'),
	E('SWALLOW'), E('WEATHER_HAIL'), E('TORMENT'), E('STATUS_BURN'), E('MAKE_GLOBAL_TARGET'),
	E('SP_DEF_UP_DOUBLE_ELECTRIC_POWER'), E('SWITCH_HELD_ITEMS'), E('COPY_ABILITY'),
	E('GROUND_TRAP_USER_CONTINUOUS_HEAL'), E('RECYCLE'), E('REMOVE_HELD_ITEM'),
	E('MAKE_SHARED_MOVES_UNUSEABLE'), E('HEAL_STATUS'), E('REMOVE_ALL_PP_ON_DEFEAT'),
	E('CONFUSE_ALL'), E('HALVE_ELECTRIC_DAMAGE'), E('HALVE_FIRE_DAMAGE'), E('ATK_SPD_UP'),
	E('CAMOUFLAGE'), E('GRAVITY'), E('IGNORE_EVATION_REMOVE_DARK_IMMUNE'),
	E('FAINT_AND_FULL_HEAL_NEXT_MON'), E('NATURAL_GIFT'), E('REMOVE_PROTECT'),
	E('DOUBLE_SPEED_3_TURNS'), E('RANDOM_STAT_UP_2'), E('FLING'), E('TRANSFER_STATUS'),
	E('PREVENT_HEALING'), E('SWAP_ATK_DEF'), E('SUPRESS_ABILITY'), E('PREVENT_CRITS'),
	E('SWAP_ATK_SP_ATK_STAT_CHANGES'), E('SWAP_DEF_SP_DEF_STAT_CHANGES'),
	E('SET_ABILITY_TO_INSOMNIA'), E('SWAP_STAT_CHANGES'), E('RESTORE_HP_EVERY_TURN'),
	E('GIVE_GROUND_IMMUNITY'), E('TRICK_ROOM'),
].map(String));
function Expert_Encore(ctx) {
	const TryPlus3 = () => { if (!rnd(ctx, 30)) add(ctx, 3); };
	if (disabledFoe(ctx)) return TryPlus3();
	if (slower(ctx)) return add(ctx, -2);
	if (!ENCORE_EFFECTS.has(lastMoveData(ctx).effect)) return add(ctx, -2);
	TryPlus3();
}
function Expert_PainSplit(ctx) {
	if (hpD(ctx) < 80) return add(ctx, -1);
	if (slower(ctx)) {
		if (hpA(ctx) > 60) return add(ctx, -1);
		return add(ctx, 1);
	}
	if (hpA(ctx) > 40) return add(ctx, -1);
	add(ctx, 1);
}
const Expert_Nightmare = (ctx) => add(ctx, 2);
const Expert_LockOn = (ctx) => { if (!rnd(ctx, 128)) add(ctx, 2); };
const Expert_SleepTalk = (ctx) => {
	if (SLEEP(ctx.active)) return add(ctx, 10); // decomp: GoTo global ScorePlus10
	add(ctx, -5);
};
function Expert_DestinyBond(ctx) {
	add(ctx, -1);
	if (slower(ctx)) return;
	if (hpA(ctx) > 70) return;
	if (!rnd(ctx, 128)) add(ctx, 1);
	if (hpA(ctx) > 50) return;
	if (!rnd(ctx, 128)) add(ctx, 1);
	if (hpA(ctx) > 30) return;
	if (!rnd(ctx, 100)) add(ctx, 2);
}
function Expert_Reversal(ctx) {
	const TryPlus1 = () => { if (!rnd(ctx, 100)) add(ctx, 1); };
	if (slower(ctx)) {
		if (hpA(ctx) > 60) return add(ctx, -1);
		if (hpA(ctx) > 40) return;
		return TryPlus1();
	}
	if (hpA(ctx) > 33) return add(ctx, -1);
	if (hpA(ctx) > 20) return;
	if (hpA(ctx) < 8) add(ctx, 1);
	TryPlus1();
}
function Expert_HealBell(ctx) {
	if (ANYSTATUS(ctx.active) || partyMemberStatus(ctx)) return;
	add(ctx, -5);
}
const THIEF_GOOD_HOLDS = new Set(['slprestore', 'statusrestore', 'hprestore', 'accreduce',
	'hprestoregradual', 'pikaspatkup', 'cuboneatkup', 'hprestorepsntype', 'weakennormal',
	'weakensefire', 'weakensewater', 'weakenseelectric', 'weakensegrass', 'weakenseice',
	'weakensefight', 'weakensepoison', 'weakenseground', 'weakenseflying', 'weakensepsychic',
	'weakensebug', 'weakenserock', 'weakenseghost', 'weakensedragon', 'weakensedark', 'weakensesteel']);
function Expert_Thief(ctx) {
	if (!THIEF_GOOD_HOLDS.has(holdOf(ctx, ctx.foeActive))) return add(ctx, -2);
	if (!rnd(ctx, 50)) add(ctx, 1);
}
function Expert_Curse(ctx) {
	const CheckStage2 = () => {
		if (stg(ctx, 'atk', 'def') > 6) return;
		if (!rnd(ctx, 128)) add(ctx, 1);
	};
	const CheckStage1 = () => {
		if (stg(ctx, 'atk', 'def') > 7) return;
		if (!rnd(ctx, 128)) add(ctx, 1);
		CheckStage2();
	};
	const HighChance = () => { if (!rnd(ctx, 32)) add(ctx, 1); CheckStage1(); };
	const FlipCoin = () => { if (!rnd(ctx, 128)) add(ctx, 1); CheckStage1(); };
	const GhostCheckHP = () => { if (hpA(ctx) <= 80) add(ctx, -1); };
	const [t1, t2] = typesOf(ctx, ctx.active);
	if (t1 === 'ghost') return GhostCheckHP();
	if (t2 === 'ghost') return GhostCheckHP();
	if (stg(ctx, 'atk', 'def') > 9) return;
	if (knows(ctx, 'atk', 'gyro ball')) return HighChance();
	if (knows(ctx, 'atk', 'trickroom')) return HighChance();
	FlipCoin();
}
function Expert_Protect(ctx) {
	const d = ctx.foeActive;
	if (knows(ctx, 'def', 'feint') || knows(ctx, 'def', 'shadowforce')) {
		if (!rnd(ctx, 128)) add(ctx, -2);
	}
	if (protectChain(ctx, 'atk') > 1) return add(ctx, -2);
	const cursed = TOXIC(ctx.active) || hasVolatile(ctx, 'atk', 'CURSE') || hasMoveEffect(ctx, 'atk', 'PERISH_SONG') ||
		hasVolatile(ctx, 'atk', 'ATTRACT') || hasMoveEffect(ctx, 'atk', 'LEECH_SEED') ||
		hasMoveEffect(ctx, 'atk', 'YAWN') || knowsEffect(ctx, 'def', E('RESTORE_HALF_HP')) ||
		knowsEffect(ctx, 'def', E('DEF_UP_DOUBLE_ROLLOUT_POWER'));
	if (cursed && !hasMoveEffect(ctx, 'atk', 'LOCK_ON')) return add(ctx, -2);
	if (TOXIC(d) || hasVolatile(ctx, 'def', 'CURSE') || hasMoveEffect(ctx, 'def', 'PERISH_SONG') ||
		hasVolatile(ctx, 'def', 'ATTRACT') || hasMoveEffect(ctx, 'def', 'LEECH_SEED') ||
		hasMoveEffect(ctx, 'def', 'YAWN') || isDoubles(ctx) || hasMoveEffect(ctx, 'atk', 'LOCK_ON')) {
		add(ctx, 2);
	} else if (!rnd(ctx, 85)) {
		add(ctx, 2);
	}
	if (!rnd(ctx, 128)) add(ctx, -1);
	if (protectChain(ctx, 'atk') === 0) return;
	add(ctx, -1);
	if (!rnd(ctx, 128)) add(ctx, -1);
}
function hazardSetup(ctx) {
	if (rnd(ctx, 128)) return; // 50% flat +0
	add(ctx, 1);
	if (knows(ctx, 'atk', 'roar') || knows(ctx, 'atk', 'whirlwind')) {
		if (!rnd(ctx, 64)) add(ctx, 1);
	}
}
const Expert_Spikes = hazardSetup;
const Expert_ToxicSpikes = hazardSetup;
const Expert_StealthRock = hazardSetup;
function Expert_Foresight(ctx) {
	// BUG in decomp: checks the ATTACKER's Ghost typing (comment says target)
	const R2 = () => { if (!rnd(ctx, 80)) add(ctx, 2); };
	const R1 = () => { if (rnd(ctx, 80)) return; R2(); };
	const [t1, t2] = typesOf(ctx, ctx.active);
	if (t1 === 'ghost') return R1();
	if (t2 === 'ghost') return R1();
	if (stg(ctx, 'def', 'evasion') > 8) return R2();
	add(ctx, -2);
}
function Expert_Endure(ctx) {
	if (hpA(ctx) < 4) return add(ctx, -1);
	if (hpA(ctx) < 35) { if (!rnd(ctx, 70)) add(ctx, 1); }
}
function Expert_BatonPass(ctx) {
	const TryPlus2 = () => { if (!rnd(ctx, 80)) add(ctx, 2); };
	const HighCheck = () => {
		if (!slower(ctx)) {
			if (hpA(ctx) > 60) return;
			return TryPlus2();
		}
		if (hpA(ctx) > 70) return;
		TryPlus2();
	};
	const hi = (n) => ['atk', 'def', 'spa', 'spd', 'evasion'].some((k) => stg(ctx, 'atk', k) > n);
	if (hi(8)) return HighCheck();
	const MedCheck = () => {
		if (!slower(ctx)) {
			if (hpA(ctx) > 60) return add(ctx, -2);
			return;
		}
		if (hpA(ctx) < 70) return;
		add(ctx, -2);
	};
	if (hi(7)) return MedCheck();
	add(ctx, -2);
}
function Expert_Pursuit(ctx) {
	const TryPlus1 = () => { if (!rnd(ctx, 128)) add(ctx, 1); };
	const CheckUturn = () => {
		if (!knows(ctx, 'def', 'uturn')) return;
		if (!rnd(ctx, 128)) add(ctx, 1);
	};
	if (firstTurnInBattle(ctx, ctx.active)) return TryPlus1();
	const [t1, t2] = typesOf(ctx, ctx.foeActive);
	if (t1 === 'ghost') return TryPlus1();
	if (t1 === 'psychic') return TryPlus1(); // decomp re-tests TYPE_1 (quirk)
	if (t2 === 'ghost') return TryPlus1();
	if (t2 === 'psychic') return TryPlus1();
	CheckUturn();
}
function Expert_RainDance(ctx) {
	const Plus1End = () => add(ctx, 1);
	const OtherChecks = () => {
		if (hpA(ctx) < 40) return add(ctx, -1);
		const w = wx(ctx);
		if (w === 'HAILING' || w === 'SUNNY' || w === 'SANDSTORM') return Plus1End();
		const ab = ctx.abilityOf(ctx.active);
		if (ab === 'raindish') return Plus1End();
		if (ab !== 'hydration') return;
		if (ANYSTATUS(ctx.active)) Plus1End();
	};
	if (!faster(ctx) && ctx.abilityOf(ctx.active) === 'swiftswim') return Plus1End();
	OtherChecks();
}
function Expert_SunnyDay(ctx) {
	const Plus1End = () => add(ctx, 1);
	if (hpA(ctx) < 40) return add(ctx, -1);
	const w = wx(ctx);
	if (w === 'HAILING' || w === 'RAINING' || w === 'SANDSTORM') return Plus1End();
	const ab = ctx.abilityOf(ctx.active);
	if (ab === 'flowergift') return Plus1End();
	if (ab !== 'leafguard') return;
	if (ANYSTATUS(ctx.active)) Plus1End();
}
const Expert_BellyDrum = (ctx) => { if (hpA(ctx) < 90) add(ctx, -2); };
function Expert_PsychUp(ctx) {
	const hi = (who, n) => ['atk', 'def', 'spa', 'spd', 'evasion'].some((k) => stg(ctx, who, k) > n);
	const CheckUser = () => {
		const lo = (k) => stg(ctx, 'atk', k) < 7;
		if (lo('atk') || lo('def') || lo('spa') || lo('spd')) return add(ctx, 1);
		if (lo('evasion')) return add(ctx, 2); // ScorePlus2 label adds 1 then falls into ScorePlus1 (+1)
		if (rnd(ctx, 50)) return;
		add(ctx, -2);
	};
	if (hi('def', 8)) return CheckUser();
	add(ctx, -2);
}
function Expert_ChargeTurnNoInvuln(ctx, id) {
	const Plus2End = () => add(ctx, 2);
	const Minus2End = () => add(ctx, -2);
	if (noDmg(ctx, id)) return Minus2End();
	if (ctx.effectOf(id) === E('SKIP_CHARGE_TURN_IN_SUN')) {
		if (wx(ctx) === 'SUNNY') return Plus2End();
	}
	if (ctx.active.item === 'powerherb') return Plus2End();
	if (knowsEffect(ctx, 'def', E('PROTECT'))) return Minus2End();
	if (hpA(ctx) <= 38) add(ctx, -1);
}
// dead jump-table entry (the duplicate SKIP_CHARGE_TURN_IN_SUN row) — body kept:
function Expert_UnusedSolarbeam(ctx, id) {
	const TryMinus3 = () => { if (!rnd(ctx, 50)) add(ctx, -3); };
	if (noDmg(ctx, id)) return TryMinus3();
	const w = wx(ctx);
	if (w === 'SUNNY') return TryMinus3();
	if (w !== 'RAINING') return;
	add(ctx, 1);
}
function Expert_ChargeTurnWithInvuln(ctx, id) {
	if (ctx.active.item === 'powerherb') return add(ctx, 2); // shared ScorePlus2 (+2 End)
	if (knowsEffect(ctx, 'def', E('PROTECT'))) return add(ctx, -1);
	Expert_ShadowForce(ctx, id);
}
function Expert_ShadowForce(ctx, id) {
	const TryPlus1 = () => { if (!rnd(ctx, 80)) add(ctx, 1); };
	const CompareSpeed = () => {
		if (slower(ctx)) return;
		if (lastMoveData(ctx).effect !== E('NEXT_ATTACK_ALWAYS_HITS')) return TryPlus1();
	};
	const CheckConditions = () => {
		const d = ctx.foeActive;
		if (TOXIC(d) || hasVolatile(ctx, 'def', 'CURSE') || hasMoveEffect(ctx, 'def', 'LEECH_SEED')) return TryPlus1();
		const w = wx(ctx);
		if (w === 'SANDSTORM') {
			const [t1, t2] = typesOf(ctx, ctx.active);
			const sand = new Set(['ground', 'rock', 'steel']);
			if (sand.has(t1) || sand.has(t2)) return TryPlus1();
			return CompareSpeed();
		}
		if (w === 'HAILING') {
			const [t1, t2] = typesOf(ctx, ctx.active);
			if (t1 === 'ice' || t2 === 'ice') return TryPlus1();
			return CompareSpeed();
		}
		CompareSpeed();
	};
	if (noDmg(ctx, id)) return add(ctx, 1); // immune/resist → +1 (decomp "Bug?")
	if (ctx.active.item === 'powerherb') return add(ctx, 1);
	CheckConditions();
}
const Expert_FakeOut = (ctx) => add(ctx, 2);
const Expert_SpitUp = (ctx) => {
	const n = (ctx.active.volatiles && ctx.active.volatiles.stockpile && ctx.active.volatiles.stockpile.layers) || 0;
	if (n < 2) return;
	if (!rnd(ctx, 80)) add(ctx, 2);
};
function Expert_Hail(ctx) {
	if (hpA(ctx) < 40) return add(ctx, -1);
	const w = wx(ctx);
	if (w !== 'SUNNY' && w !== 'RAINING' && w !== 'SANDSTORM') return;
	add(ctx, 1);
	if (knows(ctx, 'atk', 'blizzard')) add(ctx, 2); // ATTACKER knows Blizzard
	if (ctx.abilityOf(ctx.active) === 'icebody') add(ctx, 2);
}
function Expert_Facade(ctx) {
	// decomp BUG: checks the target's status (comment says attacker) — code wins
	const p = ctx.foeActive;
	if (!(p.status === 'brn' || p.status === 'psn' || p.status === 'tox' || p.status === 'par')) return;
	add(ctx, 1);
}
function Expert_FocusPunch(ctx, id) {
	const d = ctx.foeActive;
	const Plus1End = () => add(ctx, 1);
	const TryPlus1 = () => { if (rnd(ctx, 100)) return; Plus1End(); };
	if (noDmg(ctx, id)) return add(ctx, -1);
	if (hasVolatile(ctx, 'atk', 'SUBSTITUTE')) return add(ctx, 5);
	if (SLEEP(d)) return Plus1End();
	if (hasVolatile(ctx, 'def', 'ATTRACT')) return TryPlus1();
	if (hasVolatile(ctx, 'def', 'CONFUSION')) return TryPlus1();
	if (firstTurnInBattle(ctx, ctx.active)) return; // IfLoadedNotEqualTo FALSE → End
	if (rnd(ctx, 200)) return;
	add(ctx, 1);
}
const Expert_SmellingSalts = (ctx) => {
	if (ctx.foeActive.status === 'par') add(ctx, 1);
};
const TRICK_DISRUPTIVE = new Set(['choiceatk', 'choicespatk', 'choicespeed', 'speeddowngrounded',
	'prioritydown', 'dmgusercontactxfr', 'lvlupatkevup', 'lvlupdefevup', 'lvlupspatkevup',
	'lvlupspdefevup', 'lvlupspeedevup', 'lvluphpevup']);
const TRICK_POISONING = new Set(['psnuser']);
const TRICK_BURNING = new Set(['brnuser']);
const TRICK_SLUDGE = new Set(['hprestorepsntype']);
const TRICK_FLAVOR = new Set(['hprestorespicy', 'hprestoredry', 'hprestoresweet', 'hprestorebitter', 'hprestoresour']);
const TRICK_BAD = new Set(['evsupspeeddown', 'psnuser', 'brnuser', 'hprestorepsntype',
	'choiceatk', 'choicespatk', 'choicespeed', 'speeddowngrounded', 'prioritydown', 'dmgusercontactxfr',
	'lvlupatkevup', 'lvlupdefevup', 'lvlupspatkevup', 'lvlupspdefevup', 'lvlupspeedevup', 'lvluphpevup']);
const TRICK_BAD_FLAVOR = new Set([...TRICK_BAD, ...TRICK_FLAVOR]);
function Expert_Trick(ctx) {
	const a = ctx.active, d = ctx.foeActive;
	const defHold = holdOf(ctx, d);
	const atkHold = holdOf(ctx, a);
	const foePoisonVuln = () => ANYSTATUS(d) || sideCond(ctx, 'def', 'SAFEGUARD') ||
		typesOf(ctx, d).includes('steel') || typesOf(ctx, d).includes('poison') ||
		['immunity', 'magicguard', 'poisonheal'].includes(ctx.abilityOf(d));
	const selfPoisonVuln = () => ANYSTATUS(a) || sideCond(ctx, 'atk', 'SAFEGUARD') ||
		typesOf(ctx, a).includes('steel') || typesOf(ctx, a).includes('poison') ||
		['immunity', 'magicguard', 'poisonheal', 'klutz'].includes(ctx.abilityOf(a));
	const foeBurnVuln = () => ['waterveil', 'magicguard'].includes(ctx.abilityOf(d)) ||
		ANYSTATUS(d) || sideCond(ctx, 'def', 'SAFEGUARD') || typesOf(ctx, d).includes('fire');
	const selfBurnVuln = () => ['waterveil', 'magicguard'].includes(ctx.abilityOf(a)) ||
		ANYSTATUS(a) || sideCond(ctx, 'atk', 'SAFEGUARD') || typesOf(ctx, a).includes('fire');
	if (TRICK_DISRUPTIVE.has(atkHold)) return add(ctx, TRICK_BAD.has(defHold) ? -3 : 5);
	if (TRICK_POISONING.has(atkHold)) {
		if (TRICK_BAD.has(defHold)) return add(ctx, -3);
		if (foePoisonVuln()) return add(ctx, selfPoisonVuln() ? -3 : 5);
		return add(ctx, 5);
	}
	if (TRICK_BURNING.has(atkHold)) {
		if (TRICK_BAD.has(defHold)) return add(ctx, -3);
		if (foeBurnVuln()) {
			if (['waterveil', 'magicguard'].includes(ctx.abilityOf(a))) return add(ctx, -3);
			if (ctx.abilityOf(a) === 'klutz') return add(ctx, -5); // global ScoreMinus5
			return add(ctx, selfBurnVuln() ? -3 : 5);
		}
		return add(ctx, 5);
	}
	if (TRICK_SLUDGE.has(atkHold)) {
		if (TRICK_BAD.has(defHold)) return add(ctx, -3);
		if (typesOf(ctx, d).includes('poison') || ctx.abilityOf(d) === 'magicguard') {
			return add(ctx, (typesOf(ctx, a).includes('poison') || ['magicguard', 'klutz'].includes(ctx.abilityOf(a))) ? -3 : 5);
		}
		return add(ctx, 5);
	}
	if (TRICK_FLAVOR.has(atkHold)) {
		if (TRICK_BAD_FLAVOR.has(defHold)) return add(ctx, -3);
		if (!rnd(ctx, 50)) add(ctx, 2);
		return;
	}
	add(ctx, -3); // fallthrough: itemless / unknown-item Trick is −3 (script adjacency)
}
const DESIRABLE_ABILITIES = new Set(['speedboost', 'battlearmor', 'sandveil', 'static', 'flashfire',
	'wonderguard', 'effectspore', 'swiftswim', 'hugepower', 'raindish', 'cutecharm', 'shedskin',
	'marvelscale', 'purepower', 'chlorophyll', 'shielddust', 'adaptability', 'magicguard',
	'moldbreaker', 'superluck', 'unaware', 'tintedlens', 'filter', 'solidrock', 'reckless']);
function Expert_ChangeUserAbility(ctx) {
	if (DESIRABLE_ABILITIES.has(ctx.abilityOf(ctx.active))) return add(ctx, -1);
	if (DESIRABLE_ABILITIES.has(ctx.abilityOf(ctx.foeActive))) {
		if (!rnd(ctx, 50)) add(ctx, 2);
	}
}
const Expert_Ingrain = () => {}; // no score change
function Expert_Superpower(ctx, id) {
	if (noDmg(ctx, id)) return add(ctx, -1);
	if (stg(ctx, 'atk', 'atk') < 6) return add(ctx, -1);
	if (slower(ctx)) {
		if (hpA(ctx) < 60) return;
		return add(ctx, -1);
	}
	if (hpA(ctx) > 40) add(ctx, -1);
}
function Expert_MagicCoat(ctx) {
	const CheckFirst = () => {
		if (!firstTurnInBattle(ctx, ctx.active)) {
			if (!rnd(ctx, 30)) add(ctx, -1); // TryScoreMinus1
			return;
		}
		if (!rnd(ctx, 150)) add(ctx, 1);
	};
	if (hpD(ctx) > 30) return CheckFirst();
	if (!rnd(ctx, 100)) return CheckFirst();
	add(ctx, -1);
	CheckFirst();
}
function Expert_Recycle(ctx) {
	const lost = recycleItem(ctx, 'atk');
	if (lost !== 'chestoberry' && lost !== 'lumberry' && lost !== 'starfberry') return add(ctx, -2);
	if (!rnd(ctx, 50)) add(ctx, 1);
}
function Expert_Revenge(ctx) {
	const d = ctx.foeActive;
	if (SLEEP(d) || hasVolatile(ctx, 'def', 'ATTRACT') || hasVolatile(ctx, 'def', 'CONFUSION')) return add(ctx, -2);
	if (!rnd(ctx, 180)) return add(ctx, -2);
	add(ctx, 2);
}
function Expert_BrickBreak(ctx) {
	if (sideCond(ctx, 'def', 'REFLECT') || sideCond(ctx, 'def', 'LIGHT_SCREEN')) add(ctx, 1);
}
function Expert_KnockOff(ctx) {
	if (hpD(ctx) < 30) return;
	if (firstTurnInBattle(ctx, ctx.active)) return;
	if (!rnd(ctx, 180)) add(ctx, 1);
}
function Expert_Endeavor(ctx) {
	if (hpD(ctx) < 70) return add(ctx, -1);
	if (slower(ctx)) return add(ctx, hpA(ctx) > 50 ? -1 : 1);
	add(ctx, hpA(ctx) > 40 ? -1 : 1);
}
function Expert_WaterSpout(ctx, id) {
	if (noDmg(ctx, id)) return add(ctx, -1);
	if (slower(ctx)) {
		if (hpD(ctx) > 70) return; // decomp checks the TARGET's HP (bug) — code wins
		return add(ctx, -1);
	}
	if (hpD(ctx) > 50) return;
	add(ctx, -1);
}
function Expert_Imprison(ctx) {
	if (firstTurnInBattle(ctx, ctx.active)) return;
	if (!rnd(ctx, 100)) add(ctx, 2);
}
const Expert_Refresh = (ctx) => { if (hpD(ctx) < 50) add(ctx, -1); }; // decomp checks target HP (bug)
function Expert_Snatch(ctx) {
	const TryPlus2 = () => { if (!rnd(ctx, 150)) add(ctx, 2); };
	const TryMinus2 = () => { if (!rnd(ctx, 30)) add(ctx, -2); };
	const TryPlus1 = () => { if (rnd(ctx, 230)) return TryMinus2(); add(ctx, 1); };
	const UserIsSlower = () => {
		if (hpD(ctx) > 25) return TryMinus2();
		if (knowsEffect(ctx, 'def', E('RESTORE_HALF_HP'))) return TryPlus2();
		if (knowsEffect(ctx, 'def', E('DEF_UP_DOUBLE_ROLLOUT_POWER'))) return TryPlus2();
		TryPlus1();
	};
	if (firstTurnInBattle(ctx, ctx.active)) return TryPlus2();
	if (rnd(ctx, 30)) return;
	if (slower(ctx)) return UserIsSlower();
	if (hpA(ctx) !== 100) return TryMinus2();
	if (hpD(ctx) < 70) return TryMinus2();
	if (rnd(ctx, 60)) return; // IfRandomLessThan 60, End
	TryMinus2();
}
function mudWaterSport(ctx, wantType) {
	if (hpA(ctx) < 50) return add(ctx, -1);
	if (typesOf(ctx, ctx.foeActive).includes(wantType)) return add(ctx, 1);
	add(ctx, -1);
}
const Expert_MudSport = (ctx) => mudWaterSport(ctx, 'electric');
const Expert_WaterSport = (ctx) => mudWaterSport(ctx, 'fire');
function Expert_Overheat(ctx, id) {
	if (noDmg(ctx, id)) return add(ctx, -1);
	if (slower(ctx)) {
		if (hpA(ctx) > 80) return;
		return add(ctx, -1);
	}
	if (hpA(ctx) > 60) return;
	add(ctx, -1);
}
function Expert_DragonDance(ctx) {
	if (slower(ctx)) {
		if (!rnd(ctx, 128)) add(ctx, 1);
		return;
	}
	if (hpA(ctx) > 50) return;
	if (!rnd(ctx, 70)) add(ctx, -1);
}
function Expert_Gravity(ctx) {
	const TryPlus1 = () => { if (!rnd(ctx, 64)) add(ctx, 1); };
	const d = ctx.foeActive;
	if (ctx.abilityOf(d) === 'levitate') return TryPlus1();
	if (hasMoveEffect(ctx, 'def', 'MAGNET_RISE')) return TryPlus1();
	if (typesOf(ctx, d).includes('flying')) return TryPlus1();
	if (hpA(ctx) < 60) return;
	if (rnd(ctx, 128)) return TryPlus1();
}
function Expert_MiracleEye(ctx) {
	const Gate = () => { if (rnd(ctx, 80)) return; if (!rnd(ctx, 80)) add(ctx, 2); };
	if (typesOf(ctx, ctx.foeActive).includes('dark')) return Gate();
	if (stg(ctx, 'def', 'evasion') > 8) {
		if (!rnd(ctx, 80)) add(ctx, 2);
		return;
	}
	add(ctx, -2);
}
function Expert_WakeUpSlap(ctx, id) {
	if (noDmg(ctx, id)) return add(ctx, -1);
	if (SLEEP(ctx.foeActive)) add(ctx, 1);
}
function Expert_HammerArm(ctx, id) {
	if (noDmg(ctx, id)) return add(ctx, -1);
	if (slower(ctx)) add(ctx, 1);
}
const Expert_GyroBall = () => {}; // no score changes
function Expert_Brine(ctx, id) {
	if (noDmg(ctx, id)) return add(ctx, -1);
	if (hpD(ctx) > 50) return;
	add(ctx, 1);
	if (!rnd(ctx, 128)) add(ctx, 1);
}
function Expert_Feint(ctx) {
	const CheckChain = () => {
		const c = protectChain(ctx, 'def');
		if (c === 0) { if (!rnd(ctx, 128)) add(ctx, 1); return; }
		if (c === 1) { if (!rnd(ctx, 192)) add(ctx, 1); return; }
		if (c > 2) return add(ctx, -2);
		if (!rnd(ctx, 128)) add(ctx, 1); // chain == 2 falls into the chain-0 branch (quirk)
	};
	const TryPlus1 = () => { if (rnd(ctx, 128)) return CheckChain(); add(ctx, 1); CheckChain(); };
	const CheckConditions = () => {
		if (TOXIC(ctx.active) || hasVolatile(ctx, 'atk', 'CURSE') || hasMoveEffect(ctx, 'atk', 'PERISH_SONG') ||
			hasVolatile(ctx, 'atk', 'ATTRACT') || hasMoveEffect(ctx, 'atk', 'LEECH_SEED') ||
			hasMoveEffect(ctx, 'atk', 'YAWN')) return TryPlus1();
		if (hpD(ctx) === 100) return CheckChain();
		const h = holdOf(ctx, ctx.foeActive);
		if (h !== 'hprestoregradual' && h !== 'hprestorepsntype') return CheckChain();
		TryPlus1();
	};
	if (!knowsEffect(ctx, 'def', E('PROTECT'))) {
		if (rnd(ctx, 64)) return; // 75% flat +0
	}
	CheckConditions();
}
function Expert_Pluck(ctx, id) {
	const TryPlus1 = () => { if (!rnd(ctx, 128)) add(ctx, 1); };
	if (noDmg(ctx, id)) return add(ctx, -1);
	if (!firstTurnInBattle(ctx, ctx.active)) return TryPlus1();
	if (!rnd(ctx, 64)) { add(ctx, 1); return; }
	TryPlus1();
}
function Expert_Tailwind(ctx) {
	if (rnd(ctx, 64)) return; // 25% flat +0
	if (faster(ctx)) return add(ctx, -1);
	if (hpA(ctx) < 31) return add(ctx, -1);
	if (hpA(ctx) > 75) return add(ctx, 1);
	if (rnd(ctx, 64)) return;
	add(ctx, 1);
}
function Expert_Acupressure(ctx) {
	const TryPlus1 = () => { if (!rnd(ctx, 64)) add(ctx, 1); };
	if (hpA(ctx) < 51) return add(ctx, -1);
	if (hpA(ctx) > 90) return TryPlus1();
	if (rnd(ctx, 128)) return; // IfRandomLessThan 128, End
	TryPlus1();
}
function Expert_MetalBurst(ctx) {
	const d = ctx.foeActive;
	if (SLEEP(d) || hasVolatile(ctx, 'def', 'ATTRACT') || hasVolatile(ctx, 'def', 'CONFUSION')) return add(ctx, -1);
	if (knowsEffect(ctx, 'def', E('DOUBLE_POWER_IF_HIT')) ||
		knowsEffect(ctx, 'def', E('HIT_LAST_WHIFF_IF_HIT')) ||
		knowsEffect(ctx, 'def', E('PRIORITY_NEG_1_BYPASS_ACCURACY'))) return add(ctx, -1);
	const TryPlus1 = () => {
		if (!taunted(d)) return;
		if (!rnd(ctx, 100)) add(ctx, 1);
	};
	if (hpA(ctx) <= 30) { if (!rnd(ctx, 10)) add(ctx, -1); }
	if (hpA(ctx) <= 50) { if (!rnd(ctx, 100)) add(ctx, -1); }
	if (!rnd(ctx, 192)) add(ctx, 1); // HighHPTryPlus1 reached by fallthrough/jump from both branches
	const lm = lastMoveData(ctx);
	if (lm.power === 0) return TryPlus1();
	if (!taunted(d)) return TryPlus1();
	if (rnd(ctx, 100)) return TryPlus1(); // jump fires when roll < 100
	add(ctx, 1); // AddToMoveScore 1, then falls into TryScorePlus1 label
	TryPlus1();
}
function Expert_UTurn(ctx, id) {
	const P50 = () => { if (rnd(ctx, 128)) return CheckSpeed(); add(ctx, 1); CheckSpeed(); };
	const CheckSpeed = () => {
		if (faster(ctx)) return add(ctx, 1);
		if (rnd(ctx, 128)) return;
		add(ctx, 1);
	};
	const CheckTargetHP = () => {
		if (hpD(ctx) > 70) { if (rnd(ctx, 64)) return P50(); add(ctx, 1); return P50(); }
		if (hpD(ctx) > 30) return P50();
		if (rnd(ctx, 128)) return CheckSpeed(); // jump fires when roll < 128
		P50();
	};
	const CheckPartyDamage = () => {
		if (partyDealsMore(ctx)) return CheckTargetHP();
		if (rnd(ctx, 64)) return CheckTargetHP();
		add(ctx, -2); // GoTo End
	};
	if (noDmg(ctx, id)) return add(ctx, -1);
	if (aliveParty(ctx, 'atk').length === 0) return; // EXPERT-DEV 14: End, no score
	if (ctx.engine.aiHasSuperEffectiveMove(ctx.active, ctx.foeActive, true)) {
		if (!rnd(ctx, 64)) add(ctx, -2);
	}
	CheckPartyDamage();
}
function Expert_CloseCombat(ctx, id) {
	if (noDmg(ctx, id)) return add(ctx, -1);
	if (slower(ctx)) {
		if (hpA(ctx) > 80) return;
		return add(ctx, -1);
	}
	if (hpA(ctx) > 60) return;
	add(ctx, -1);
}
function Expert_Payback(ctx, id) {
	if (noDmg(ctx, id)) return add(ctx, -1);
	if (faster(ctx)) return;
	if (hpA(ctx) < 30) return;
	if (!rnd(ctx, 64)) add(ctx, 1);
}
function Expert_Assurance(ctx, id) {
	const TryPlus1 = () => { if (!rnd(ctx, 128)) add(ctx, 1); };
	if (noDmg(ctx, id)) return add(ctx, -1);
	if (faster(ctx)) return;
	if (ctx.abilityOf(ctx.active) === 'roughskin') return TryPlus1();
	// EXPERT-DEV 11: recoil-berry table compares hold effect vs ITEM_* constants → dead
	if (rnd(ctx, 128)) return TryPlus1();
}
const Expert_Embargo = (ctx) => { if (!rnd(ctx, 128)) add(ctx, 1); };
const FLING_GOOD_HOLDS = new Set(['sometimesflinch', 'strengthenpoison', 'psnuser', 'brnuser', 'pikaspatkup']);
function Expert_Fling(ctx, id) {
	const TryPlus1 = () => { if (!rnd(ctx, 64)) add(ctx, 1); };
	const CheckWeakness = () => {
		const c = clsNow(ctx, id);
		if (c === 80 || c === 160) { add(ctx, 4); return TryPlus1(); }
		if (!rnd(ctx, 128)) { add(ctx, 1); return TryPlus1(); }
		TryPlus1();
	};
	if (noDmg(ctx, id)) {
		if (!FLING_GOOD_HOLDS.has(holdOf(ctx, ctx.active))) add(ctx, -1);
		return;
	}
	const p = (ctx.itemRec(ctx.active.item || '') || {}).flingPower || 0;
	if (p < 30) return add(ctx, -2);
	if (p > 90) return CheckWeakness();
	if (p > 60) return TryPlus1();
	if (!rnd(ctx, 128)) add(ctx, -1);
}
function Expert_PsychoShift(ctx) {
	if (!ANYSTATUS(ctx.active)) return add(ctx, -10); // global ScoreMinus10
	if (rnd(ctx, 128)) return;
	if (hpD(ctx) < 30) return;
	add(ctx, 1);
}
function Expert_TrumpCard(ctx, id) {
	const Plus1 = () => { if (!rnd(ctx, 100)) add(ctx, 1); };
	const Maybe2 = () => { add(ctx, 1); Plus1(); };
	if (noDmg(ctx, id)) return add(ctx, -1);
	const pp = (ctx.moves[ctx.slot] || {}).pp || 0;
	if (pp === 1) return add(ctx, 3);
	if (pp === 2) return Maybe2();
	if (pp === 3) return Plus1();
	const CheckStats = () => {
		if (stg(ctx, 'def', 'evasion') > 10) return Maybe2();
		if (stg(ctx, 'atk', 'accuracy') < 2) return Maybe2();
		if (stg(ctx, 'def', 'evasion') > 8) return Plus1();
		if (stg(ctx, 'atk', 'accuracy') < 4) return Plus1();
	};
	if (ctx.abilityOf(ctx.foeActive) === 'pressure') {
		if (!rnd(ctx, 30)) add(ctx, 1);
	}
	CheckStats();
}
function Expert_HealBlock(ctx) {
	const TryPlus1 = () => { if (!rnd(ctx, 25)) add(ctx, 1); };
	const effs = ['RECOVER_DAMAGE_SLEEP', 'RESTORE_HALF_HP', 'HEAL_HALF_REMOVE_FLYING_TYPE',
		'UNUSED_157', 'HEAL_HALF_MORE_IN_SUN', 'REST', 'SWALLOW', 'RECOVER_HALF_DAMAGE_DEALT',
		'GROUND_TRAP_USER_CONTINUOUS_HEAL', 'RESTORE_HP_EVERY_TURN', 'STATUS_LEECH_SEED',
		'FAINT_AND_FULL_HEAL_NEXT_MON', 'FAINT_FULL_RESTORE_NEXT_MON'].map(E);
	for (const e of effs) if (knowsEffect(ctx, 'def', e)) return TryPlus1();
	if (hasMoveEffect(ctx, 'atk', 'LEECH_SEED') || hasMoveEffect(ctx, 'def', 'AQUA_RING') ||
		hasMoveEffect(ctx, 'def', 'INGRAIN')) return TryPlus1();
	if (rnd(ctx, 96)) return TryPlus1();
}
function Expert_WringOut(ctx, id) {
	const TryPlus1 = () => { if (!rnd(ctx, 25)) add(ctx, 1); };
	if (noDmg(ctx, id)) return add(ctx, -1);
	if (hpD(ctx) < 50) return add(ctx, -1);
	if (hpD(ctx) === 100) {
		if (!slower(ctx)) add(ctx, 1); // faster: +1 then falls into ScorePlus1
		add(ctx, 1);
		return TryPlus1();
	}
	if (hpD(ctx) > 85) return TryPlus1();
}
function Expert_PowerTrick(ctx) {
	if (hpA(ctx) > 90) { if (!rnd(ctx, 96)) add(ctx, 1); return; }
	if (hpA(ctx) > 60) { if (!rnd(ctx, 128)) add(ctx, 1); return; }
	if (hpA(ctx) > 30) { if (!rnd(ctx, 164)) add(ctx, 1); return; }
	add(ctx, -2); // global ScoreMinus2
}
function Expert_GastroAcid(ctx) {
	const ContinueCheck = () => {
		if (hpD(ctx) > 50) return;
		add(ctx, -1);
		if (hpD(ctx) > 30) return;
		add(ctx, -1);
	};
	if (rnd(ctx, 64)) return; // 25% flat +0
	add(ctx, 1);
	if (hpD(ctx) > 70) return;
	if (!rnd(ctx, 128)) add(ctx, -1); // roll ≥128 applies −1, then falls into ContinueHPCheck
	ContinueCheck();
}
function Expert_LuckyChant(ctx) {
	if (hpA(ctx) < 70) return add(ctx, -1);
	const effs = [E('HIGH_CRITICAL'), E('HIGH_CRITICAL_BURN_HIT'), E('HIGH_CRITICAL_POISON_HIT')];
	for (const e of effs) if (knowsEffect(ctx, 'def', e)) { add(ctx, 1); return; }
	if (rnd(ctx, 64)) add(ctx, 1); // 25% +1 (script: jumps to ScorePlus1 when the roll fires)
}
function Expert_MeFirst(ctx) {
	const TryPlus1AndEnd = () => { if (!rnd(ctx, 64)) add(ctx, 1); };
	if (slower(ctx)) return add(ctx, -2);
	// decomp jumps to the +1 roll when the DEFENDER outscores the attacker — code wins
	if (foeDealsMore(ctx)) { if (!rnd(ctx, 32)) add(ctx, 1); return CheckLast(); }
	CheckLast();
	function CheckLast() {
		if (lastMoveData(ctx).cls === 'Status') return TryPlus1AndEnd();
		if (!rnd(ctx, 128)) add(ctx, 1);
	}
}
function Expert_Copycat(ctx) {
	const inTable = () => { const lm = lastId(ctx, 'def'); return !!lm && MOVE_TABLE_MIRROR.has(lm); };
	const CheckEncouraged = () => {
		if (foeDealsMore(ctx)) return;
		if (inTable()) return;
		if (!rnd(ctx, 80)) add(ctx, -1);
	};
	if (slower(ctx)) return CheckEncouraged();
	if (foeDealsMore(ctx)) { // decomp jumps here when the DEFENDER outscores (code wins)
		if (!rnd(ctx, 32)) add(ctx, 2);
		return;
	}
	if (inTable()) {
		if (!rnd(ctx, 128)) add(ctx, 2);
		return;
	}
	CheckEncouraged();
}
// Power/GuardSwap cascade: 50% +L, 25% +(L−1), … (each label GoTo End)
function swapCascade(ctx, level) {
	for (let n = level; n >= 1; n--) {
		if (!rnd(ctx, 128)) { add(ctx, n); return; }
	}
}
function swapMove(ctx, atkStat, spStat) {
	const dA = diffStages(ctx, atkStat);
	const dS = diffStages(ctx, spStat);
	let level = 0;
	if (dA > 3) level = dS > 3 ? 5 : dS > 1 ? 4 : dS === 0 ? 3 : 0;
	else if (dA > 1) level = dS > 3 ? 4 : dS > 1 ? 3 : dS === 0 ? 2 : 0;
	else if (dA > 0) level = dS > 3 ? 3 : dS > 1 ? 2 : dS === 0 ? 1 : 0;
	else if (dA === 0) level = dS > 3 ? 3 : dS > 1 ? 2 : dS > 0 ? 1 : 0;
	swapCascade(ctx, level);
}
const Expert_PowerSwap = (ctx) => swapMove(ctx, 'atk', 'spa');
const Expert_GuardSwap = (ctx) => swapMove(ctx, 'def', 'spd');
function Expert_Punishment(ctx, id) {
	// Punishment labels ACCUMULATE on fallthrough (no GoTo between tiers)
	if (noDmg(ctx, id)) return;
	const s = ['atk', 'def', 'spa', 'spd', 'spe', 'accuracy', 'evasion']
		.reduce((a, k) => a + Math.max(0, (ctx.foeActive.boosts && ctx.foeActive.boosts[k]) || 0), 0);
	let level = 0;
	if (s > 6) level = 4;
	else if (s > 5) level = 3;
	else if (s > 4) level = 2;
	else if (s > 3) level = 1;
	else if (s > 2) level = 1;
	for (let n = level; n >= 1; n--) {
		if (rnd(ctx, 128)) continue; // tier skipped
		add(ctx, n);
	}
}
function Expert_LastResort(ctx, id) {
	if (noDmg(ctx, id)) return add(ctx, -1);
	if (canUseLastResort(ctx)) add(ctx, 1);
}
function Expert_WorrySeed(ctx) {
	const TryPlus1 = () => { if (!rnd(ctx, 64)) add(ctx, 1); };
	if (knows(ctx, 'def', 'rest')) add(ctx, 1);
	if (hpA(ctx) >= 50 && !rnd(ctx, 128)) add(ctx, 1);
	TryPlus1();
}
function Expert_SuckerPunch(ctx, id) {
	if (noDmg(ctx, id)) return add(ctx, -1);
	if (!rnd(ctx, 64)) add(ctx, 1);
}
function Expert_HeartSwap(ctx) {
	const d = ctx.foeActive;
	const foeGood = ['atk', 'def', 'spa', 'spd', 'evasion'].some((k) => stg(ctx, 'def', k) > 7) ||
		hasVolatile(ctx, 'def', 'FOCUS_ENERGY');
	if (!foeGood) return add(ctx, -2);
	const lo = (k) => stg(ctx, 'atk', k) < 7;
	if (lo('atk') || lo('def') || lo('spa') || lo('spd')) return add(ctx, 1);
	if (lo('evasion')) return add(ctx, 2); // ScorePlus2 adds 1 then falls into ScorePlus1 (+1)
	if (!hasVolatile(ctx, 'atk', 'FOCUS_ENERGY')) return add(ctx, 1);
	if (rnd(ctx, 50)) return;
	add(ctx, -2);
}
function Expert_AquaRing(ctx) {
	if (hpA(ctx) < 30) return;
	if (!rnd(ctx, 128)) add(ctx, 1);
}
function Expert_MagnetRise(ctx) {
	if (hpA(ctx) < 50) return; // ignore all further changes
	if (knows(ctx, 'def', 'earthquake') || knows(ctx, 'def', 'earthpower') || knows(ctx, 'def', 'fissure')) add(ctx, 1);
	if (typesOf(ctx, ctx.foeActive).includes('ground')) return add(ctx, 1);
	if (!rnd(ctx, 128)) add(ctx, 1);
}
function Expert_Defog(ctx) {
	const TryMinus1 = () => { if (!rnd(ctx, 128)) add(ctx, -1); CheckUserEva(); };
	const CheckOppHP = () => { if (hpD(ctx) <= 70) add(ctx, -2); };
	const TryMinus2 = () => { if (!rnd(ctx, 50)) add(ctx, -2); CheckOppHP(); };
	const CheckUserEva = () => {
		if (hpA(ctx) < 70) return TryMinus2();
		if (stg(ctx, 'def', 'evasion') > 3) return CheckOppHP();
		TryMinus2();
	};
	const ScrubHazards = () => {
		add(ctx, 1);
		if (aliveParty(ctx, 'def').length === 0) return;
		if (sideCond(ctx, 'def', 'SPIKES') || sideCond(ctx, 'def', 'STEALTH_ROCK') || sideCond(ctx, 'def', 'TOXIC_SPIKES')) return TryMinus1();
		CheckUserEva();
	};
	if (sideCond(ctx, 'def', 'LIGHT_SCREEN') || sideCond(ctx, 'def', 'REFLECT')) {
		if (hpA(ctx) <= 30 && aliveParty(ctx, 'atk').length === 0) return TryMinus2();
		return ScrubHazards();
	}
	if (sideCond(ctx, 'def', 'SPIKES') || sideCond(ctx, 'def', 'STEALTH_ROCK') || sideCond(ctx, 'def', 'TOXIC_SPIKES')) {
		add(ctx, -2);
		return CheckUserEva();
	}
	CheckUserEva();
}
function Expert_TrickRoom(ctx) {
	if (isDoubles(ctx)) return;
	if (hpA(ctx) <= 30 && aliveParty(ctx, 'atk').length === 0) return;
	if (slower(ctx)) {
		if (!rnd(ctx, 64)) add(ctx, 3);
		return;
	}
	add(ctx, -1);
}
function Expert_Blizzard(ctx, id) {
	if (noDmg(ctx, id)) {
		if (!rnd(ctx, 50)) add(ctx, -3);
		return;
	}
	if (wx(ctx) !== 'HAILING') return;
	add(ctx, 1);
}
function Expert_Captivate(ctx) {
	const CheckOppHP = () => {
		if (hpD(ctx) > 70) return CheckLastMove();
		add(ctx, -2);
		CheckLastMove();
	};
	const CheckLastMove = () => {
		if (lastMoveData(ctx).cls !== 'Physical') return;
		if (!rnd(ctx, 64)) add(ctx, -1);
	};
	if (stg(ctx, 'def', 'spa') === 6) return CheckOppHP();
	add(ctx, -1);
	if (hpA(ctx) <= 90) add(ctx, -1);
	if (stg(ctx, 'def', 'spa') > 3) return CheckOppHP();
	if (!rnd(ctx, 50)) return CheckOppHP();
	add(ctx, -2);
	CheckOppHP();
}
function Expert_RecoilMove(ctx, id) {
	if (noDmg(ctx, id)) return; // ignore all further modifiers
	const ab = ctx.abilityOf(ctx.active);
	if (ab === 'rockhead' || ab === 'magicguard') add(ctx, 1);
}
function Expert_HealingWish(ctx) {
	const LowHP = () => {
		if (hpA(ctx) > 30) return;
		if (!rnd(ctx, 128)) add(ctx, 1);
	};
	const TryMinus1 = () => { if (!rnd(ctx, 50)) add(ctx, -1); };
	const HappyPath = () => {
		if (hpA(ctx) > 50) return TryMinus1();
		if (rnd(ctx, 192)) return LowHP();
		add(ctx, 1);
		const CheckParty = () => {
			if (partyDealsMore(ctx)) {
				if (!rnd(ctx, 128)) add(ctx, 1);
				return LowHP();
			}
			LowHP();
		};
		if (ctx.engine.aiHasSuperEffectiveMove(ctx.active, ctx.foeActive, true)) return CheckParty();
		if (rnd(ctx, 192)) return CheckParty();
		add(ctx, 1);
		CheckParty();
	};
	if (hpA(ctx) >= 80 && !slower(ctx)) {
		if (rnd(ctx, 192)) return;
		return add(ctx, -5); // global ScoreMinus5
	}
	HappyPath();
}

// ===========================================================================
// Jump table (script.s:1628-1805, exact order; first match wins)
// ===========================================================================
const DISPATCH = [
	[E('STATUS_SLEEP'), Expert_StatusSleep],
	[E('RECOVER_HALF_DAMAGE_DEALT'), Expert_DrainMove],
	[E('HALVE_DEFENSE'), Expert_Explosion],
	[E('RECOVER_DAMAGE_SLEEP'), Expert_DreamEater],
	[E('COPY_MOVE'), Expert_MirrorMove],
	[E('ATK_UP'), Expert_StatusAttackUp],
	[E('DEF_UP'), Expert_StatusDefenseUp],
	[E('SPEED_UP'), Expert_StatusSpeedUp],
	[E('SP_ATK_UP'), Expert_StatusSpAttackUp],
	[E('SP_DEF_UP'), Expert_StatusSpDefenseUp],
	[E('ACC_UP'), Expert_StatusAccuracyUp],
	[E('EVA_UP'), Expert_StatusEvasionUp],
	[E('BYPASS_ACCURACY'), Expert_BypassAccuracyMove],
	[E('ATK_DOWN'), Expert_StatusAttackDown],
	[E('DEF_DOWN'), Expert_StatusDefenseDown],
	[E('SPEED_DOWN'), Expert_StatusSpeedDown],
	[E('SP_ATK_DOWN'), Expert_StatusSpAttackDown],
	[E('SP_DEF_DOWN'), Expert_StatusSpDefenseDown],
	[E('ACC_DOWN'), Expert_StatusAccuracyDown],
	[E('EVA_DOWN'), Expert_StatusEvasionDown],
	[E('RESET_STAT_CHANGES'), Expert_Haze],
	[E('BIDE'), Expert_Bide],
	[E('FORCE_SWITCH'), Expert_ForceSwitch],
	[E('CONVERSION'), Expert_Conversion],
	[E('RESTORE_HALF_HP'), Expert_Recovery],
	[E('STATUS_BADLY_POISON'), Expert_ToxicLeechSeed],
	[E('SET_LIGHT_SCREEN'), Expert_LightScreen],
	[E('REST'), Expert_Rest],
	[E('ONE_HIT_KO'), Expert_OHKOMove],
	[E('CHARGE_TURN_HIGH_CRIT'), Expert_ChargeTurnNoInvuln],
	[E('HALVE_HP'), Expert_SuperFang],
	[E('BIND_HIT'), Expert_BindingMove],
	[E('HIGH_CRITICAL'), Expert_HighCritical],
	[E('RECOIL_QUARTER'), Expert_RecoilMove],
	[E('STATUS_CONFUSE'), Expert_StatusConfuse],
	[E('ATK_UP_2'), Expert_StatusAttackUp],
	[E('DEF_UP_2'), Expert_StatusDefenseUp],
	[E('SPEED_UP_2'), Expert_StatusSpeedUp],
	[E('SP_ATK_UP_2'), Expert_StatusSpAttackUp],
	[E('SP_DEF_UP_2'), Expert_StatusSpDefenseUp],
	[E('ACC_UP_2'), Expert_StatusAccuracyUp],
	[E('EVA_UP_2'), Expert_StatusEvasionUp],
	[E('ATK_DOWN_2'), Expert_StatusAttackDown],
	[E('DEF_DOWN_2'), Expert_StatusDefenseDown],
	[E('SPEED_DOWN_2'), Expert_StatusSpeedDown],
	[E('SP_ATK_DOWN_2'), Expert_StatusSpAttackDown],
	[E('SP_DEF_DOWN_2'), Expert_StatusSpDefenseDown],
	[E('EVA_DOWN_2'), Expert_StatusAccuracyDown], // cross-wired in decomp — code wins
	[E('ACC_DOWN_2'), Expert_StatusEvasionDown],  // cross-wired in decomp — code wins
	[E('SET_REFLECT'), Expert_Reflect],
	[E('STATUS_POISON'), Expert_StatusPoison],
	[E('STATUS_PARALYZE'), Expert_StatusParalyze],
	[E('ATK_UP_2_STATUS_CONFUSION'), Expert_Swagger],
	[E('LOWER_SPEED_HIT'), Expert_SpeedDownOnHit],
	[E('CHARGE_TURN_HIGH_CRIT_FLINCH'), Expert_ChargeTurnNoInvuln],
	[E('PRIORITY_NEG_1_BYPASS_ACCURACY'), Expert_VitalThrow],
	[E('SET_SUBSTITUTE'), Expert_Substitute],
	[E('RECHARGE_AFTER'), Expert_RechargeTurn],
	[E('STATUS_LEECH_SEED'), Expert_ToxicLeechSeed],
	[E('DISABLE'), Expert_Disable],
	[E('COUNTER'), Expert_Counter],
	[E('ENCORE'), Expert_Encore],
	[E('AVERAGE_HP'), Expert_PainSplit],
	[E('DAMAGE_WHILE_ASLEEP'), Expert_Nightmare],
	[E('NEXT_ATTACK_ALWAYS_HITS'), Expert_LockOn],
	[E('USE_RANDOM_LEARNED_MOVE_SLEEP'), Expert_SleepTalk],
	[E('KO_MON_THAT_DEFEATED_USER'), Expert_DestinyBond],
	[E('INCREASE_POWER_WITH_LESS_HP'), Expert_Reversal],
	[E('CURE_PARTY_STATUS'), Expert_HealBell],
	[E('STEAL_HELD_ITEM'), Expert_Thief],
	[E('PREVENT_ESCAPE'), Expert_BindingMove],
	[E('EVA_UP_2_MINIMIZE'), Expert_StatusEvasionUp],
	[E('CURSE'), Expert_Curse],
	[E('PROTECT'), Expert_Protect],
	[E('SET_SPIKES'), Expert_Spikes],
	[E('FORESIGHT'), Expert_Foresight],
	[E('SURVIVE_WITH_1_HP'), Expert_Endure],
	[E('PASS_STATS_AND_STATUS'), Expert_BatonPass],
	[E('HIT_BEFORE_SWITCH'), Expert_Pursuit],
	[E('HEAL_HALF_MORE_IN_SUN'), Expert_Synthesis],
	[E('UNUSED_133'), Expert_Synthesis],
	[E('UNUSED_134'), Expert_Synthesis],
	[E('WEATHER_RAIN'), Expert_RainDance],
	[E('WEATHER_SUN'), Expert_SunnyDay],
	[E('MAX_ATK_LOSE_HALF_MAX_HP'), Expert_BellyDrum],
	[E('COPY_STAT_CHANGES'), Expert_PsychUp],
	[E('MIRROR_COAT'), Expert_MirrorCoat],
	[E('CHARGE_TURN_DEF_UP'), Expert_ChargeTurnNoInvuln],
	[E('SKIP_CHARGE_TURN_IN_SUN'), Expert_ChargeTurnNoInvuln],
	[E('SKIP_CHARGE_TURN_IN_SUN'), Expert_UnusedSolarbeam], // dead: duplicate key
	[E('FLY'), Expert_ChargeTurnWithInvuln],
	[E('UNUSED_157'), Expert_Recovery],
	[E('ALWAYS_FLINCH_FIRST_TURN_ONLY'), Expert_FakeOut],
	[E('SPIT_UP'), Expert_SpitUp],
	[E('SWALLOW'), Expert_Recovery],
	[E('WEATHER_HAIL'), Expert_Hail],
	[E('SP_ATK_UP_CAUSE_CONFUSION'), Expert_Flatter],
	[E('FAINT_AND_ATK_SP_ATK_DOWN_2'), Expert_Explosion],
	[E('DOUBLE_POWER_WHEN_STATUSED'), Expert_Facade],
	[E('HIT_LAST_WHIFF_IF_HIT'), Expert_FocusPunch],
	[E('DOUBLE_POWER_AND_CURE_PARALYSIS'), Expert_SmellingSalts],
	[E('SWITCH_HELD_ITEMS'), Expert_Trick],
	[E('COPY_ABILITY'), Expert_ChangeUserAbility],
	[E('GROUND_TRAP_USER_CONTINUOUS_HEAL'), Expert_Ingrain],
	[E('LOWER_OWN_ATK_AND_DEF'), Expert_Superpower],
	[E('APPLY_MAGIC_COAT'), Expert_MagicCoat],
	[E('RECYCLE'), Expert_Recycle],
	[E('DOUBLE_POWER_IF_HIT'), Expert_Revenge],
	[E('REMOVE_SCREENS'), Expert_BrickBreak],
	[E('REMOVE_HELD_ITEM'), Expert_KnockOff],
	[E('SET_HP_EQUAL_TO_USER'), Expert_Endeavor],
	[E('DECREASE_POWER_WITH_LESS_USER_HP'), Expert_WaterSpout],
	[E('SWITCH_ABILITIES'), Expert_ChangeUserAbility],
	[E('MAKE_SHARED_MOVES_UNUSEABLE'), Expert_Imprison],
	[E('HEAL_STATUS'), Expert_Refresh],
	[E('STEAL_STATUS_MOVE'), Expert_Snatch],
	[E('RECOIL_THIRD'), Expert_RecoilMove],
	[E('HIGH_CRITICAL_BURN_HIT'), Expert_HighCritical],
	[E('HALVE_ELECTRIC_DAMAGE'), Expert_MudSport],
	[E('USER_SP_ATK_DOWN_2'), Expert_Overheat],
	[E('ATK_DEF_DOWN'), Expert_StatusDefenseDown],
	[E('DEF_SPD_UP'), Expert_StatusSpDefenseUp],
	[E('ATK_DEF_UP'), Expert_StatusDefenseUp],
	[E('HIGH_CRITICAL_POISON_HIT'), Expert_HighCritical],
	[E('HALVE_FIRE_DAMAGE'), Expert_WaterSport],
	[E('SP_ATK_SP_DEF_UP'), Expert_StatusSpDefenseUp],
	[E('ATK_SPD_UP'), Expert_DragonDance],
	[E('HEAL_HALF_REMOVE_FLYING_TYPE'), Expert_Recovery],
	[E('GRAVITY'), Expert_Gravity],
	[E('IGNORE_EVATION_REMOVE_DARK_IMMUNE'), Expert_MiracleEye],
	[E('DOUBLE_POWER_HEAL_SLEEP'), Expert_WakeUpSlap],
	[E('SPEED_DOWN_HIT'), Expert_HammerArm],
	[E('POWER_BASED_ON_LOW_SPEED'), Expert_GyroBall],
	[E('FAINT_AND_FULL_HEAL_NEXT_MON'), Expert_HealingWish],
	[E('DOUBLE_POWER_WHEN_BELOW_HALF'), Expert_Brine],
	[E('REMOVE_PROTECT'), Expert_Feint],
	[E('EAT_BERRY'), Expert_Pluck],
	[E('DOUBLE_SPEED_3_TURNS'), Expert_Tailwind],
	[E('RANDOM_STAT_UP_2'), Expert_Acupressure],
	[E('METAL_BURST'), Expert_MetalBurst],
	[E('SWITCH_HIT'), Expert_UTurn],
	[E('DEF_SPD_DOWN_HIT'), Expert_CloseCombat],
	[E('DOUBLE_POWER_IF_MOVING_SECOND'), Expert_Payback],
	[E('DOUBLE_POWER_IF_TARGET_HIT'), Expert_Assurance],
	[E('PREVENT_ITEM_USE'), Expert_Embargo],
	[E('FLING'), Expert_Fling],
	[E('TRANSFER_STATUS'), Expert_PsychoShift],
	[E('HIGHER_POWER_WHEN_LOW_PP'), Expert_TrumpCard],
	[E('PREVENT_HEALING'), Expert_HealBlock],
	[E('INCREASE_POWER_WITH_MORE_HP'), Expert_WringOut],
	[E('SWAP_ATK_DEF'), Expert_PowerTrick],
	[E('SUPRESS_ABILITY'), Expert_GastroAcid],
	[E('PREVENT_CRITS'), Expert_LuckyChant],
	[E('USE_MOVE_FIRST'), Expert_MeFirst],
	[E('USE_LAST_USED_MOVE'), Expert_Copycat],
	[E('SWAP_ATK_SP_ATK_STAT_CHANGES'), Expert_PowerSwap],
	[E('SWAP_DEF_SP_DEF_STAT_CHANGES'), Expert_GuardSwap],
	[E('INCREASE_POWER_WITH_MORE_STAT_UP'), Expert_Punishment],
	[E('FAIL_IF_NOT_USED_ALL_OTHER_MOVES'), Expert_LastResort],
	[E('SET_ABILITY_TO_INSOMNIA'), Expert_WorrySeed],
	[E('HIT_FIRST_IF_TARGET_ATTACKING'), Expert_SuckerPunch],
	[E('TOXIC_SPIKES'), Expert_ToxicSpikes],
	[E('SWAP_STAT_CHANGES'), Expert_HeartSwap],
	[E('RESTORE_HP_EVERY_TURN'), Expert_AquaRing],
	[E('GIVE_GROUND_IMMUNITY'), Expert_MagnetRise],
	[E('RECOIL_BURN_HIT'), Expert_RecoilMove],
	[E('DIVE'), Expert_ChargeTurnWithInvuln],
	[E('DIG'), Expert_ChargeTurnWithInvuln],
	[E('REMOVE_HAZARDS_SCREENS_EVA_DOWN'), Expert_Defog],
	[E('TRICK_ROOM'), Expert_TrickRoom],
	[E('BLIZZARD'), Expert_Blizzard],
	[E('RECOIL_PARALYZE_HIT'), Expert_RecoilMove],
	[E('BOUNCE'), Expert_ChargeTurnWithInvuln],
	[E('SP_ATK_DOWN_2_OPPOSITE_GENDER'), Expert_Captivate],
	[E('STEALTH_ROCK'), Expert_StealthRock],
	[E('RECOIL_HALF'), Expert_RecoilMove],
	[E('FAINT_FULL_RESTORE_NEXT_MON'), Expert_HealingWish],
	[E('SHADOW_FORCE'), Expert_ShadowForce],
];

// SpeedDownOnHit: gate, then run StatusSpeedDown for the three tagged moves
function Expert_SpeedDownOnHit(ctx, id) {
	if (noDmg(ctx, id)) return;
	if (id === 'icywind' || id === 'rocktomb' || id === 'mudshot') return Expert_StatusSpeedDown(ctx, id);
}
function Expert_StatusSpeedDown(ctx) {
	if (!slower(ctx)) return add(ctx, -3);
	if (!rnd(ctx, 70)) add(ctx, 2);
}

function decide(ctx) {
	// EXPERT-DEV 16: refresh module-local log scan (see scan() note above)
	scanSt = scan(ctx);
	// decomp 1624: IfTargetIsPartner Terminate — impossible in singles (EXPERT-DEV 3)
	ctx.eachSlot((i) => {
		const id = ctx.moves[i] && ctx.moves[i].id;
		if (!id) return;
		const e = ctx.effectOf(id);
		if (!e) return;
		for (let k = 0; k < DISPATCH.length; k++) {
			if (DISPATCH[k][0] === e) { DISPATCH[k][1](ctx, id); return; }
		}
	});
}

module.exports = { id: 'expert', bit: 2, decide };

