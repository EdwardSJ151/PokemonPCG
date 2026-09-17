'use strict';
// Smoke for Engine.eff() — assert decomp-faithful mask/scaled/class on a live
// battle with abilities/items/volatiles hand-set around each case. Cases
// evaluate immediately (C = check), so transient state is per-case. Temporary —
// assertions fold into ai_test.js in the integration phase.
const Sim = require('/home/pressprexx/Code/GamingResearch/PokemonPCG/pokemon-showdown/dist/sim');
const { Engine, SE, NVE, INEFF, LEVITATED, WONDER_GUARD, MAGNET_RISE } = require('./ai/engine.js');

const ZERO = { hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0 };
const pkm = (species, moves, ability = '', item = '') =>
	({ species, level: 50, ability, item, nature: 'quiet', ivs: ZERO, moves });

const TEAM_A = [
	pkm('groudon', ['earthquake', 'flamethrower', 'stoneedge', 'roar']),
	pkm('gyarados', ['waterfall', 'icefang', 'outrage', 'bodyslam'], 'adaptability'),
	pkm('hypno', ['darkpulse', 'psychic', 'hypnosis', 'thunderwave']),
	pkm('machoke', ['crosschop', 'rockslide', 'vitalthrow', 'doubleedge']),
	pkm('snorlax', ['tackle', 'curse', 'seismictoss', 'rest']),
	pkm('starmie', ['surf', 'hydropump', 'thunderbolt', 'iciclespear']),
];
const TEAM_B = [
	pkm('gengar', ['shadowball', 'thunderbolt', 'sludgebomb', 'willowisp'], 'levitate'),
	pkm('shedinja', ['x-scissor', 'swordsdance', 'toxic', 'leechlife'], 'wonderguard'),
	pkm('spiritomb', ['darkpulse', 'shadowball', 'willowisp', 'psychic'], 'filter'),
	pkm('snorlax', ['tackle', 'rest', 'selfdestruct', 'encore']),
	pkm('rhyhorn', ['earthquake', 'rockslide', 'hornattack', 'stomp']),
	pkm('starly', ['peck', 'pluck', 'wingattack', 'taunt']),
];

let failures = 0;
function check(label, got, want) {
	const ok = JSON.stringify(got) === JSON.stringify(want);
	if (!ok) failures++;
	console.log(`  ${ok ? 'ok  ' : 'FAIL'} ${label}${ok ? '' : `: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`}`);
}

async function main() {
	const stream = new Sim.BattleStream();
	const st = Sim.getPlayerStreams(stream);
	// fork player streams are ObjectReadStreams (no .on): the battle only
	// advances while the streams are consumed, and team preview must be answered
	(async () => { for await (const _ of st.omniscient) { /* drain */ } })().catch(() => {});
	for (const k of ['p1', 'p2'])
		(async () => {
			for await (const chunk of st[k]) {
				for (const line of chunk.split('\n')) {
					const m = line.match(/^\|request\|(.*)$/);
					if (m && JSON.parse(m[1]).teamPreview) st[k].write('team 123456');
				}
			}
		})();
	st.omniscient.write(`>start ${JSON.stringify({ formatid: 'gen4customgame', seed: [7, 3, 11, 5] })}`);
	st.omniscient.write(`>player p1 ${JSON.stringify({ name: 'A', team: TEAM_A })}`);
	st.omniscient.write(`>player p2 ${JSON.stringify({ name: 'B', team: TEAM_B })}`);
	for (let i = 0; i < 200
		&& (!stream.battle || !stream.battle.sides[0] || !stream.battle.sides[1]
			|| !stream.battle.sides[0].active[0] || !stream.battle.sides[1].active[0]); i++) {
		await new Promise((r) => setTimeout(r, 25));
	}
	if (!stream.battle || !stream.battle.sides[0] || !stream.battle.sides[1]
		|| !stream.battle.sides[0].active[0] || !stream.battle.sides[1].active[0]) {
		console.error('FATAL: battle never started');
		process.exit(1);
	}
	const battle = stream.battle;
	const engine = new Engine(battle, { items: [] });
	const ai = (i) => battle.sides[0].pokemon[i];
	const bi = (i) => battle.sides[1].pokemon[i];

	const setItem = (p, it) => { p.item = it; };
	const setAb = (p, a) => { p.ability = a; };
	const vol = (p, v) => { p.addVolatile(v, p); };
	const noVol = (p, v) => p.removeVolatile(v);

	// C(name, att, def, move, variant, expect, opts) — evaluates eff() now
	const C = (name, att, def, move, variant, expect, opts) => {
		const full = engine.eff(att, def, move, variant, opts);
		const got = {};
		for (const k of Object.keys(expect)) got[k] = full[k];
		check(name, got, expect);
	};

	console.log('eff() smoke (active variant unless noted):');

	// 1-2. Levitate: active variant sets LEVITATED, switch variant sets INEFF (quirk)
	C('levitate active', ai(0), bi(0), 'earthquake', 'active', { mask: LEVITATED, scaled: 60, class: 0, type: 'ground' });
	C('levitate switch', ai(0), bi(0), 'earthquake', 'switch', { mask: INEFF });

	// 3. Iron Ball on the Levitate mon → pre-branch skipped; ground is SE vs its poison
	setItem(bi(0), 'ironball');
	C('ironball vs levitate', ai(0), bi(0), 'earthquake', 'active', { mask: SE, scaled: 120, class: 80, type: 'ground' });
	setItem(bi(0), '');

	// 4. Foresight: the batched normal→ghost immunity rows are skipped (active); the
	// switch variant has no foresight handling (quirk)
	vol(bi(0), 'foresight');
	C('foresight active', ai(4), bi(0), 'tackle', 'active', { mask: 0, scaled: 60, class: 60, type: 'normal' });
	noVol(bi(0), 'foresight');
	C('no foresight', ai(4), bi(0), 'tackle', 'active', { mask: INEFF, scaled: 0, class: 0, type: 'normal' });

	// 5. Wonder Guard: blocks NVE, lets SE through; power-0 moves bypass the gate
	C('wg blocks nve (ground vs shedinja)', ai(0), bi(1), 'earthquake', 'active',
		{ mask: NVE | WONDER_GUARD, scaled: 30, class: 0, type: 'ground' });
	C('wg lets se through (dark vs shedinja)', ai(2), bi(1), 'darkpulse', 'active',
		{ mask: SE, scaled: 80, class: 80, type: 'dark' });
	C('wg flat-damage bypass (nightshade vs shedinja)', bi(0), bi(1), 'nightshade', 'active',
		{ mask: 0, scaled: 120, class: 80, type: 'ghost' });

	// 6. Adaptability: 40→80 STAB; double-SE lands 320 (unmapped); non-adapt 240→160
	C('adapt double-se (water vs rhyhorn)', ai(1), bi(4), 'waterfall', 'active',
		{ mask: SE, scaled: 320, class: 320, type: 'water' });
	C('plain double-se (water vs rhyhorn)', ai(5), bi(4), 'surf', 'active',
		{ mask: SE, scaled: 240, class: 160, type: 'water' });

	// 7. Filter halves SE: 120 → 90
	{
		const origAb = bi(3).ability;
		setAb(bi(3), 'filter');
		C('filter halves se (fighting vs snorlax)', ai(3), bi(3), 'crosschop', 'active',
			{ mask: SE, scaled: 90, class: 90, type: 'fighting' });
		setAb(bi(3), origAb);
	}

	// 8. Struggle short-circuit and ???-type neutrality
	C('struggle short-circuit', ai(0), bi(0), 'struggle', 'active', { mask: 0, scaled: 40, class: 40, type: 'normal' });
	C('??? type neutral (curse)', ai(4), bi(0), 'curse', 'active', { mask: 0, scaled: 40, class: 40, type: '???' });

	// 9. ignoreType/ignoreImmunities (flat-damage path): no STAB; the mask update is
	// gated on movePower, so power-0 moves get no effectiveness bits at all
	C('ignoreType seismic toss vs ghost', ai(4), bi(0), 'seismictoss', 'active',
		{ mask: 0, scaled: 0, class: 0, type: 'fighting' }, { ignoreType: true, ignoreImmunities: true });

	// 10. Magnet Rise (and ingrain cancels it)
	vol(bi(3), 'magnetrise');
	C('magnetrise active', ai(0), bi(3), 'earthquake', 'active', { mask: MAGNET_RISE, scaled: 60, class: 0, type: 'ground' });
	vol(bi(3), 'ingrain');
	C('ingrain cancels magnetrise', ai(0), bi(3), 'earthquake', 'active', { mask: 0, scaled: 60, class: 60, type: 'ground' });
	noVol(bi(3), 'ingrain'); noVol(bi(3), 'magnetrise');

	// 11. Gravity vs a flying-immunity row: the row is skipped, so the move lands
	// NEUTRAL (not SE)
	{
		const { rows } = require('./ai/data_typechart.js');
		const flyImmune = rows.filter((r) => r[1] === 2 && r[2] === 0).map((r) => r[0]);
		if (flyImmune.includes(4)) {
			battle.field.addPseudoWeather('gravity', ai(0));
			C('gravity overrides flying-immunity row', ai(0), bi(5), 'earthquake', 'active',
				{ mask: 0, scaled: 60, class: 60, type: 'ground' });
			battle.field.removePseudoWeather('gravity');
			C('no gravity, flying immune to ground', ai(0), bi(5), 'earthquake', 'active',
				{ mask: INEFF, scaled: 0, class: 0, type: 'ground' });
		} else {
			console.log('  info: no ground→flying immune row in the table; skipping gravity case');
		}
	}

	// 12. isTrapped
	{
		const p = ai(4);
		check('isTrapped base false', engine.isTrapped(p), false);
		p.addVolatile('trapped', p);
		check('isTrapped volatile', engine.isTrapped(p), true);
		p.removeVolatile('trapped');
		p.item = 'shedshell';
		p.volatiles.trapped = {};
		check('isTrapped shedshell false', engine.isTrapped(p), false);
		p.item = ''; delete p.volatiles.trapped;
		const foeActive = battle.sides[1].active[0];
		setAb(foeActive, 'shadowtag');
		check('isTrapped foe shadowtag', engine.isTrapped(p), true);
		setAb(foeActive, 'levitate');
	}

	// 13. speedRank ordering
	{
		const r = engine.speedRank();
		check('speedRank returns alive actives', r.length, 2);
		check('speedRank fastest first', r[0].getActionSpeed() >= r[1].getActionSpeed(), true);
	}

	console.log(failures ? `\n${failures} FAILURES` : 'ALL PASS');
	process.exit(failures ? 1 : 0);
}

main().catch((e) => { console.error('FATAL:', e); process.exit(1); });
