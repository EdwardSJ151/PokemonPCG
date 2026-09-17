// Dev-time generator: decomp data -> committed static AI tables.
//   node qwenwork/task3/gen_data.js
// Reads pokeplatinum/res/moves/*/data.json and res/items/data/*.json and writes
// qwenwork/task3/ai/data_moves.js and ai/data_items.js (CommonJS, committed).
// Run again only if the decomp data changes; the AI requires the output, not this script.
'use strict';
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..', '..');
const DECOMP = path.join(ROOT, 'pokeplatinum');
const OUT = path.join(__dirname, 'ai');
fs.mkdirSync(OUT, { recursive: true });
const compact = (name) => name.toLowerCase().replace(/_/g, '');
const ITEM_ALIAS = { adamant_orb: 'adamantorb' };
const MOVE_ALIAS = { smelling_salt: 'saltcure' };
const shell = (cmd) => execSync(cmd, { cwd: ROOT, encoding: 'utf8' });

// Probe the fork dex once: existence for candidate move/item ids.
function forkCheck(moves, items) {
	const script = `
const Sim = require('./pokemon-showdown/dist/sim');
const dex = (Sim.Dex || Sim).forGen(4);
const out = {};
for (const m of ${JSON.stringify(moves)}) out['m:' + m] = !!dex.moves.get(m).exists;
for (const i of ${JSON.stringify(items)}) out['i:' + i] = !!dex.items.get(i).exists;
console.log(JSON.stringify(out));`;
	const tmp = path.join(ROOT, '.gen_check.js');
	fs.writeFileSync(tmp, script);
	try {
		return JSON.parse(shell(`node .gen_check.js`));
	} finally {
		fs.unlinkSync(tmp);
	}
}

// ---------- moves ----------
const moveFiles = fs.readdirSync(path.join(DECOMP, 'res/moves'))
	.filter((d) => fs.existsSync(path.join(DECOMP, 'res/moves', d, 'data.json')));
const moveRows = [];
for (const dir of moveFiles) {
	const j = JSON.parse(fs.readFileSync(path.join(DECOMP, 'res/moves', dir, 'data.json'), 'utf8'));
	const forkId = MOVE_ALIAS[dir] || compact(dir);
	moveRows.push({ decomp: dir, forkId, effect: j.effect && j.effect.type });
}
const moveCheck = forkCheck(moveRows.map((r) => r.forkId), []);
const moveMiss = [];
const movesOut = {};
for (const r of moveRows) {
	if (moveCheck['m:' + r.forkId]) movesOut[r.forkId] = r.effect;
	else moveMiss.push(`${r.decomp} -> ${r.forkId}`);
}

// ---------- items ----------
const CURE_BITS = {
	healSleep: 'slp',
	healPoison: 'psn',
	healBurn: 'brn',
	healFreeze: 'frz',
	healParalysis: 'par',
	healConfusion: 'confusion',
};
const STAGE_KEYS = ['atkStages', 'defStages', 'spatkStages', 'spdefStages', 'speedStages', 'accStages'];
const itemFiles = fs.readdirSync(path.join(DECOMP, 'res/items/data')).filter((f) => f.endsWith('.json'));
const itemRows = [];
for (const f of itemFiles) {
	const j = JSON.parse(fs.readFileSync(path.join(DECOMP, 'res/items/data', f), 'utf8'));
	const decomp = f.replace('.json', '');
	const forkId = ITEM_ALIAS[decomp] || compact(decomp);
	const p = j.itemUseParams || {};
	const cures = Object.keys(CURE_BITS).filter((k) => p[k]).map((k) => CURE_BITS[k]);
	const stages = {};
	for (const k of STAGE_KEYS) if (p[k]) stages[compact(k).replace('stages', '')] = p[k];
	let category;
	if (p.hpRestored === -1 && cures.length === 6) category = 'FULL_RESTORE';
	else if (p.hpRestored) category = 'RECOVER_HP';
	else if (cures.length) category = 'RECOVER_STATUS';
	else if (Object.keys(stages).length) category = 'STAT_BOOSTER';
	else if (p.guardSpec) category = 'GUARD_SPEC';
	else if (j.battlePocket === 'BATTLE_POCKET_MASK_POKE_BALLS') category = 'POKEBALL';
	else category = 'MAX';
	const hold = j.holdEffect && j.holdEffect !== 'HOLD_EFFECT_NONE' ? compact(j.holdEffect.replace('HOLD_EFFECT_', '')) : null;
	let holdType = null;
	if (/^HOLD_EFFECT_ARCEUS_/.test(j.holdEffect)) {
		holdType = compact(j.holdEffect.replace('HOLD_EFFECT_ARCEUS_', ''));
	}
	itemRows.push({
		decomp, forkId, category,
		hp: p.hpRestored || 0, revive: !!p.revive, cures, stages,
		guardSpec: !!p.guardSpec,
		flingPower: j.flingPower || 0,
		naturalGiftType: j.naturalGiftType ? compact(j.naturalGiftType.replace('TYPE_', '')) : null,
		effectParam: j.effectParam || 0,
		hold, holdType,
	});
}
const makeItemRec = (r) => {
	const rec = { category: r.category };
	if (r.hp) rec.hp = r.hp;
	if (r.revive) rec.revive = true;
	if (r.effectParam) rec.effectParam = r.effectParam;
	if (r.cures.length) rec.cures = r.cures;
	if (Object.keys(r.stages).length) rec.stages = r.stages;
	if (r.guardSpec) rec.guardSpec = true;
	if (r.flingPower) rec.flingPower = r.flingPower;
	if (r.naturalGiftType) rec.naturalGiftType = r.naturalGiftType;
	if (r.hold) rec.hold = r.hold;
	if (r.holdType) rec.holdType = r.holdType;
	return rec;
};
// forkId is the compact decomp name (alias-resolved); it equals the fork's own
// id for items the fork knows and is the natural key for the rest (the AI's
// item bag references items by these keys)
const itemsOut = {};
const itemMiss = [];
for (const r of itemRows) {
	if (itemsOut[r.forkId]) { itemMiss.push(`${r.decomp} -> ${r.forkId} (duplicate key)`); continue; }
	itemsOut[r.forkId] = makeItemRec(r);
}

// ---------- type chart ----------
// Parse the decomp's sTypeMatchupMultipliers (battle_lib.c:2399-2517) verbatim:
// rows [atkType, defType, mul] with mul in {0=immune, 5=NVE, 20=SE}; the 0xFE
// sentinel marks the Normal/Fighting-vs-Ghost immunity rows the AI's
// Foresight/Scrappy handling treats as a batch (see engine.js eff()).
const typeNames = fs.readFileSync(path.join(DECOMP, 'generated/pokemon_types.txt'), 'utf8')
	.trim().split('\n')
	.map((n) => n.trim())
	.filter((n) => n && !n.startsWith('NUM_'))
	.map((n) => {
		const t = n.toLowerCase().replace(/^type_/, '');
		return t === 'mystery' ? '???' : t;
	});
const typeNameToIdx = {};
typeNames.forEach((n, i) => {
	typeNameToIdx[n.toUpperCase().replace(/\?\?\?/g, 'MYSTERY')] = i;
});

const libSrc = fs.readFileSync(path.join(DECOMP, 'src/battle/battle_lib.c'), 'utf8');
const tblStart = libSrc.indexOf('sTypeMatchupMultipliers');
const tblBody = libSrc.slice(libSrc.indexOf('{', tblStart) + 1, libSrc.indexOf('{ 0xFF', tblStart));
const MUL_VALS = { TYPE_MULTI_IMMUNE: 0, TYPE_MULTI_NOT_VERY_EFF: 5, TYPE_MULTI_SUPER_EFF: 20 };
const chartRows = [];
let chartSentinel = -1;
for (const line of tblBody.split('\n')) {
	const m = line.match(/\{([^}]*)\}/);
	if (!m) continue;
	const parts = m[1].split(',').map((s) => s.trim()).filter(Boolean);
	if (parts[0] === '0xFE') {
		chartSentinel = chartRows.length;
		continue;
	}
	chartRows.push([typeNameToIdx[parts[0].replace(/^TYPE_/, '').toUpperCase()], typeNameToIdx[parts[1].replace(/^TYPE_/, '').toUpperCase()], MUL_VALS[parts[2]]]);
}
fs.writeFileSync(path.join(OUT, 'data_typechart.js'), `// Generated by gen_data.js from pokeplatinum/src/battle/battle_lib.c sTypeMatchupMultipliers.
// types[i] = fork type id; rows[i] = [atkType, defType, mul] with mul in {0,5,20}.
// sentinel = index of the 0xFE row; rows after it are the Foresight/Scrappy-batched
// Normal/Fighting-vs-Ghost immunities.
'use strict';
module.exports = { types: ${JSON.stringify(typeNames)}, rows: ${JSON.stringify(chartRows)}, sentinel: ${chartSentinel} };
`);
console.log(`typechart: ${typeNames.length} types, ${chartRows.length} rows, sentinel at ${chartSentinel}`);

const moveTable = `// Generated by gen_data.js from pokeplatinum/res/moves/*/data.json.
// {[fork move id]: decomp BATTLE_EFFECT_* name} — dispatch key for the AI's
// move-effect checks. Moves the fork lacks are omitted.
'use strict';
module.exports = ${JSON.stringify(movesOut, null, 1)};
`;
const itemTable = `// Generated by gen_data.js from pokeplatinum/res/items/data/*.json.
// {[fork item id]: AI item record}. category: FULL_RESTORE | RECOVER_HP |
// RECOVER_STATUS | STAT_BOOSTER | GUARD_SPEC | POKEBALL | MAX.
// hp: -1 = full heal; cures: engine status/volatile ids; stages: engine boost keys.
'use strict';
module.exports = ${JSON.stringify(itemsOut, null, 1)};
`;
fs.writeFileSync(path.join(OUT, 'data_moves.js'), moveTable);
fs.writeFileSync(path.join(OUT, 'data_items.js'), itemTable);
console.log(`moves: ${Object.keys(movesOut).length} in, ${moveMiss.length} missed: ${moveMiss.join(', ') || 'none'}`);
console.log(`items: ${Object.keys(itemsOut).length} in, ${itemMiss.length} missed: ${itemMiss.join(', ') || 'none'}`);
