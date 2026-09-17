#!/usr/bin/env node
/**
 * showdown_daemon.js — long-lived Gen 4 battle backend for Java (AGENT_PLAN.md, Task 1).
 *
 * Line-delimited JSON over stdio. Java is the decision-maker only: it sends
 * moves/switches, the daemon runs the local pokemon-showdown sim
 * (Dex.forGen(4)) and streams the raw protocol back. Many battles run
 * concurrently in one process; each id owns a fresh BattleStream (the base
 * stream's atEOF is sticky after a battle ends, so one battle per stream).
 *
 * Every battle has exactly two players: p1 (the human/Java side) and
 * p2 (the CPU side). Both teams are supplied in the start request; the
 * daemon registers p1 then p2 in that order (the engine crashes if a log
 * line is flushed while sides[0] is null).
 *
 * Requests (stdin, one JSON object per line):
 *   {"id":"b1","op":"start","formatid":"gen4customgame","seed":[7,3,11,5],
 *    "name1":"You","team1":[{...}], "name2":"Trainer","team2":[{...}]}
 *   {"id":"b1","op":"request","side":"p1","choice":"move 1"}
 *   {"id":"b1","op":"cancel"}               // force a tie
 *   {"id":"b1","op":"cancel","side":"p2"}   // force p2 to win
 *
 * Choices are 1-based, as in Gen 4: "move 1..4" (or a move id/name),
 * "switch 1..N", "team 1..N". An invalid choice yields an |error| line to
 * that player and the battle stalls until a valid one arrives.
 *
 * Events (stdout, one JSON object per line):
 *   {"type":"started","id":"b1"}
 *   {"type":"line","id":"b1","side":"omni","line":"|turn|1"}
 *   {"type":"line","id":"b1","side":"p1","line":"|request|{...}"}
 *   {"type":"request","id":"b1","side":"p1"}   // ack for a request op
 *   {"type":"win","id":"b1","result":"Trainer"} // winner name, or "tie"/"ended"
 *   {"type":"error","id":"b1","error":"..."}
 *
 * All other output (warnings, crashes) goes to stderr.
 *   node qwenwork/task1/showdown_daemon.js
 */
'use strict';

const Sim = require('/home/pressprexx/Code/GamingResearch/PokemonPCG/pokemon-showdown/dist/sim');

const battles = new Map(); // id -> { base, streams, closed }

function emit(obj) {
	process.stdout.write(JSON.stringify(obj) + '\n');
}

function startBattle(id, req) {
	const base = new Sim.BattleStream();
	const streams = Sim.getPlayerStreams(base);
	const rec = { base, streams, closed: false };
	battles.set(id, rec);
	base.write(`>start ${JSON.stringify({
		formatid: req.formatid || 'gen4customgame',
		seed: req.seed || [7, 3, 11, 5],
	})}`);
	// p1 first, always: the engine dereferences sides[0].name when
	// flushing any log line, and sides are created lazily by setPlayer.
	base.write(`>player p1 ${JSON.stringify({ name: req.name1 || 'P1', team: req.team1 || [] })}`);
	base.write(`>player p2 ${JSON.stringify({ name: req.name2 || 'P2', team: req.team2 || [] })}`);
	emit({ type: 'started', id });
	// Omniscient lines carry the shared battle state; the per-player
	// substreams additionally carry each side's |request| JSON, which
	// Java must see to make decisions.
	for (const [label, key] of [['omni', 'omniscient'], ['p1', 'p1'], ['p2', 'p2']]) {
		(async () => {
			try {
				for await (const chunk of streams[key]) {
					for (const line of chunk.split('\n')) {
						if (!line) continue;
						emit({ type: 'line', id, side: label, line });
						if (label !== 'omni') continue;
						const w = line.match(/^\|win\|(.*)$/);
						const t = line.match(/^\|tie(\||$)/);
						if (w || t) {
							rec.closed = true;
							emit({ type: 'win', id, result: w ? (w[1] || 'p1') : 'tie' });
						}
					}
				}
			} catch (err) {
				rec.closed = true;
				emit({ type: 'error', id, error: `stream ${label}: ${err && err.message || err}` });
			}
			if (!rec.closed) {
				rec.closed = true;
				emit({ type: 'win', id, result: 'ended' });
			}
		})();
	}
}

function requireRec(id) {
	const rec = battles.get(id);
	if (!rec) throw new Error(`unknown id: ${id}`);
	if (rec.closed) throw new Error(`battle already ended: ${id}`);
	return rec;
}

function handle(req) {
	try {
		const { id, op } = req;
		switch (op) {
		case 'start':
			if (battles.has(id)) throw new Error(`id in use: ${id}`);
			startBattle(id, req);
			break;
		case 'request': {
			const rec = requireRec(id);
			rec.streams[req.side].write(req.choice);
			emit({ type: 'request', id, side: req.side });
			break;
		}
		case 'cancel': {
			const rec = requireRec(id);
			rec.base.write(req.side ? `>forcewin ${req.side}` : '>forcetie');
			emit({ type: 'cancelled', id, side: req.side || null });
			break;
		}
		default:
			throw new Error(`unknown op: ${op}`);
		}
	} catch (err) {
		emit({ type: 'error', id: req && req.id, error: String(err && err.message || err) });
	}
}

let buf = '';
process.stdin.on('data', d => {
	buf += d.toString();
	let i;
	while ((i = buf.indexOf('\n')) >= 0) {
		const line = buf.slice(0, i).trim();
		buf = buf.slice(i + 1);
		if (!line) continue;
		let req;
		try {
			req = JSON.parse(line);
		} catch (err) {
			emit({ type: 'error', error: `bad request line: ${err.message}` });
			continue;
		}
		handle(req);
	}
});
process.stdin.on('end', () => process.exit(0));
