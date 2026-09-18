'use strict';
// Interactive terminal client for the trainer AI (dev-time only — the Java
// simulator speaks the stdio protocol in task1/INTEGRATION.md; this file is
// display-only and never touches the daemon).
//
//   node qwenwork/task3/play.js                 # menu-driven module choice
//   node qwenwork/task3/play.js full            # straight into basic+eval_attack+expert
//   node qwenwork/task3/play.js risky weather   # combos as args
//
// On a real terminal you get a cartridge-style HUD: both active Pokémon with
// HP bars and status, the trainer's remaining count, weather/field effects,
// game-style message text, and a 2x2 move grid driven by ARROW KEYS:
//   ←→↑↓  choose move   enter  confirm   p  Pokémon switch submenu   ctrl-c  quit
// Piped input (tests/CI) falls back to the old numbered text menus.
//
// Teams are hardcoded below (TEAMS.you fights TEAMS.cpu); bag = CPU_ITEMS.
const daemon = require('../task1/showdown_daemon.js');
const { TEAM_P1, TEAM_P2, pkm } = require('./ai_test.js');

const TEAMS = {
	you: TEAM_P1, // Snorlax / Gengar / Zapdos
	cpu: [
		...TEAM_P2, // Machoke / Onix / Gengar
		pkm('blissey', ['softboiled', 'icebeam', 'thunderbolt', 'toxic'], { item: 'leftovers' }),
	],
};
const CPU_ITEMS = ['full_restore', 'persimberry', 'x_special_attack', 'potion'];

const FLAGS = [
	['basic', 'immunity/OHKO/effect scoring baseline'],
	['eval_attack', 'reward the biggest damage score'],
	['expert', 'the 178-entry effect dispatch (133 routines)'],
	['setup_first_turn', '+2 (68.75%) classic setup moves, turn 1'],
	['risky', '+2 (50%) high-risk effects'],
	['prioritize_extremes', '+2 (60.9%) no-damage-calc moves'],
	['baton_pass', 'boost/setup with a bench to pass to'],
	['tag_strategy', 'partner/AoE scoring (singles quirks)'],
	['check_hp', 'HP-band discouragement'],
	['weather', '+5 weather setup on entry turn'],
	['harassment', '+2 (50%) status/hazard moves'],
];
const IDS = FLAGS.map(([id]) => id);

const REAL = process.stdout.write.bind(process.stdout); // real terminal (daemon hooks stdout)
const isTTY = !!process.stdin.isTTY;
const log = (s) => REAL(s + '\n'); // non-HUD channel: menus/tests still speak lines

// Session recorder: every daemon event + every choice we send, so a session
// can be reconstructed afterwards (override with PLAY_LOG=path).
const fs = require('node:fs');
const LOGF = process.env.PLAY_LOG || 'play_log.txt';
const rec = (s) => { try { fs.appendFileSync(LOGF, `[${new Date().toISOString()}] ${s}\n`); } catch {} };

// ---------------------------------------------------------------- TTY input
// One PTY write can carry several keys ("←\r"); parse all and FIFO the rest.
// Cartridges pace input; we must match that or one physical press equals two
// selections: terminals that send CRLF for Enter (two parsed keys in one
// buffer) and auto-repeat / split writes (same key twice within ~120 ms) are
// both collapsed to a single press.
const keyQ = [];
let lastKey = null;
let lastKeyAt = 0;
function parseKeys(s) {
	const out = [];
	for (let i = 0; i < s.length; i++) {
		const c = s[i];
		if (c === '\x1b' && s[i + 1] === '[') {
			const t = s[i + 2];
			if (t === 'C') out.push('right');
			else if (t === 'D') out.push('left');
			else if (t === 'A') out.push('up');
			else if (t === 'B') out.push('down');
			i += 2;
		} else if (c === '\x03' || c === 'q') out.push('quit');
		else if (c === '\r' || c === '\n') {
			out.push('enter');
			while (s[i + 1] === '\r' || s[i + 1] === '\n') i++; // CR LF = one Enter
		} else if (c === 'p') out.push('p');
		else if (c === 'x' || c === '\x7f') out.push('x');
	}
	return out;
}
function keyOnce() {
	if (keyQ.length) return Promise.resolve(keyQ.shift());
	return new Promise((resolve) => {
		process.stdin.setRawMode(true);
		process.stdin.resume();
		const onData = (buf) => {
			const raw = buf.toString();
			const now = Date.now();
			for (const k of parseKeys(raw)) {
				if (k === lastKey && now - lastKeyAt < 120) continue; // auto-repeat / sticky
				lastKey = k; lastKeyAt = now;
				keyQ.push(k);
			}
			rec(`key ${JSON.stringify(raw)} -> ${JSON.stringify(keyQ)}`);
			if (!keyQ.length) return;
			process.stdin.removeListener('data', onData);
			process.stdin.setRawMode(false);
			process.stdin.pause();
			resolve(keyQ.shift());
		};
		process.stdin.on('data', onData);
	});
}

// ---------------------------------------------------------------- battle HUD
const WEATHER_MSG = {
	RainDance: ['Rain', 'Rain is pouring down!'],
	SunnyDay: ['Sun', 'The sunlight turned harsh!'],
	Sandstorm: ['Sandstorm', 'A sandstorm kicked up!'],
	Hail: ['Hail', 'It began to hail!'],
};
const STATUS_TAG = { psn: 'PSN', tox: 'PSN', brn: 'BRN', par: 'PRZ', slp: 'SLP', frz: 'FRZ', confusion: 'CONF' };
const STATUS_MSG = {
	psn: 'was poisoned!', tox: 'was badly poisoned!', brn: 'was burned!',
	par: 'is paralyzed!', slp: 'fell asleep!', frz: 'was frozen solid!', confusion: 'became confused!',
};
// DS shows these on the cure too — without them a frozen mon that wins the
// gen4 20% pre-move thaw roll looks like it attacked out of freeze for free.
const CURE_MSG = {
	psn: 'is no longer poisoned!', tox: 'is no longer badly poisoned!', brn: 'is no longer burned!',
	par: 'is no longer paralyzed!', slp: 'woke up!', frz: 'thawed out!', confusion: 'snapped out of it!',
};
const STAT_NAME = { atk: 'Attack', def: 'Defense', spa: 'Sp. Atk', spd: 'Sp. Def', spe: 'Speed', accuracy: 'Accuracy', evasion: 'Evasiveness' };
const hpColor = (f) => (f >= 0.5 ? '\x1b[32m' : f >= 0.2 ? '\x1b[33m' : '\x1b[31m');

class Hud {
	constructor() {
		this.p2name = 'CPU'; this.p1name = 'You';
		this.size = { p1: 0, p2: 0 }; this.faints = { p1: 0, p2: 0 };
		this.foe = null; this.me = null; // {name,lvl,cur,max,status}
		this.weather = null; this.foeSide = new Set(); this.meSide = new Set();
		this.history = []; // turn blocks {n, msgs}; oldest dropped past 3 turns
	}
	msg(s) { let b = this.history[this.history.length - 1]; if (!b) { b = { n: 0, msgs: [] }; this.history.push(b); } b.msgs.push(s); }
	// Attach a follow-up effect to the message that caused it, so trimmed
	// windows never hide "X used Y!" behind its own "rose/crit/effective" lines.
	amend(t) { const b = this.history[this.history.length - 1]; const last = b && b.msgs[b.msgs.length - 1]; if (last && last.length + t.length < 90) b.msgs[b.msgs.length - 1] = last + ' ' + t; else this.msg(t); }
	who(side) { return side === 'p2' ? { mon: () => this.foe, tag: 'Foe ', you: false } : { mon: () => this.me, tag: 'Your ', you: true }; }
	setHp(mon, s) { if (!mon) return; if (/fnt/.test(s)) mon.cur = 0; else { const m = s.match(/^(\d+)\/(\d+)/); if (m) { mon.cur = +m[1]; mon.max = +m[2]; } } }
	update(line) {
		const p = line.slice(1).split('|');
		const cmd = p[0];
		const actor = (who) => { const m = /^(p\d)([a-z]): (.*)$/.exec(who || ''); if (!m) return null; const w = this.who(m[1]); return { w, name: m[3], side: m[1] }; };
		switch (cmd) {
		case 'turn': this.history.push({ n: +p[1], msgs: [] }); if (this.history.length > 3) this.history.shift(); break;
		case 'player': if (p[1] === 'p2') this.p2name = p[2]; else if (p[1] === 'p1') this.p1name = p[2]; break;
		case 'teamsize': this.size[p[1]] = +p[2]; break;
		case 'switch': case 'drag': {
			const a = actor(p[1]); if (!a) break;
			const lvl = /L(\d+)/.exec(p[2]);
			const mon = { name: a.name, lvl: lvl ? lvl[1] : '?', cur: 0, max: 0, status: null };
			this.setHp(mon, p[3] || '0/0');
			if (a.side === 'p2') { this.foe = mon; this.msg(`Foe ${a.name} appeared!`); }
			else { this.me = mon; this.msg(cmd === 'drag' ? `${a.name} was dragged out!` : `Go! ${a.name}!`); }
			break; }
		case 'faint': {
			const a = actor(p[1]); if (!a) break;
			this.faints[a.side]++; this.setHp(a.w.mon(), '0');
			this.msg(`${a.w.you ? 'Your' : 'Foe'} ${a.name} fainted!`);
			break; }
		case 'move': {
			const a = actor(p[1]); if (!a) break;
			this.lastAct = a; // stat lines by the same mover shorten to "Attack rose!"
			this.msg(`${a.w.you ? a.name : 'Foe ' + a.name} used ${p[2]}!`);
			break; }
		case '-damage': case '-heal': {
			let tgt = p[1];
			if (tgt.startsWith('[of] ')) tgt = tgt.slice(5); // recoil/secondary: bar must still move
			const a = actor(tgt); if (a) this.setHp(a.w.mon(), p[2]);
			break; }
		case '-status': {
			const a = actor(p[1]); if (!a || !STATUS_TAG[p[2]]) break;
			const mon = a.w.mon(); if (mon) mon.status = p[2];
			this.msg(`${a.w.you ? 'Your' : 'Foe'} ${a.name} ${STATUS_MSG[p[2]]}`);
			break; }
		case '-curestatus': {
			const a = actor(p[1]); if (!a) break;
			const mon = a.w.mon(); if (mon && mon.status === p[2]) mon.status = null;
			if (p[3] !== '[silent]' && CURE_MSG[p[2]]) {
				this.msg(`${a.w.you ? 'Your' : 'Foe'} ${a.name} ${CURE_MSG[p[2]]}`);
			}
			break; }
		case '-weather': {
			const w = WEATHER_MSG[p[1]];
			if (p[3] === '[end]') { this.weather = null; if (w) this.msg(`The ${w[0].toLowerCase()} faded…`); }
			else if (p[3] !== '[upkeep]') { this.weather = p[1]; if (w) this.msg(w[1]); }
			break; }
		case '-sidestart': {
			const side = /^p\d/.exec(p[1])[0]; const set = side === 'p2' ? this.foeSide : this.meSide;
			const eff = p[2].replace(/^move: /, ''); set.add(eff); this.msg(`${eff} scattered around ${side === 'p2' ? 'your foe' : 'your team'}!`);
			break; }
		case '-sideend': { const side = /^p\d/.exec(p[1])[0]; (side === 'p2' ? this.foeSide : this.meSide).delete(p[2].replace(/^move: /, '')); break; }
		case '-boost': case '-unboost': {
			const a = actor(p[1]); if (!a) break;
			const same = this.lastAct && this.lastAct.side === a.side && this.lastAct.name === a.name;
			const stat = STAT_NAME[p[2]] || p[2];
			this.amend(same ? `${stat} ${cmd === '-boost' ? 'rose' : 'fell'}!`
				: `${a.w.you ? 'Your' : 'Foe'} ${a.name}’s ${stat} ${cmd === '-boost' ? 'rose' : 'fell'}!`);
			break; }
		case '-crit': this.amend('A critical hit!'); break;
		case '-supereffective': this.amend('It’s super effective!'); break;
		case '-resisted': this.amend('It’s not very effective…'); break;
		case '-item': { const a = actor(p[1]); if (a) this.msg(`${a.w.you ? 'Your' : 'Foe'} ${a.name} ate its ${p[2]}!`); break; }
		case '-immune': {
			const a = actor(p[1]);
			if (a) this.amend(`But it doesn’t affect ${a.w.you ? 'your ' + a.name : 'Foe ' + a.name}…`);
			break; }
		case '-ability': { if (p[3] === '[immune]') this.amend(`${p[2]}! It doesn’t hit…`); break; }
		case '-singleturn': {
			const a = actor(p[1]);
			if (a && /Protect|Detect|Endure/.test(p[2])) this.amend(`${a.w.you ? a.name : 'Foe ' + a.name} is protecting itself!`);
			break; }
		case '-activate': {
			const a = actor(p[1]);
			if (a && p[2] === 'Protect') this.amend(`${a.w.you ? a.name : 'Foe ' + a.name} protected itself!`);
			break; }
		case '-miss': { const a = actor(p[1]); if (a) this.amend(`${a.w.you ? a.name : 'Foe ' + a.name}’s attack missed!`); break; }
		case '-fail': this.amend('But it failed!'); break;
		case '-start': { const a = actor(p[1]); if (a && p[2] === 'Substitute') this.msg(`${a.w.you ? 'Your' : 'Foe'} ${a.name} made a Substitute!`); break; }
		case '-end': { const a = actor(p[1]); if (a && p[2] === 'Substitute') this.msg(`${a.w.you ? 'Your' : 'Foe'} ${a.name}’s Substitute faded!`); break; }
		case '-drain': {
			const t = actor(p[1]); const s = actor(p[2]);
			const m = /^(\d+)\//.exec(p[3] || '');
			if (t && t.w.mon()) { const mo = t.w.mon(); if (m && mo.max) mo.cur = Math.max(0, mo.cur - +m[1]); }
			if (s && s.w.mon()) { const mo = s.w.mon(); if (m && mo.max) mo.cur = Math.min(mo.max, mo.cur + +m[1]); }
			if (s) this.amend(`${s.w.you ? s.name : 'Foe ' + s.name} drained ${t ? t.name : 'its target'}’s HP!`);
			break; }
		}
	}
	ball(s, n) { // pokemon balls: alive/fainted
		const fainted = Math.min(this.faints['p' + n], s);
		return '●'.repeat(Math.max(0, s - fainted)) + '○'.repeat(fainted) + ` (${Math.max(0, s - fainted)} left)`;
	}
	hpBar(mon) {
		if (!mon) return ' '.repeat(20);
		const f = mon.max ? mon.cur / mon.max : 0;
		const on = Math.round(f * 18);
		return `${hpColor(f)}${'█'.repeat(on)}\x1b[0m${'░'.repeat(18 - on)} ${String(Math.max(0, mon.cur)).padStart(3)}/${mon.max}`;
	}
	render(menu) {
		const W = 52;
		const f = [];
		f.push(`${this.p2name}: ${this.ball(this.size.p2 || 1, 2)}`);
		f.push('─'.repeat(W));
		const fm = this.foe; const mm = this.me;
		f.push(fm ? `Foe ${fm.name.padEnd(16)} Lv${fm.lvl} ${fm.status ? `[${STATUS_TAG[fm.status]}]` : ''.padEnd(5)}` : '');
		f.push('  ' + this.hpBar(fm));
		f.push('');
		f.push(mm ? `Your ${mm.name.padEnd(17)} Lv${mm.lvl} ${mm.status ? `[${STATUS_TAG[mm.status]}]` : ''.padEnd(5)}` : '');
		f.push('  ' + this.hpBar(mm));
		f.push('─'.repeat(W));
		const fx = [this.weather ? `Weather: ${WEATHER_MSG[this.weather]?.[0] || this.weather}` : null,
			this.meSide.size ? `Your field: ${[...this.meSide].join(',')}` : null,
			this.foeSide.size ? `Foe field: ${[...this.foeSide].join(',')}` : null].filter(Boolean);
		f.push(' ' + (fx.join('   ·   ') || ''));
		// Battle history: last three turns, newest first, filled only as far
		// as the terminal has rows left — the move menu must stay on-screen.
		const menuLines = menu ? menu.render() : [' …'];
		const budget = Math.max(5, (process.stdout.rows || 24) - 11 - menuLines.length);
		const COLS = process.stdout.columns || 80;
		const rowsOf = (m) => 1 + Math.floor((3 + m.length) / COLS); // wrapped lines count
		const blocks = [];
		let used = 0;
		for (let bi = this.history.length - 1; bi >= 0; bi--) {
			const b = this.history[bi];
			if (!b.msgs.length) continue; // header for a turn still resolving is noise
			const head = b.n ? ` ── Turn ${b.n} ${'─'.repeat(Math.max(0, 40 - String(b.n).length - 8))}` : ' ── Battle start ──────────────────────';
			// NEVER drop a single line within a block — the first casualty was
			// always the player's own "Snorlax used X!" mid-block line. If space
			// runs out, the whole OLDER block goes (break); the newest block
			// trims from its top only when it alone cannot fit.
			const costOf = (ms) => 1 + ms.reduce((n, m) => n + rowsOf(m), 0);
			let msgs = b.msgs;
			if (blocks.length) { if (used + costOf(msgs) > budget) break; }
			else while (costOf(msgs) > budget && msgs.length > 1) msgs = msgs.slice(1);
			used += costOf(msgs);
			blocks.unshift([head, ...msgs.map((m) => '   ' + m)]);
		}
		for (const bl of blocks) f.push(...bl);
		f.push(...menuLines);
		return f;
	}
	draw(menu) {
		const lines = this.render(menu);
		const body = lines.map((l) => l + '\x1b[K').join('\r\n');
		REAL('\x1b[2J\x1b[H' + body + '\r\n');
	}
}

// move grid / list menus (TTY). menu.pick() -> 'move N'|'switch N'|'default'|'quit'
class MoveMenu {
	constructor(hud, moves, side) { this.hud = hud; this.moves = moves; this.side = side; this.cursor = moves.findIndex((m) => m.pp > 0 && !m.disabled); if (this.cursor < 0) this.cursor = 0; }
	render() {
		const c = this.cursor;
		const cell = (i) => {
			const m = this.moves[i];
			if (!m) return [' '.repeat(22), ' '.repeat(22)];
			const dis = m.pp <= 0 || m.disabled;
			const code = i === c ? '\x1b[7m' : dis ? '\x1b[90m' : '\x1b[0m';
			return [code + (i + 1 + ' ' + m.move).slice(0, 22).padEnd(22) + '\x1b[0m',
				code + (m.pp + '/' + m.maxpp).padStart(22) + '\x1b[0m'];
		};
		const cells = [0, 1, 2, 3].map(cell);
		const B = (a, b, d) => a + '─'.repeat(22) + b + '─'.repeat(22) + d;
		return [
			B('┌', '┬', '┐'),
			`│${cells[0][0]}│${cells[1][0]}│`,
			`│${cells[0][1]}│${cells[1][1]}│`,
			B('├', '┼', '┤'),
			`│${cells[2][0]}│${cells[3][0]}│`,
			`│${cells[2][1]}│${cells[3][1]}│`,
			B('└', '┴', '┘'),
			' ←→↑↓ move · enter OK · p Pokémon · ctrl-c quit',
		];
	}
	async pick() {
		for (;;) {
			this.hud.draw(this);
			const k = await keyOnce();
			if (k === 'quit') return 'quit';
			if (k === 'p') return 'pkmn';
			const n = this.moves.length;
			if (k === 'right') this.cursor = (this.cursor % 2) === 0 ? this.cursor + 1 : this.cursor;
			else if (k === 'left') this.cursor = (this.cursor % 2) === 1 ? this.cursor - 1 : this.cursor;
			else if (k === 'down') this.cursor = this.cursor + 2 < n ? this.cursor + 2 : this.cursor;
			else if (k === 'up') this.cursor = this.cursor - 2 >= 0 ? this.cursor - 2 : this.cursor;
			else if (k === 'enter') {
				const m = this.moves[this.cursor]; if (!m) continue;
				if (m.pp <= 0 || m.disabled) { this.hud.msg('But it has no PP!'); continue; }
				return `move ${this.cursor + 1}`;
			}
		}
	}
}

class ListMenu {
	constructor(hud, title, items, allowBack) { this.hud = hud; this.title = title; this.items = items; this.allowBack = !!allowBack; this.cursor = items.findIndex((x) => x.ok); if (this.cursor < 0) this.cursor = 0; }
	render() {
		const out = [`── ${this.title} ${'─'.repeat(Math.max(0, 44 - this.title.length))}`];
		this.items.forEach((it, i) => {
			const pre = i === this.cursor ? '\x1b[7m' : '';
			out.push(`${pre} ${it.label}${' '.repeat(Math.max(0, 44 - it.label.length))}\x1b[0m`);
		});
		const keys = this.allowBack ? ' ↑↓ choose · enter OK · p back to moves' : ' ↑↓ choose · enter OK';
		out.push(' ' + keys);
		return out;
	}
	async pick() {
		for (;;) {
			this.hud.draw(this);
			const k = await keyOnce();
			if (k === 'quit') return 'quit';
			if (k === 'p') return this.allowBack ? 'moves' : null;
			if (k === 'up') { let c = this.cursor; do { c = (c - 1 + this.items.length) % this.items.length; } while (!this.items[c].ok && c !== this.cursor); this.cursor = c; }
			else if (k === 'down') { let c = this.cursor; do { c = (c + 1) % this.items.length; } while (!this.items[c].ok && c !== this.cursor); this.cursor = c; }
			else if (k === 'enter') return this.items[this.cursor].value;
		}
	}
}

// ---------------------------------------------------------------- non-TTY input (tests/CI keep working)
let rl = null;
const inputQ = []; const waiters = []; let eof = false;
async function ask(prompt) {
	log(prompt);
	if (inputQ.length) return inputQ.shift();
	if (eof) return null;
	return new Promise((resolve) => waiters.push(resolve));
}

function pkmnItems(req) {
	// NOTE: this fork's forceSwitch table is per-ACTIVE-slot ([true] in
	// singles), NOT a party table — availability comes from the party data:
	// not active and not fainted (gen4 requests have no `fainted` field;
	// fainted shows as condition "0 fnt").
	return req.side.pokemon.map((m, i) => ({
		ok: !m.active && !/fnt/.test(m.condition || ''),
		label: `${i + 1} ${m.ident.replace(/^p\d: /, '').padEnd(14)} ${m.condition}${m.active ? ' (out)' : ''}`,
		value: `switch ${i + 1}`,
	}));
}

async function battleMenuTTY(hud, req) {
	for (;;) {
		if (req.teamPreview) return (await new ListMenu(hud, 'Send which Pokémon?', req.side.pokemon.map((m, i) => ({ ok: !/fnt/.test(m.condition || ''), label: `${i + 1} ${m.ident}`, value: `team ${i + 1}` })), false).pick());
		if (req.forceSwitch && req.forceSwitch.some(Boolean)) {
			const c = await new ListMenu(hud, 'Choose next Pokémon!', pkmnItems(req), false).pick();
			if (c === 'quit') return 'quit';
			return c;
		}
		const mv = req.active[0].moves;
		const choice = await new MoveMenu(hud, mv, req.side).pick();
		if (choice === 'quit') return 'quit';
		if (choice === 'pkmn') {
			const items = pkmnItems(req);
			const c2 = await new ListMenu(hud, 'Switch to…', items, true).pick();
			if (c2 === 'moves') continue;
			if (c2 === 'quit') return 'quit';
			return c2;
		}
		return choice;
	}
}

async function battleMenuLines(hud, req) { // legacy numbered menus
	for (;;) {
		if (req.teamPreview) { req.side.pokemon.forEach((m, i) => log(`  ${i + 1}. ${m.ident}`)); const a = (await ask('lead? > ') || '').trim(); if (/^\d+$/.test(a)) return `team ${a}`; continue; }
		if (req.forceSwitch && req.forceSwitch.some(Boolean)) {
			pkmnItems(req).forEach((it) => it.ok && log('  ' + it.label));
			const a = (await ask('switch to? > ') || '').trim(); if (/^\d+$/.test(a)) return `switch ${a}`;
			if (/^d$/i.test(a)) return 'default'; continue;
		}
		req.active[0].moves.forEach((m, i) => log(`  ${i + 1}. ${m.move}${m.disabled ? ' (disabled)' : ''} ${m.pp}/${m.maxpp}`));
		req.side.pokemon.forEach((m, i) => !m.active && !/fnt/.test(m.condition || '') && log(`  s${i + 1}. switch → ${m.ident.replace(/^p\d: /, '')} (${m.condition})`));
		const a = ((await ask('move 1-4 / sN / d > ') || '')).trim();
		if (/^[1-4]$/.test(a)) return `move ${a}`;
		const w = a.match(/^s?witch (\d+)$/i) || a.match(/^s([1-9])$/);
		if (w) return `switch ${w[1]}`;
		if (/^d$/i.test(a)) return 'default';
		log('  (a number, sN switch, or d=default)');
	}
}

// ---------------------------------------------------------------- battle driver
let battleSeq = 0;
async function playBattle(ids) {
	const id = `play${++battleSeq}`;
	const hud = new Hud();
	let pending = null;
	const deliver = (ev) => { if (pending) { const w = pending; pending = null; w.resolve(ev); } };
	let drawQueued = false;
	const real = process.stdout.write;
	process.stdout.write = (chunk) => {
		for (const line of String(chunk).split('\n')) {
			if (!line) continue;
			let o = null;
			try { o = JSON.parse(line); } catch { continue; }
			rec(`[${o.id}] ` + JSON.stringify(o));
			if (o.type === 'error') { hud.msg('[error] ' + o.error); if (!isTTY) log('[error] ' + o.error); continue; }
			if (o.type === 'win') { deliver({ type: 'win', result: o.result }); continue; }
			if (o.type === 'ai') continue; // HUD shows it as a move message
			if (o.type !== 'line' || o.side !== 'p1') continue;
			const l = o.line;
			if (l.startsWith('|request|')) { deliver({ type: 'req', req: JSON.parse(l.slice('|request|'.length)) }); continue; }
			if (l.startsWith('|t:|') || l === '|') continue;
			if (isTTY) { hud.update(l); if (!drawQueued) { drawQueued = true; setImmediate(() => { drawQueued = false; hud.draw(null); }); } }
			else log('  ' + l.slice(1));
		}
		return true;
	};
	try {
		daemon.handle({ id, op: 'start', seed: [7, 3, 11, 5], name1: 'You', team1: TEAMS.you, name2: 'CPU', team2: TEAMS.cpu, ai: ids, aiItems: CPU_ITEMS });
		rec(`[${id}] START ai=[${ids.join(',')}] items=[${CPU_ITEMS.join(',')}]`);
		for (;;) {
			const ev = await new Promise((resolve) => { pending = { resolve }; });
			if (ev.type === 'win') {
				const r = ev.result === 'You' ? `You defeated ${hud.p2name}!` : ev.result === 'tie' ? 'It’s a tie!' : `${ev.result} won. You blacked out!`;
				hud.msg(r);
				if (isTTY) { hud.draw(null); await keyOnce(); }
				return ev.result;
			}
			const req = ev.req;
			// {"wait":true} is informational — the fork needs NO ack. Answering
			// (e.g. 'default') re-enters side.choose(), which clears the finished
			// choice and queues a default move for the NEXT turn: the human's one
			// Enter would look like two clicks (their turn, then an auto turn).
			if (req.wait) continue;
			if (isTTY) hud.draw(null);
			const choice = await (isTTY ? battleMenuTTY(hud, req) : battleMenuLines(hud, req));
			if (choice === 'quit') return 'quit';
			rec(`> ${choice}`); daemon.handle({ id, op: 'request', side: 'p1', choice });
		}
	} finally {
		daemon.handle({ id, op: 'cancel', side: 'p1' });
		process.stdout.write = real;
		if (isTTY) REAL('\x1b[0m');
	}
}

function parseIds(pick) {
	let ids = pick.replace(/\+/g, ',').split(/[,\s]+/).filter(Boolean);
	if (!ids.length) ids = ['full'];
	if (ids.length === 1 && ids[0] === 'none') ids = [];
	return ids;
}

async function moduleMenuTTY() {
	const items = [
		{ ok: true, label: 'full — basic + eval_attack + expert (Frontier)', value: 'full' },
		...IDS.map((id) => ({ ok: true, label: `${id.padEnd(20)}— ${FLAGS.find((f) => f[0] === id)[1]}`, value: id })),
		{ ok: true, label: 'all 11 — every module at once', value: IDS.join(',') },
		{ ok: true, label: 'none — random legal moves', value: 'none' },
	];
	const c = await new ListMenu({ msg() {}, draw(menu) { REAL('\x1b[2J\x1b[H' + menu.render().map((l) => l + '\x1b[K').join('\r\n') + '\x1b[K\r\n'); }, render: () => [] }, 'CPU trainer AI', items, false).pick();
	return c === 'quit' ? null : c;
}

async function main() {
	log(`you: ${TEAMS.you.map((m) => m.species).join('/')}  vs  cpu: ${TEAMS.cpu.map((m) => m.species).join('/')}  bag: ${CPU_ITEMS.join(', ')}`);
	let seed = process.argv.slice(2).join(',').trim();
	for (;;) {
		let pick = seed; seed = '';
		if (isTTY) {
			if (!pick) { const c = await moduleMenuTTY(); if (c === null) break; pick = c; }
		} else {
			if (!rl) {
				rl = require('node:readline').createInterface({ input: process.stdin, output: process.stderr });
				rl.on('line', (l) => { const w = waiters.shift(); w ? w(l) : inputQ.push(l); });
				rl.on('close', () => { eof = true; for (const w of waiters.splice(0)) w(null); });
			}
			if (!pick) {
				const ans = await ask('\nCPU modules [full, q=quit] > ');
				if (ans === null) break;
				pick = ans.trim() || 'full';
				if (/^(q|quit|exit)$/i.test(pick)) break;
			}
		}
		let ids;
		try { ids = parseIds(pick); } catch { continue; }
		if (!ids.every((x) => x === 'full' || x === 'none' || IDS.includes(x))) {
			log(`unknown id(s): ${ids.filter((x) => !(x === 'full' || x === 'none' || IDS.includes(x))).join(',')}`);
			continue;
		}
		log(`\n▶ CPU runs [${ids.join(', ') || 'no modules — random legal'}]`);
		let result;
		try { result = await playBattle(ids); } catch (e) { log(`battle error: ${e && e.message || e}`); continue; }
		if (result === 'quit') break;
		log(`\n★ ${result === 'You' ? 'You won' : result === 'tie' ? 'Tie' : `${result} won`} (vs [${ids.join(', ')}])`);
	}
	if (rl) rl.close();
	process.exit(0);
}

module.exports = { Hud, parseKeys };
if (require.main === module) main().catch((e) => { REAL(`\x1b[0mFATAL: ${e && e.stack || e}\n`); process.exit(1); });
