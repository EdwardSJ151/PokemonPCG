// AI dice: the decomp's 16-bit LCG (include/math_util.h:8-10, src/battle/battle_system.c:1308-1312),
// u32 state, returns the top 16 bits. Deliberately NOT the fork's battle PRNG (Gen5RNG);
// the seed is the 32-bit battle seed (decomp battle_main.c:1034), here taken from prngSeed[1..0].
'use strict';

class AICpuLcg {
	constructor(seed) {
		this.s = (seed >>> 0) || 1;
	}

	// RandNext(): [0..65535]
	randNext() {
		this.s = (Math.imul(this.s, 1103515245) + 24691) >>> 0;
		return this.s >>> 16;
	}

	// The decomp's `RandNext() % N` idiom.
	randN(n) {
		return this.randNext() % n;
	}
}

module.exports = { AICpuLcg };
