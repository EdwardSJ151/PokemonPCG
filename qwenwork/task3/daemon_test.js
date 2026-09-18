'use strict';
// Daemon regression (PLAN.md validation plan, T0 + T1.5 re-proof):
//   a) playerxplayer (no `ai` field) stays byte-compatible: two concurrent
//      battle ids per run in ONE process, same seed twice ⇒ identical event
//      streams (ids normalized), zero error events, no `ai` events.
//   b) unknown AI module id ⇒ error event, no battle started (fail loud).
//   c) (once basic.js exists) AI mode: daemon answers p2 itself ({type:'ai'}
//      events), a Java `request` op for p2 is rejected, and the battle still
//      completes with the harness answering only p1.
//
// Driven IN-PROCESS via the daemon's exported handle() — the stdio bootstrap
// is require.main-gated; events are captured by wrapping process.stdout.write
// for the duration of a run.
//
//   node qwenwork/task3/daemon_test.js
const fs = require('fs');
const path = require('path');
const daemon = require('../task1/showdown_daemon.js');
const { TEAM_P1, TEAM_P2 } = require('./ai_test.js');

// Run the given battle ids in this process, capturing every event. Default
// policy answers every non-omni |request| line with 'default' via
// setImmediate (never re-enter handle() from inside its own write). opts:
//   answerSide: 'p1' — only that side's requests are harness-answered
//               (the other side must be answered by the daemon's AI).
//   onRequest(obj): observer hook per captured request event.
function daemonRun(tag, ids, startExtra = () => ({}), opts = {}) {
	return new Promise((resolve, reject) => {
		const events = [];
		const won = {};
		let settled = false;
		const real = process.stdout.write.bind(process.stdout);
		const finish = () => {
			if (settled) return;
			settled = true;
			process.stdout.write = real;
			clearTimeout(guard);
			const norm = events.map((o) => {
				const c = { ...o };
				if (c.id) c.id = c.id.replace(tag, 'T');
				if (typeof c.line === 'string') c.line = c.line.replace(new RegExp(tag, 'g'), 'T');
				return c;
			});
			resolve({ won, events: norm, rawEvents: events });
		};
		const guard = setTimeout(() => {
			process.stdout.write = real;
			reject(new Error(`timeout: won ${JSON.stringify(won)} of ${ids.join(',')}`));
		}, 60000);
		const parse = (chunk) => {
			for (const line of String(chunk).split('\n')) {
				if (!line) continue;
				let obj = null;
				try { obj = JSON.parse(line); } catch { continue; }
				events.push(obj);
				if (obj.type === 'line' && obj.side && obj.side !== 'omni'
					&& typeof obj.line === 'string' && obj.line.startsWith('|request|')) {
					if (opts.onRequest) opts.onRequest(obj);
					if (!opts.answerSide || obj.side === opts.answerSide) {
						// the battle may close before the deferred reply lands —
						// requireRec would throw 'battle already ended' (an error
						// event, and run-dependent stream noise): guard liveness.
						setImmediate(() => {
							const rec = daemon.battles.get(obj.id);
							if (rec && !rec.closed) {
								daemon.handle({ id: obj.id, op: 'request', side: obj.side, choice: 'default' });
							}
						});
					}
				} else if (obj.type === 'win' && ids.includes(obj.id)) {
					won[obj.id] = obj.result;
					if (ids.every((i) => won[i] !== undefined)) setImmediate(finish);
				}
			}
			return true;
		};
		process.stdout.write = parse;
		for (const id of ids) {
			daemon.handle({
				id, op: 'start', formatid: 'gen4customgame', seed: [7, 3, 11, 5],
				name1: 'A', team1: TEAM_P1, name2: 'B', team2: TEAM_P2, ...startExtra(id),
			});
		}
	});
}

let ok = true;
const chk = (cond, msg) => { console.log(`${cond ? 'ok  ' : 'FAIL'}  ${msg}`); if (!cond) ok = false; };

(async () => {
	// (a) playerxplayer: concurrency + cross-run byte-compat + no ai/error events
	const r1 = await daemonRun('r1', ['r1x', 'r1y']);
	const r2 = await daemonRun('r2', ['r2x', 'r2y']);
	chk(Object.keys(r1.won).length === 2, `two concurrent battles completed (${r1.won.r1x} / ${r1.won.r1y})`);
	chk(!r1.rawEvents.some((o) => o.type === 'ai'), 'no ai events in playerxplayer');
	chk(!r1.rawEvents.some((o) => o.type === 'error'), 'zero error events in playerxplayer');
	// compare per battle id (interleaving order between concurrent ids is
	// scheduling-dependent; the per-id sequence must be identical)
	const evsFor = (r, id, tag) => r.rawEvents.filter((o) => o.id === id).map((o) => {
		const c = { ...o };
		c.id = c.id.replace(tag, 'T');
		// |t:| lines carry real wall-clock seconds — normalize; the rest of
		// a seeded gen4 battle is fully deterministic.
		if (typeof c.line === 'string') c.line = c.line.replace(new RegExp(tag, 'g'), 'T').replace(/\|t:\|\d+/g, '|t:|');
		return c;
	});
	chk(JSON.stringify(evsFor(r1, 'r1x', 'r1')) === JSON.stringify(evsFor(r2, 'r2x', 'r2')),
		`same seed twice ⇒ identical per-battle streams (${evsFor(r1, 'r1x', 'r1').length} events)`);
	chk(JSON.stringify(evsFor(r1, 'r1y', 'r1')) === JSON.stringify(evsFor(r2, 'r2y', 'r2')),
		'second concurrent battle also identical across runs');

	// (b) fail loud on unknown module id
	const capB = [];
	const realB = process.stdout.write.bind(process.stdout);
	process.stdout.write = (c) => {
		for (const l of String(c).split('\n')) if (l) { try { capB.push(JSON.parse(l)); } catch {} }
		return true;
	};
	daemon.handle({ id: 'bad1', op: 'start', team1: TEAM_P1, team2: TEAM_P2, ai: 'no_such_module' });
	await new Promise((r) => setTimeout(r, 30));
	process.stdout.write = realB;
	chk(capB.some((o) => o.type === 'error' && String(o.error).includes('no_such_module'))
		&& !capB.some((o) => o.type === 'started') && !daemon.battles.has('bad1'),
	'unknown ai id ⇒ error event, no started, no battle record');

	// (d) AI item use: lead CONFUSED in-process (daemon runs the battle here;
	// gen4 Persim Berry cures CONFUSION, not burn — decomp data wins).
	// aiItems ['persimberry'] ⇒ engine emits |-item| and clears the volatile,
	// then the turn continues into move choice. ai: [] = zero modules = random
	{
		let seeded = false;
		const run = await daemonRun('it', ['it1'], () => ({ ai: [], aiItems: ['persimberry'] }), {
			answerSide: 'p1',
			onRequest: (obj) => {
				if (seeded || obj.side !== 'p1') return;
				const rec = daemon.battles.get('it1');
				const p = rec && rec.base.battle && rec.base.battle.sides[1].active[0];
				if (p && !p.fainted && !p.volatiles.confusion) { p.addVolatile('confusion'); seeded = true; }
			},
		});
		const uses = run.rawEvents.filter((o) => o.type === 'line' && o.side === 'p2'
			&& /\|-item\|p2a/.test(o.line)).length;
		chk(seeded && uses === 1 && run.won.it1 !== undefined,
			`AI mode: confusion cured once by Persim Berry (-item x${uses}), battle completed (${run.won.it1})`);
	}

	// (c) AI mode: daemon answers p2 itself; a harness request for p2 is rejected
	if (fs.existsSync(path.join(__dirname, 'ai', 'basic.js'))) {
		let probed = false;
		let aiEvents = 0;
		const run = await daemonRun('ai', ['ai1'], () => ({ ai: 'basic' }), {
			answerSide: 'p1',
			onRequest: (obj) => {
				if (obj.side === 'p2') aiEvents++;
				if (!probed && obj.side === 'p1') {
					probed = true;
					daemon.handle({ id: 'ai1', op: 'request', side: 'p2', choice: 'move 1' });
				}
			},
		});
		const aiEv = run.rawEvents.filter((o) => o.type === 'ai').length;
		const rejected = run.rawEvents.some((o) => o.type === 'error' && o.error && o.error.includes('AI-controlled'));
		chk(aiEv > 0, `AI mode: daemon auto-answered p2 (${aiEv} ai events, ${aiEvents} p2 requests seen)`);
		chk(rejected, 'AI mode: harness request for p2 rejected with AI-controlled error');
		chk(run.won.ai1 !== undefined, `AI mode: battle completed (${run.won.ai1})`);
	} else {
		console.log('skip  ai/basic.js not present yet — AI-mode daemon check deferred');
	}

	console.log(ok ? 'PASS' : 'FAIL');
	process.exit(ok ? 0 : 1);
})().catch((e) => { console.error('FATAL:', e); process.exit(1); });
