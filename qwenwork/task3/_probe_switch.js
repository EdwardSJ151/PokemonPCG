'use strict';
// Throwaway probe: exercise the ported BattleAI_ShouldSwitch sub-routines
// directly against live battle state (decide() would mask which branch fired).
// Each scenario = a real battle; the probe runs once per scenario on the
// first real turn request (undefined return = try again next request).
// Deterministic (AICpuLcg seeded from the battle seed). Deleted in cleanup.
const Sim = require('/home/pressprexx/Code/GamingResearch/PokemonPCG/pokemon-showdown/dist/sim');
const { Engine } = require('./ai/engine.js');

const ZERO = { hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0 };
const pkm = (species, moves, ability = '') =>
	({ species, level: 50, ability, item: '', nature: 'quiet', ivs: ZERO, moves });

const SEED = [7, 3, 11, 5];
const seen = {};
let failures = 0;

function check(name, value, ok, note) {
	if (seen[name] !== undefined && seen[name] !== value) {
		console.log(`  FAIL ${name}: not deterministic (${seen[name]} then ${value})`);
		failures++;
	}
	seen[name] = value;
	const status = ok ? 'ok  ' : 'FAIL';
	console.log(`  ${status} ${name} = ${value}${note ? `  (${note})` : ''}`);
	if (!ok) failures++;
}

async function scenario(name, teamA, teamB, inject, probe) {
	const stream = new Sim.BattleStream();
	const streams = Sim.getPlayerStreams(stream);
	(async () => { for await (const _ of streams.omniscient) {} })().catch(() => {});
	(async () => {
		for await (const chunk of streams.p1)
			for (const line of chunk.split('\n'))
				if (line.match(/^\|request\|/)) streams.p1.write('default');
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
						engine = new Engine(stream.battle, { mask: 1, items: [] });
						if (inject) inject(stream.battle);
					}
					if (!probed && !req.teamPreview && !req.wait && !req.forceSwitch) {
						let r;
						try { r = probe(engine, req); } catch (e) { console.error(`  PROBE-ERR ${name}:`, e.message); r = 0; }
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
	const guard = setTimeout(() => { console.error(`  [aborted] ${name} did not end`); process.exit(1); }, 20000);
	await done;
	clearTimeout(guard);
}

const TEAM_B_BASE = [
	pkm('snorlax', ['body slam', 'earthquake', 'rest', 'recover']),
	pkm('flareon', ['flamethrower', 'swift', 'slash', 'rest'], 'flashfire'),
	pkm('primeape', ['karate chop', 'cross chop', 'low kick', 'focus punch']),
];
const HIT_FIRE = (b) =>
	b.sides[1].active[0].attackedBy.push({ source: b.sides[0].active[0], damage: 0, move: 'flamethrower' });

(async () => {
	// 1: foe Shadow Tag → never switch
	await scenario('shadowtag',
		[pkm('shedinja', ['x-scissor', 'shockwave', 'rest', 'spore'], 'shadowtag')],
		[pkm('snorlax', ['body slam', 'earthquake', 'rest', 'recover']),
		 pkm('machoke', ['karate chop', 'cross chop', 'focus punch', 'rest'])],
		null,
		(engine) => {
			const v = engine.shouldSwitch(1);
			check('1 shadowtag guard', v, v === -1, 'foe shadowtag');
			return v;
		});

	// 2: foe Wonder Guard, no SE move of ours, bench pichu owns thunder (SE on Electric/Flying)
	await scenario('wonderguard',
		[pkm('zapdos', ['thunder', 'fly', 'rest', 'roost'], 'wonderguard')],
		[pkm('snorlax', ['body slam', 'earthquake', 'rest', 'recover']),
		 pkm('pichu', ['thunder', 'swift', 'growl', 'tailwind']),
		 pkm('gengar', ['shadowball', 'thunderbolt', 'sludgebomb', 'willowisp'])],
		(b) => check('2 foe ability', b.sides[0].active[0].ability, true, 'entry override sticks?'),
		(engine) => {
			const v = engine.aiCannotDamageWonderGuard(1, engine.battle.sides[1].active[0], engine.battle.sides[0].active[0]);
			check('2 wonderguard → slot', v, v === 1 || v === -1, '66% roll; 1 = pichu');
			return v;
		});

	// 3: last hit was flamethrower (Fire); bench flareon holds Flash Fire
	await scenario('absorb',
		[pkm('flareon', ['flamethrower', 'quick attack', 'rest', 'roar'])],
		TEAM_B_BASE,
		HIT_FIRE,
		(engine) => {
			const p = engine.battle.sides[1].active[0];
			const v = engine.aiHasAbsorbAbilityInParty(1, p, engine.battle.sides[0].active[0]);
			check('3 absorb → slot', v, v === 1 || v === -1, '50% roll; 1 = flareon');
			return v;
		});

	// 4: asleep + Natural Cure + above half HP → rotate (slot 6 or -1)
	await scenario('asleep-nc',
		[pkm('mudkip', ['splash', 'growl', 'tail whip', 'splash'])],
		[pkm('pidgeot', ['fly', 'quick attack', 'rest', 'roost'], 'naturalcure'),
		 pkm('machoke', ['karate chop', 'cross chop', 'focus punch', 'rest'])],
		(b) => { b.sides[1].active[0].setStatus('slp'); },
		(engine) => {
			const p = engine.battle.sides[1].active[0];
			const v = engine.aiIsAsleepWithNaturalCure(1, p, engine.battle.sides[0].active[0]);
			check('4 asleep-nc → slot', v, v === 6 || v === -1, 'no hit yet: 87.5% → 6');
			return v;
		});

	// 5: five positive stat stages → stay (deterministic: no rolls before it)
	await scenario('boosted',
		[pkm('mudkip', ['splash', 'growl', 'tail whip', 'splash'])],
		[pkm('snorlax', ['body slam', 'earthquake', 'rest', 'recover']),
		 pkm('machoke', ['karate chop', 'cross chop', 'focus punch', 'rest'])],
		(b) => { b.sides[1].active[0].boosts = { atk: 0, def: 0, spa: 2, spd: 2, spe: 1, accuracy: 0, evasion: 0 }; },
		(engine) => {
			const v = engine.shouldSwitch(1);
			check('5 heavily boosted → -1', v, v === -1, '5 stages total');
			return v;
		});

	// 6: the full shouldSwitch() driver on the absorb state (wiring check)
	await scenario('absorb-full',
		[pkm('flareon', ['flamethrower', 'quick attack', 'rest', 'roar'])],
		TEAM_B_BASE,
		HIT_FIRE,
		(engine) => {
			const v = engine.shouldSwitch(1);
			check('6 shouldSwitch full → slot', v, v === 1 || v === -1, 'absorb branch inside driver');
			return v;
		});

	console.log(failures === 0 ? 'ALL PASS' : `FAILURES: ${failures}`);
	process.exit(failures === 0 ? 0 : 1);
})().catch((e) => { console.error('FATAL:', e); process.exit(1); });
