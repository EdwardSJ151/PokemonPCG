'use strict';
// Throwaway probe: exercise the ported TrainerAI_ShouldUseItem + executeUsedItem
// against live battle state. Each scenario = a real battle; the probe runs on
// the first real turn request (undefined return = try again next request).
// Deterministic (AICpuLcg seeded from the battle seed). Deleted in cleanup.
const Sim = require('../../pokemon-showdown/dist/sim');
const { Engine } = require('./ai/engine.js');
const ITEMS = require('./ai/data_items.js');

const ZERO = { hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0 };
const pkm = (species, moves) =>
	({ species, level: 50, ability: '', item: '', nature: 'quiet', ivs: ZERO, moves });

const SEED = [7, 3, 11, 5];
const seen = {};
let failures = 0;

function check(name, value, ok, note) {
	if (seen[name] !== undefined && seen[name] !== value) {
		console.log(`  NONDET ${name}: ${seen[name]} != ${value}`);
		failures++;
	}
	seen[name] = value;
	const status = ok ? 'ok  ' : 'FAIL';
	console.log(`  ${status} ${name} = ${value}${note ? `  (${note})` : ''}`);
	if (!ok) failures++;
}

const P1 = [pkm('mudkip', ['splash', 'splash', 'splash', 'splash'])];
const STATUS_ONLY = ['rest', 'growl', 'doubleteam', 'tail whip'];

async function scenario(name, teamA, teamB, items, inject, probe) {
	const stream = new Sim.BattleStream();
	const streams = Sim.getPlayerStreams(stream);
	const cap = [];
	(async () => { for await (const _ of streams.omniscient) {} })().catch(() => {});
	(async () => {
		for await (const chunk of streams.p1)
			for (const line of chunk.split('\n')) {
				cap.push(line);
				if (line.match(/^\|request\|/)) streams.p1.write('default');
			}
	})().catch(() => {});
	let engine = null, probed = false;
	const done = new Promise((resolve) => {
		(async () => {
			for await (const chunk of streams.p2) {
				for (const line of chunk.split('\n')) {
					const m = line.match(/^\|request\|(.*)$/);
					if (!m) continue;
					const req = JSON.parse(m[1]);
					if (!engine) {
						if (!stream.battle) continue;
						engine = new Engine(stream.battle, { mask: 1, items });
						if (inject) inject(stream.battle);
					}
					if (!probed && !req.teamPreview && !req.wait && !req.forceSwitch) {
						let r;
						try { r = probe(engine, req, cap); } catch (e) { console.error(`  PROBE-ERR ${name}:`, e.stack); r = 0; }
						if (r !== undefined) probed = true;
					}
					const choice = engine.decide(req);
					if (choice === null) continue;
					streams.p2.write(choice);
				}
			}
			resolve();
		})().catch(() => resolve());
	});
	streams.omniscient.write(`>start ${JSON.stringify({ formatid: 'gen4customgame', seed: SEED })}`);
	streams.omniscient.write(`>player p1 ${JSON.stringify({ name: 'A', team: teamA })}`);
	streams.omniscient.write(`>player p2 ${JSON.stringify({ name: 'B', team: teamB })}`);
	const guard = setTimeout(() => { console.error(`  [aborted] ${name} did not end`); process.exit(1); }, 40000);
	await done;
	clearTimeout(guard);
}

const LOW_HP = (b) => {
	const p = b.sides[1].active[0];
	p.hp = Math.max(1, (p.maxhp >> 2) - 1);
};

(async () => {
	// 1: low HP + Potion -> RECOVER_HP; execute heals exactly the record amount
	await scenario('potion-lowhp', P1, [pkm('snorlax', STATUS_ONLY)], ['potion'],
		LOW_HP,
		(engine) => {
			const p = engine.battle.sides[1].active[0];
			const before = p.hp;
			const v = engine.shouldUseItem(1);
			const executed = v && engine.executeUsedItem(1);
			check('1 use result', v, v === true, `usedItem=${engine.usedItem} type=${engine.usedItemType}`);
			check('1 slot zeroed', engine.initialItems[0] === '', engine.initialItems[0] === '', 'no-break quirk: last examined non-NONE wins');
			const want = Math.min(ITEMS.potion.hp, p.maxhp - before);
			check('1 healed', p.hp - before, p.hp - before === want, `want +${want}`);
			check('1 -item line', engine.battle.log.some((l) => l.startsWith('|-item|')),
				engine.battle.log.some((l) => l.startsWith('|-item|')));
			return v;
		});

	// 2: full HP -> no potion
	await scenario('potion-fullhp', P1, [pkm('snorlax', STATUS_ONLY)], ['potion'],
		null,
		(engine) => {
			const v = engine.shouldUseItem(1);
			check('2 no use at full hp', v, v === false, `type=${engine.usedItemType}`);
			check('2 slot intact', engine.initialItems[0], engine.initialItems[0] === 'potion');
			return v;
		});

	// 3: paralyzed + Parlyz-Heal -> RECOVER_STATUS (condition bit 2)
	await scenario('parlyz-par', P1, [pkm('snorlax', STATUS_ONLY)], ['parlyzheal'],
		(b) => b.sides[1].active[0].setStatus('par'),
		(engine) => {
			const p = engine.battle.sides[1].active[0];
			const v = engine.shouldUseItem(1);
			const executed = v && engine.executeUsedItem(1);
			check('3 use result', v, v === true, `cond=${engine.usedItemCondition}`);
			check('3 par bit', engine.usedItemCondition, engine.usedItemCondition === 2);
			check('3 status cured', p.status === '', p.status === '', `now=${p.status}`);
			return v;
		});

	// 4: X Attack on the entry turn -> STAT_BOOSTER (condition = BATTLE_STAT_ATTACK)
	await scenario('xattack-entry', P1, [pkm('snorlax', STATUS_ONLY)], ['xattack'],
		null,
		(engine) => {
			const p = engine.battle.sides[1].active[0];
			const v = engine.shouldUseItem(1);
			const executed = v && engine.executeUsedItem(1);
			check('4 use result', v, v === true, `type=${engine.usedItemType} cond=${engine.usedItemCondition}`);
			check('4 atk stat idx', engine.usedItemCondition, engine.usedItemCondition === 1);
			check('4 boost applied', p.boosts.atk, p.boosts.atk === 1);
			return v;
		});

	// 5: booster on a NON-entry turn is blocked (the fakeOut guard). Pre-seed
	// lastActive so the engine treats the lead as already in play, then the
	// booster branch must be skipped and the item must not be consumed.
	await scenario('xattack-notentry', P1, [pkm('snorlax', STATUS_ONLY)], ['xattack'],
		null,
		(engine) => {
			const p = engine.battle.sides[1].active[0];
			engine.lastActive[1] = p; // pretend it entered on an earlier turn
			const v = engine.shouldUseItem(1);
			check('5 blocked non-entry', v, v === false, `type=${engine.usedItemType}`);
			check('5 MAX clobber', engine.usedItemType, engine.usedItemType === 'MAX');
			check('5 slot intact', engine.initialItems[0], engine.initialItems[0] === 'xattack');
			return v;
		});

	// 6: no-break quirk — two potions, low HP: BOTH slots zeroed (slot 2 matches
	// too, since execution happens after the loop)
	await scenario('nobreak', P1, [pkm('snorlax', STATUS_ONLY)], ['potion', 'potion'],
		LOW_HP,
		(engine) => {
			const v = engine.shouldUseItem(1);
			const executed = v && engine.executeUsedItem(1);
			check('6 both zeroed', `${engine.initialItems[0] === ''},${engine.initialItems[1] === ''}`,
				engine.initialItems[0] === '' && engine.initialItems[1] === '', 'no-break quirk');
			check('6 usedItem', engine.usedItem, engine.usedItem === 'potion');
			return v;
		});

	// 7: Embargo -> never an item
	await scenario('embargo', P1, [pkm('snorlax', STATUS_ONLY)], ['potion'],
		(b) => { const p = b.sides[1].active[0]; p.addVolatile('embargo'); p.hp = Math.max(1, (p.maxhp >> 2) - 1); },
		(engine) => {
			const v = engine.shouldUseItem(1);
			check('7 embargo blocks', v, v === false);
			check('7 slot intact', engine.initialItems[0], engine.initialItems[0] === 'potion');
			return v;
		});

	// 8: slot-examined condition — 3 alive mons, 2 item slots: slot 1 is NOT
	// examined (3 alive > 2 - 1 + 1); the X Attack in slot 1 must stay untouched
	await scenario('slot-examine', P1,
		[pkm('snorlax', STATUS_ONLY), pkm('machoke', ['growl', 'tail whip', 'splash', 'splash']), pkm('pichu', ['growl', 'tail whip', 'splash', 'splash'])],
		['fullrestore', 'xattack'],
		null,
		(engine) => {
			const v = engine.shouldUseItem(1);
			check('8 no match (full hp, slot 1 skipped)', v, v === false, `type=${engine.usedItemType}`);
			check('8 slot 1 untouched', engine.initialItems[1], engine.initialItems[1] === 'xattack', 'would match on entry turn if examined');
			return v;
		});

	// 9: Full Restore on low HP + status -> full heal and cure
	await scenario('fullrestore', P1, [pkm('snorlax', STATUS_ONLY)], ['fullrestore'],
		(b) => { const p = b.sides[1].active[0]; p.hp = Math.max(1, (p.maxhp >> 2) - 1); p.setStatus('par'); },
		(engine) => {
			const p = engine.battle.sides[1].active[0];
			const v = engine.shouldUseItem(1);
			const executed = v && engine.executeUsedItem(1);
			check('9 use result', v, v === true, `type=${engine.usedItemType}`);
			check('9 full hp', p.hp === p.maxhp, p.hp === p.maxhp, `${p.hp}/${p.maxhp}`);
			check('9 cured', p.status === '', p.status === '', `now=${p.status}`);
			return v;
		});

	// 10: Guard Spec on the entry turn -> consumed (no Mist in the fork)
	await scenario('guardspec', P1, [pkm('snorlax', STATUS_ONLY)], ['guardspec'],
		null,
		(engine) => {
			const v = engine.shouldUseItem(1);
			const executed = v && engine.executeUsedItem(1);
			check('10 use result', v, v === true, `type=${engine.usedItemType}`);
			check('10 slot zeroed', engine.initialItems[0] === '', engine.initialItems[0] === '');
			return v;
		});

	console.log(failures === 0 ? 'ALL PASS' : `FAILURES: ${failures}`);
	process.exit(failures === 0 ? 0 : 1);
})().catch((e) => { console.error('FATAL:', e); process.exit(1); });
