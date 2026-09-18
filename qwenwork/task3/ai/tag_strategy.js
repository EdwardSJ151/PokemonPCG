'use strict';
// AI_FLAG_TAG_STRATEGY — TagStrategy_Main (pokeplatinum script.s:6624-7102)
// + TagStrategy_Partner (7326-7681), spec §3 "TagStrategy_Main". Ported
// line-by-line from the script source; code beats comments/spec.
//
// ── What "doubles specialist" does NOT mean ─────────────────────────────
// The script is not gated on battle type: it runs in SINGLES too, reading
// the partner operands as battleMons[battler^2] — a ZEROED slot in a
// 2-player battle (curHP 0, maxHP 0 → hpPct 0 via ARM ÷0→0, ability NONE →
// CheckBattlerAbility result AI_UNKNOWN, both types TYPE_NORMAL=0, no
// moves/volatiles/items). AIScript_Battler (trainer_ai.c:~620) maps
// ATTACKER/DEFENDER_PARTNER to battler^2 unconditionally. Ported literally
// as partner = null with exactly those accessor semantics (hpPct null→0,
// abChk→'unknown', types→[normal]). Observable real-hardware singles
// consequences, all preserved here:
//   • Earthquake/Magnitude/Discharge/Surf/Lava Plume always take −3
//     (zeroed Normal partner hits the "else −3" leaf — the decomp's own
//     comment at script.s:7109 says a solo battler takes the −3);
//   • every non-Surf WATER move takes −1: the Storm Drain gate jumps only
//     on AI_NOT_HAVE and a zeroed partner reads AI_UNKNOWN (7283-7286);
//   • Trick Room always −30 (partner HP% == 0 gate, 7002-7004);
//   • Follow Me lands on the partner-<30% leaves (+3 at 75% / −5 at 75%);
//   • NVE/¼-score penalties can NEVER fire (the "target's partner is out"
//     gate passes trivially, 6647-6667);
//   • Helping-Hand / Future Sight / Skill-Swap-partner / Gravity-partner
//     branches degrade exactly as the zeroed reads dictate.
// Real partner data is used ONLY behind battle.battleType === 'doubles';
// The Partner subroutine additionally needs the engine to hand us an
// ally-targeted evaluation — buildCtx always pairs against the opposing
// active, so IfTargetIsPartner never fires through this harness (structural
// branch kept per task contract). Deviation there: CheckBattlerAbility
// reads TRUE abilities (the decomp's opponent-ability coin-flip guessing is
// replaced by the engine's real-ability convention → no guessing dice).
//
// Dice: IfRandomLessThan N == (RandNext() % 256) < N (trainer_ai.c:635-644)
// — the spec's "(N=x)" values are RAW 256 THRESHOLDS, e.g. "80.5% of +1
// (N=50)" is rand ≥ 50 → 206/256 = 80.47%. QUIRKS preserved: the Solar
// Power ≥50% branch falls through into its own −2 coin (script.s:6818-6824
// — +1 AND 50% of −2), ScoreMove's +1 paths SKIP the SE bonuses
// (GoTo CheckSpecialScoring), Hail's per-side checks short-circuit (first
// match only — the doc's "can stack" prose is wrong for same-side checks),
// Discharge's Ground check runs after Water/Flying (vanilla BUG, comment
// at script.s:7247), Lava Plume Dry Skin is −3 (comment claims +3).

// ── damage model (trainer_ai.c:30-58, 1119-1165) — local copies per the
// self-contained-module contract ─────────────────────────────────────────
const NO_DAMAGE_CALC = new Set([
	'BATTLE_EFFECT_HALVE_DEFENSE', 'BATTLE_EFFECT_RECOVER_DAMAGE_SLEEP',
	'BATTLE_EFFECT_CHARGE_TURN_HIGH_CRIT', 'BATTLE_EFFECT_CHARGE_TURN_HIGH_CRIT_FLINCH',
	'BATTLE_EFFECT_RECHARGE_AFTER', 'BATTLE_EFFECT_CHARGE_TURN_DEF_UP',
	'BATTLE_EFFECT_SKIP_CHARGE_TURN_IN_SUN', 'BATTLE_EFFECT_SPIT_UP',
	'BATTLE_EFFECT_HIT_LAST_WHIFF_IF_HIT', 'BATTLE_EFFECT_LOWER_OWN_ATK_AND_DEF',
	'BATTLE_EFFECT_DECREASE_POWER_WITH_LESS_USER_HP',
	'BATTLE_EFFECT_HIT_FIRST_IF_TARGET_ATTACKING', 'BATTLE_EFFECT_RECOIL_HALF',
]);
const ALT_POWER_CALC = new Set([
	'BATTLE_EFFECT_RANDOM_POWER_BASED_ON_IVS', 'BATTLE_EFFECT_POWER_BASED_ON_LOW_SPEED',
	'BATTLE_EFFECT_NATURAL_GIFT', 'BATTLE_EFFECT_JUDGEMENT', 'BATTLE_EFFECT_40_DAMAGE_FLAT',
	'BATTLE_EFFECT_LEVEL_DAMAGE_FLAT', 'BATTLE_EFFECT_RANDOM_DAMAGE_1_TO_150_LEVEL',
	'BATTLE_EFFECT_POWER_BASED_ON_FRIENDSHIP', 'BATTLE_EFFECT_POWER_BASED_ON_LOW_FRIENDSHIP',
	'BATTLE_EFFECT_20_DAMAGE_FLAT', 'BATTLE_EFFECT_INCREASE_POWER_WITH_WEIGHT',
]);
// The flat-damage family the script routes around the effectiveness gates.
const FLAT_DAMAGE = new Set([
	'BATTLE_EFFECT_ONE_HIT_KO', 'BATTLE_EFFECT_40_DAMAGE_FLAT',
	'BATTLE_EFFECT_LEVEL_DAMAGE_FLAT', 'BATTLE_EFFECT_RANDOM_DAMAGE_1_TO_150_LEVEL',
	'BATTLE_EFFECT_20_DAMAGE_FLAT',
]);
function comparisonMade(ctx, id) {
	const eff = ctx.effectOf(id);
	const m = ctx.move(id);
	return ALT_POWER_CALC.has(eff) || (!!m && m.basePower > 1 && !NO_DAMAGE_CALC.has(eff));
}

function add(ctx, v) {
	ctx.addScore(v); // AddToMoveScore: s8 add then clamp <0 → 0 (trainer_ai.c:683)
	if (ctx.scores[ctx.slot] < 0) ctx.scores[ctx.slot] = 0;
}
const hpPct = (p) => (p && p.maxhp ? Math.floor(p.hp * 100 / p.maxhp) : 0); // null = zeroed → 0
const norm = (s) => (s ? String(s).toLowerCase().replace(/[^a-z0-9]/g, '') : '');
const scrAb = (ctx, p) => (!p || ctx.engine.battle.suppressingAbility(p) ? '' : ctx.rawAbilityOf(p));
const abChk = (ctx, p, ab) => { // CheckBattlerAbility → AI_HAVE / AI_NOT_HAVE / AI_UNKNOWN
	const a = scrAb(ctx, p);
	return !a ? 'unknown' : a === ab ? 'have' : 'no';
};
const typesOf = (p) => (p ? p.getTypes().map(norm) : ['normal', 'normal']);
const hasType = (p, t) => typesOf(p).includes(t);
const knowsMove = (p, id) => !!p && p.hp > 0 && (p.moveSlots || []).some((ms) => ms && ms.id === id);
const statStage = (p, k) => (p ? p.boosts[k] + 6 : 6);

const PARTNER_ACCURACY_MOVES = ['fireblast', 'thunder', 'crosschop', 'hydropump', 'dynamicpunch',
	'blizzard', 'zapcannon', 'megahorn', 'focusblast', 'gunkshot', 'magmastorm', 'powerwhip',
	'seedflare', 'headsmash'];

module.exports = {
	id: 'tag_strategy',
	bit: 7,

	decide(ctx) {
		const doubles = ctx.battle.battleType === 'doubles';
		ctx.eachSlot(() => main(ctx, doubles));
	},
};

// ══ TagStrategy_Main ════════════════════════════════════════════════════
function main(ctx, doubles) {
	// IfTargetIsPartner TagStrategy_Partner (engine pairing: never in this
	// harness; kept as the structural doubles gate).
	if (doubles && ctx.foeActive && ctx.foeActive.side === ctx.side) {
		const p = ctx.side.active[1];
		if (p) partnerSubroutine(ctx, p);
		return;
	}
	const id = ctx.moves[ctx.slot].id;
	const eff = ctx.effectOf(id);
	const partner = doubles ? (ctx.side.active[1] || null) : null;        // attacker's partner
	const dpartner = doubles ? (ctx.foeActive.side.active[1] || null) : null; // defender's partner
	// FlagMoveDamageScore FALSE → non-damaging moves go straight to
	// CheckSpecialScoring.
	if (!comparisonMade(ctx, id)) return checkSpecialScoring(ctx, partner, dpartner, id, eff);
	// Flat-damage family skips the effectiveness gauntlet.
	if (FLAT_DAMAGE.has(eff)) return scoreMove(ctx, partner, dpartner, id, eff);
	// IfMoveEffectivenessEquals reads eff()'s class space (TYPE_MULTI_*).
	const cls = ctx.eff(id, ctx.foeActive).class;
	const kill = () => comparisonMade(ctx, id) &&
		ctx.dmg(ctx.active, ctx.foeActive, id) >= ctx.foeActive.hp; // roll=100 (USE_MAX_DAMAGE); fork getDamage roll is the engine's documented estimate
	const tryDown = (v) => {
		if (kill()) return; // "max roll kills" → no reduction
		if (hpPct(dpartner) === 0) return; // partner out — always true in singles
		if (ctx.aiRand(256) < 64) return; // 25% skip → 75% of the −v (spec's "25%" prose is the misread; comment at 6647 says 75%)
		add(ctx, -v);
	};
	if (cls === 20) tryDown(1); // not very effective
	else if (cls === 10) tryDown(2); // quarter damage
	return scoreMove(ctx, partner, dpartner, id, eff);
}

function scoreMove(ctx, partner, dpartner, id, eff) {
	// CheckIfHighestDamageWithPartner USE_MAX_DAMAGE (trainer_ai.c:2476):
	// compare the current slot's damage against EVERY move of the attacker
	// and the attacker's partner (singles: GetPartner = the attacker itself
	// → own moves compared twice, same outcome). Damage oracle: fork dmg()
	// vs the current defender (documented engine estimate; decomp roll 100).
	const me = ctx.active;
	const mine = moveDmg(ctx, me);
	const cur = mine[ctx.slot];
	const team = [me];
	if (partner) team.push(partner);
	let highest = true;
	for (const b of team) {
		const d = b === me ? mine : moveDmg(ctx, b);
		if (d.some((x) => x > cur)) { highest = false; break; }
	}
	if (!highest) return checkBeforeScoring(ctx, partner, dpartner, id, eff);
	// Explosion/Self-Destruct (HALVE_DEFENSE) — unreachable in practice
	// (the effect is in NO_DAMAGE_CALC so no comparison was ever made),
	// ported for fidelity.
	if (eff === 'BATTLE_EFFECT_HALVE_DEFENSE') return checkSpecialScoring(ctx, partner, dpartner, id, eff);
	// Priority +1: rand < 50 → CheckBeforeScoring (SE bonuses apply);
	// else +1 and BYPASS them. → 206/256 = 80.47% of +1.
	if (eff === 'BATTLE_EFFECT_PRIORITY_1') {
		if (ctx.aiRand(256) < 50) return checkBeforeScoring(ctx, partner, dpartner, id, eff);
		add(ctx, 1);
		return checkSpecialScoring(ctx, partner, dpartner, id, eff);
	}
	// 50% of +1 (the other 50% goes to the SE bonuses instead).
	if (ctx.aiRand(256) < 128) return checkBeforeScoring(ctx, partner, dpartner, id, eff);
	add(ctx, 1);
	return checkSpecialScoring(ctx, partner, dpartner, id, eff);
}

function moveDmg(ctx, b) {
	// TrainerAI_CalcAllDamage eligibility per slot (same gate tables),
	// damage = fork dmg() vs the current defender.
	return (b.moveSlots || [null, null, null, null]).slice(0, 4).map((ms) =>
		(ms && ms.id && comparisonMade(ctx, ms.id)) ? ctx.dmg(b, ctx.foeActive, ms.id) : 0);
}

function checkBeforeScoring(ctx, partner, dpartner, id, eff) {
	if (FLAT_DAMAGE.has(eff)) return checkSpecialScoring(ctx, partner, dpartner, id, eff);
	const cls = ctx.eff(id, ctx.foeActive).class;
	if (cls === 80) { // ×2 → rand ≥ 100: 156/256 = 60.9% of +1
		if (ctx.aiRand(256) >= 100) add(ctx, 1);
		return checkSpecialScoring(ctx, partner, dpartner, id, eff);
	}
	if (cls === 160) { // ×4 → rand ≥ 64: 75% of +1
		if (ctx.aiRand(256) >= 64) add(ctx, 1);
		return checkSpecialScoring(ctx, partner, dpartner, id, eff);
	}
	return checkSpecialScoring(ctx, partner, dpartner, id, eff);
}

// ══ CheckSpecialScoring (script.s:6713-6734) ════════════════════════════
function checkSpecialScoring(ctx, partner, dpartner, id, eff) {
	if (id === 'skillswap') return skillSwap(ctx, id);
	// LOAD_MOVE_TYPE: STATIC dex type (no NaturalGift/Judgment resolution);
	// the re-load before the type checks overwrites the leading one — both
	// dice-free, so the dispatch order below is all that matters.
	if (id === 'earthquake' || id === 'magnitude') return earthquake(ctx, partner);
	if (id === 'futuresight' || id === 'doomdesire') return futureSight(ctx, partner);
	if (id === 'raindance') return rainDance(ctx, partner);
	if (id === 'sunnyday') return sunnyDay(ctx, partner);
	if (id === 'hail') return hail(ctx, partner);
	if (id === 'sandstorm') return sandstorm(ctx, partner);
	if (id === 'gravity') return gravity(ctx, partner, dpartner);
	if (id === 'trickroom') return trickRoom(ctx, partner, dpartner);
	if (id === 'followme') return followMe(ctx, partner);
	const mtype = norm(ctx.move(id) && ctx.move(id).type);
	if (mtype === 'electric') return checkElectric(ctx, partner, dpartner, id);
	if (mtype === 'fire') return checkFire(ctx, partner, id);
	if (mtype === 'water') return checkWater(ctx, partner, dpartner, id);
	if (knowsMove(partner, 'helpinghand')) return partnerHelpingHand(ctx, eff);
}

function earthquake(ctx, partner) {
	// No partner-liveness check (decomp comment 7109: "a solo battler will
	// score Earthquake and Magnitude an additional −3").
	if (partner && partner.volatiles.magnetrise) return add(ctx, 2);
	if (abChk(ctx, partner, 'levitate') === 'have') return add(ctx, 2);
	if (hasType(partner, 'flying')) return add(ctx, 2);
	if (hasType(partner, 'fire') || hasType(partner, 'electric') ||
		hasType(partner, 'poison') || hasType(partner, 'rock')) return add(ctx, -10);
	return add(ctx, -3);
}

function futureSight(ctx, partner) {
	if (hpPct(partner) === 0) return; // no partner → no change (singles always)
	if (!knowsMove(partner, 'futuresight') && !knowsMove(partner, 'doomdesire')) return;
	const rank = ctx.engine.speedRank();
	const self = rank.indexOf(ctx.active);
	const prank = () => rank.indexOf(partner);
	if (self === 3) return add(ctx, -3);
	if (self === 2) {
		if (prank() === 0 || prank() === 1) return add(ctx, -3);
		if (ctx.aiRand(256) < 128) return; // speed-tie coin
		if (prank() === 2) return add(ctx, -3);
		return;
	}
	if (self === 1) {
		if (prank() === 0) return add(ctx, -3);
		if (ctx.aiRand(256) < 128) return;
		if (prank() === 1) return add(ctx, -3);
		return;
	}
	if (self === 0) {
		if (ctx.aiRand(256) < 128) return; // 50%
		if (prank() === 0) return add(ctx, -3);
	}
}

function skillSwap(ctx) {
	// LoadBattlerAbility ATTACKER (self → true ability, suppression-aware).
	const ab = scrAb(ctx, ctx.active);
	if (ab === 'truant' || ab === 'slowstart' || ab === 'stall' || ab === 'klutz') return add(ctx, 5);
	const tab = scrAb(ctx, ctx.foeActive); // DEFENDER arm guesses in decomp — real ability here (deviation)
	if (tab === 'shadowtag' || tab === 'purepower' || tab === 'hugepower' ||
		tab === 'moldbreaker' || tab === 'solidrock' || tab === 'filter' || tab === 'flowergift') return add(ctx, 2);
}

function gravity(ctx, partner, dpartner) {
	if (ctx.battle.field.pseudoWeather.gravity) return add(ctx, -30); // TagStrategy_PartnerScoreMinus30 label = per-move AddToMoveScore(-30)+PopOrEnd
	// Levitate read via CheckBattlerAbility (raw battleMons ability — Gravity
	// is inactive here anyway).
	const floaty = (p) => abChk(ctx, p, 'levitate') === 'have' || hasType(p, 'flying') || (!!p && p.volatiles.magnetrise);
	if (floaty(ctx.active)) return add(ctx, -5);
	if (floaty(partner)) return add(ctx, -5);
	// Target arms: the first matching check jumps to the shared TryPlus3
	// leaf (ONE roll — later same-side checks were jumped past), which then
	// GoTo's the TARGET-PARTNER scan; the scan then falls to PopOrEnd.
	if (floaty(ctx.foeActive) && ctx.aiRand(256) >= 64) add(ctx, 3); // 75% +3
	if (floaty(dpartner) && ctx.aiRand(256) >= 64) add(ctx, 3);
}

function trickRoom(ctx, partner, dpartner) {
	// "reduced to one active per side" gates — in SINGLES the zeroed partner
	// always reads HP 0 → Trick Room is ALWAYS −30 (faithful decomp).
	if (hpPct(partner) === 0 || hpPct(dpartner) === 0 || hpPct(ctx.foeActive) === 0) return add(ctx, -30);
	const rank = ctx.engine.speedRank();
	const self = rank.indexOf(ctx.active);
	const prank = rank.indexOf(partner);
	if (self === 0) { if (prank === 0 || prank === 1) return add(ctx, -30); return add(ctx, -5); }
	if (self === 1) { if (prank === 0) return add(ctx, -30); return add(ctx, -5); }
	if (self === 2 && prank !== 3) return add(ctx, -5);
	if (self === 3 && prank !== 2) return add(ctx, -5);
	if (ctx.aiRand(256) < 64) return add(ctx, -5); // 25% −5 …
	add(ctx, 5); // … else +5
}

function followMe(ctx, partner) {
	const a = hpPct(ctx.active);
	const p = hpPct(partner); // zeroed → 0 in singles
	const leaf = (v) => { if (ctx.aiRand(256) >= 64) add(ctx, v); }; // 75%
	if (a > 90) return p > 90 ? leaf(-1) : p > 50 ? leaf(1) : p > 30 ? leaf(2) : leaf(3);
	if (a > 50) return p > 90 ? leaf(-2) : p > 50 ? leaf(-1) : p > 30 ? leaf(1) : leaf(2);
	if (a > 30) return p > 90 ? leaf(-2) : p > 50 ? leaf(-2) : p > 30 ? leaf(1) : leaf(2);
	if (ctx.aiRand(256) >= 64) add(ctx, -5); // attacker ≤30: 75% ScoreMinus5
}

function rainDance(ctx, partner) {
	const ab = scrAb(ctx, ctx.active);
	if (ab === 'hydration') { if (ctx.active.status) add(ctx, 2); } // only while statused
	else if (ab === 'dryskin') add(ctx, 2);
	if (abChk(ctx, partner, 'hydration') === 'have') { if (partner && partner.status) add(ctx, 2); }
	else if (abChk(ctx, partner, 'dryskin') === 'have') add(ctx, 2);
}

function sunnyDay(ctx, partner) {
	const ab = scrAb(ctx, ctx.active);
	if (ab === 'leafguard') { // healthy AND ≥30% HP
		if (!ctx.active.status && hpPct(ctx.active) >= 30) add(ctx, 2);
	} else if (ab === 'flowergift') add(ctx, 2);
	else if (ab === 'dryskin') add(ctx, -2);
	else if (ab === 'solarpower') {
		// QUIRK (script.s:6815-6822): ≥50% adds +1 then FALLS THROUGH into
		// the 50%-of-−2 coin: +1 AND (coin) −2; <50% flips the −2 coin only.
		if (hpPct(ctx.active) >= 50) add(ctx, 1);
		if (ctx.aiRand(256) >= 128) add(ctx, -2);
	}
	const have = (a) => abChk(ctx, partner, a) === 'have';
	if (have('leafguard')) {
		if (!partner || partner.status) return;
		if (hpPct(partner) < 30) return;
		add(ctx, 2);
	} else if (have('flowergift')) add(ctx, 2);
	else if (have('dryskin')) add(ctx, -2);
	else if (have('solarpower')) {
		if (hpPct(partner) >= 50) add(ctx, 1); // same fall-through quirk
		if (ctx.aiRand(256) >= 128) add(ctx, -2);
	}
}

function hail(ctx, partner) {
	// FIRST match per side awards a single +2 (jumps land on the shared
	// +2 label which exits to the partner/End — the doc's "independent
	// checks that can stack" describes same-side stacking wrongly).
	const ab = scrAb(ctx, ctx.active);
	if (ab === 'icebody' || ab === 'snowcloak' || knowsMove(ctx.active, 'blizzard')) add(ctx, 2);
	if (abChk(ctx, partner, 'icebody') === 'have' || abChk(ctx, partner, 'snowcloak') === 'have' ||
		knowsMove(partner, 'blizzard')) add(ctx, 2);
}

function sandstorm(ctx, partner) {
	if (scrAb(ctx, ctx.active) === 'sandveil' || hasType(ctx.active, 'rock')) add(ctx, 2);
	if (abChk(ctx, partner, 'sandveil') === 'have' || hasType(partner, 'rock')) add(ctx, 2);
}

function checkElectric(ctx, partner, dpartner, id) {
	if (id === 'discharge') return spreadElectric(ctx, partner);
	if (abChk(ctx, dpartner, 'lightningrod') === 'have') {
		add(ctx, -1);
		// FlagBattlerIsType FALSE == AI_NOT_HAVE → skip the extra −8.
		if (!hasType(dpartner, 'ground')) return partnerLightningRod(ctx, partner, id);
		add(ctx, -8);
	}
	return partnerLightningRod(ctx, partner, id);
}
function partnerLightningRod(ctx, partner, id) {
	if (abChk(ctx, partner, 'lightningrod') === 'have') return add(ctx, -10);
	// dead `IfMoveEqualTo DISCHARGE` re-check (already dispatched) — skipped.
}
function spreadElectric(ctx, partner) {
	if (abChk(ctx, partner, 'motordrive') === 'have') return add(ctx, 3);
	if (abChk(ctx, partner, 'voltabsorb') === 'have') return add(ctx, 3);
	if (hasType(partner, 'water')) return add(ctx, -10);
	if (hasType(partner, 'flying')) return add(ctx, -10);
	// vanilla BUG (comment script.s:7247): Ground checked LAST — a
	// Water/Ground or Flying/Ground partner takes −10, never +3.
	if (hasType(partner, 'ground')) return add(ctx, 3);
	return add(ctx, -3); // SINGLES always lands here
}

function checkWater(ctx, partner, dpartner, id) {
	if (id === 'surf') return spreadWater(ctx, partner);
	// Gate jumps on AI_NOT_HAVE ONLY: a zeroed/none-ability partner reads
	// AI_UNKNOWN → falls into −1. (Singles: every Water move always −1.)
	if (abChk(ctx, dpartner, 'stormdrain') !== 'no') add(ctx, -1);
	if (abChk(ctx, partner, 'stormdrain') === 'have') return add(ctx, -10);
	// dead Surf re-check ("should never result in a branch") — skipped.
}
function spreadWater(ctx, partner) {
	if (abChk(ctx, partner, 'dryskin') === 'have') return add(ctx, 3);
	if (abChk(ctx, partner, 'waterabsorb') === 'have') return add(ctx, 3);
	// BUG note (script.s:7296): no Rock-partner check despite the immunity.
	if (hasType(partner, 'ground')) return add(ctx, -10);
	if (hasType(partner, 'fire')) return add(ctx, -10);
	return add(ctx, -3); // SINGLES always lands here
}

function checkFire(ctx, partner, id) {
	// IfActivatedFlashFire ATTACKER: +1 on TOP of everything (fork volatile).
	if (ctx.active.volatiles.flashfire) add(ctx, 1);
	if (id !== 'lavaplume') return;
	// Lava Plume: Dry Skin partner → −3 (comment claims +3; code −3 wins).
	if (abChk(ctx, partner, 'dryskin') === 'have') return add(ctx, -3);
	if (abChk(ctx, partner, 'flashfire') === 'have') return add(ctx, 3);
	if (hasType(partner, 'grass') || hasType(partner, 'steel') ||
		hasType(partner, 'ice') || hasType(partner, 'bug')) return add(ctx, -10);
	return add(ctx, -3); // SINGLES always lands here
}

function partnerHelpingHand(ctx, eff) {
	if (FLAT_DAMAGE.has(eff)) return;
	// FlagMoveDamageScore FALSE; IfLoadedNotEqualTo NO_COMPARISON → ScorePlus1
	if (comparisonMade(ctx, ctx.moves[ctx.slot].id)) add(ctx, 1);
}

// ══ TagStrategy_Partner (script.s:7326-7681) — shared with CheckHP in the
// decomp; doubles-only branch, structurally unreachable through the engine
// pairing (see header). Same helper set as check_hp.js's faithful copy. ══
function partnerSubroutine(ctx, partner) {
	const end = {};
	const partnerItem = norm(partner && partner.item);
	const absorbLadder = () => {
		if (hpPct(partner) === 100) return add(ctx, -10);
		if (hpPct(partner) > 90) return end;
		if (hpPct(partner) > 75) return ctx.aiRand(256) >= 64 ? add(ctx, 3) : end; // 25%
		if (hpPct(partner) > 50) return ctx.aiRand(256) >= 128 ? add(ctx, 3) : end; // 50%
		return ctx.aiRand(256) >= 192 ? add(ctx, 3) : end; // 75%
	};
	const electricAbsorption = () => {
		if (abChk(ctx, partner, 'motordrive') === 'have') {
			if (ctx.aiRand(256) < 160) return end; // 62.5% no change
			if (statStage(partner, 'spe') === 12) return add(ctx, -30); // +6 Speed
			return add(ctx, 3);
		}
		if (abChk(ctx, partner, 'voltabsorb') === 'have') return absorbLadder();
		return add(ctx, -30);
	};
	const fireAbsorption = () => {
		if (abChk(ctx, partner, 'flashfire') === 'have' && !partner.volatiles.flashfire) return add(ctx, 3);
		return add(ctx, -30);
	};
	const waterAbsorption = () => {
		if (abChk(ctx, partner, 'waterabsorb') === 'have' || abChk(ctx, partner, 'dryskin') === 'have') return absorbLadder();
		return add(ctx, -30);
	};

	if (partner.fainted) return add(ctx, -30); // IfBattlerFainted (switching-mask → fork fainted, deviation noted)
	const id = ctx.moves[ctx.slot].id;
	if (!comparisonMade(ctx, id)) return partnerStatusMove();
	const mtype = norm(ctx.move(id) && ctx.move(id).type);
	if (mtype === 'fire') return fireAbsorption();
	if (mtype === 'electric') return electricAbsorption();
	if (mtype === 'water') return waterAbsorption();
	if (id === 'fling') return end; // TagStrategy_PartnerTrick
	return add(ctx, -30); // damaging, unhandled → ScoreMinus30

	function partnerStatusMove() {
		const eff = ctx.effectOf(id);
		if (id === 'skillswap') {
			const ab = scrAb(ctx, partner); // defender = partner (target)
			if (ab === 'truant' || ab === 'slowstart') return add(ctx, 10);
			if (scrAb(ctx, ctx.active) !== 'levitate') return giveAccuracy();
			if (ab === 'levitate') return add(ctx, -30);
			const t = typesOf(partner);
			if (t[0] !== 'electric') return giveAccuracy();
			add(ctx, 1); // +1 per Electric slot: mono-Electric partner scores twice
			if (t[1] !== 'electric') return giveAccuracy();
			return add(ctx, 1);
		}
		if (id === 'willowisp') {
			if (abChk(ctx, partner, 'flashfire') === 'have') return fireAbsorption();
			if (abChk(ctx, partner, 'guts') !== 'have') return add(ctx, -30);
			if (partner.status) return add(ctx, -30);
			if (hasType(partner, 'fire')) return add(ctx, -30);
			if (partnerItem === 'flameorb' || partnerItem === 'toxicorb') return add(ctx, -30);
			if (hpPct(partner) < 81) return add(ctx, -30);
			return add(ctx, 5);
		}
		if (id === 'thunderwave') {
			if (hasType(partner, 'ground')) return add(ctx, -30);
			if (abChk(ctx, partner, 'motordrive') === 'have') return electricAbsorption();
			if (abChk(ctx, partner, 'voltabsorb') === 'have') return electricAbsorption();
			return add(ctx, -30);
		}
		if (eff === 'BATTLE_EFFECT_STATUS_BADLY_POISON' || eff === 'BATTLE_EFFECT_STATUS_POISON') {
			// BUG (script.s:7548): no poison-immunity check on the partner.
			if (abChk(ctx, partner, 'poisonheal') !== 'have') return add(ctx, -30);
			if (partner.status) return add(ctx, -30);
			if (partnerItem === 'toxicorb') return add(ctx, -30);
			if (hpPct(partner) > 91) return add(ctx, -30); // comment 81% vs code 91% — code wins
			return add(ctx, 5);
		}
		if (id === 'helpinghand') {
			if (hpPct(partner) === 0) return add(ctx, -30);
			const first = ctx.engine.speedRank().indexOf(partner) < 1; // IfLoadedLessThan 1
			if (hpPct(partner) > 50 || first) {
				if (ctx.aiRand(256) < 64) return add(ctx, -1); // 25% −1
				return add(ctx, 2); // 75% +2
			}
			return end;
		}
		if (id === 'swagger') {
			// Own Tempo not considered (decomp note).
			if (partnerItem === 'persimberry' || partnerItem === 'lumberry') {
				if (statStage(partner, 'atk') > 7) return end; // ≥ +2: no change
				return add(ctx, 3);
			}
			return add(ctx, -30);
		}
		if (id === 'trick' || id === 'switcheroo') return end;
		if (id === 'gastroacid') {
			if (ctx.engine.battle.suppressingAbility(partner)) return add(ctx, -30);
			const ab = scrAb(ctx, partner);
			if (ab === 'truant' || ab === 'slowstart') return add(ctx, 5);
			return end;
		}
		if (id === 'acupressure') {
			if (abChk(ctx, partner, 'simple') === 'have') {
				for (const k of ['atk', 'def', 'spe', 'spa', 'spd', 'evasion', 'accuracy']) {
					if (statStage(partner, k) > 8) return add(ctx, -10); // any ≥ +3
				}
			} else {
				for (const k of ['atk', 'def', 'spe', 'spa', 'spd', 'evasion', 'accuracy']) {
					if (statStage(partner, k) === 12) return add(ctx, -30); // any +6
				}
			}
			if (hpPct(partner) < 51) return add(ctx, -1);
			if (hpPct(partner) > 90) return ctx.aiRand(256) < 80 ? end : add(ctx, 2); // 68.75%
			// QUIRK (script.s:7660-7668): 50% bail, then FALLS THROUGH into
			// the >90 leaf — overall +2 = 50% × 68.75%.
			if (ctx.aiRand(256) < 128) return end;
			return ctx.aiRand(256) < 80 ? end : add(ctx, 2);
		}
		return add(ctx, -30); // any other status move aimed at the partner
	}

	function giveAccuracy() {
		const ab = scrAb(ctx, ctx.active);
		if (ab !== 'compoundeyes' && ab !== 'noguard') return add(ctx, -30);
		if (PARTNER_ACCURACY_MOVES.some((mv) => knowsMove(partner, mv))) return add(ctx, 3);
		return add(ctx, -30);
	}
}
