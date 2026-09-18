// dex_dump.js — one-shot dump of the gen-4 view of the fork's OWN Dex.
//
// The daemon resolves battles with pokemon-showdown/dist (format
// gen4customgame). Everything battle-relevant that the simulator displays or
// references must agree with that exact data, so we extract FROM IT rather
// than from any ROM or hand-written table. Text comes from the fork's compiled
// text data (dist/data/text/*.js), cleaned of [Gen X]-style tags.
//
// Run:  node qwenwork/dex_dump.js          (writes qwenwork/sim_data/dex_dump.json)
// Requires nothing but the fork repo at ../pokemon-showdown.
"use strict";
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const Sim = require(path.join(ROOT, "pokemon-showdown/dist/sim"));
const { MovesText } = require(path.join(ROOT, "pokemon-showdown/dist/data/text/moves.js"));
const { AbilitiesText } = require(path.join(ROOT, "pokemon-showdown/dist/data/text/abilities.js"));
const { ItemsText } = require(path.join(ROOT, "pokemon-showdown/dist/data/text/items.js"));

const Dex = Sim.Dex;
const gen4 = Dex.forGen ? Dex.forGen(4) : Dex.mod("gen4");

function cleanText(entry) {
  if (!entry) return "";
  let s = entry.shortDesc || entry.desc || "";
  if (Array.isArray(s)) s = s.join(" ");
  // strip [Gen 6+], [Gen 4], [partially implemented] and similar metadata tags
  s = s.replace(/\[[^\]]*\]/g, "");
  // markdown-ish leftovers used in PS text
  s = s.replace(/^##\s*/gm, "").replace(/\n+/g, " ");
  s = s.replace(/\s{2,}/g, " ").trim();
  return s;
}

const out = { meta: {}, species: {}, moves: {}, abilities: {}, items: {} };

// ---- species: base forms, national dex 1..493 (gen-4 dex) ----
for (const s of Dex.species.all()) {
  if (!s.exists || !s.num || s.num < 1 || s.num > 493) continue;
  // forme species (Megas, Rotom-Wash...) out of v1 scope; changesFrom catches
  // hyphenless forme ids like greninjaash (Ash-Greninja). Base species with
  // hyphens in the NAME (Nidoran-F, Ho-Oh, Porygon-Z) must be kept.
  if (s.forme || s.changesFrom) continue;
  const gs = gen4.species.get(s.id); // gen-4 view: restores pre-XY base stats etc.
  out.species[s.id] = {
    num: s.num,
    name: s.name,
    types: (gs.types && gs.types.length ? gs.types : s.types),
    baseStats: {
      hp: gs.baseStats.hp, atk: gs.baseStats.atk, def: gs.baseStats.def,
      spa: gs.baseStats.spa, spd: gs.baseStats.spd, spe: gs.baseStats.spe,
    },
    abilities: [gs.abilities["0"], gs.abilities["1"]].filter(Boolean),
    hiddenAbility: gs.abilities["H"] || null, // reference only: gen-4 ROMs have no HA
  };
}

// ---- moves: every existing move (referenced-set filtering happens later) ----
for (const id of Object.keys(Dex.data.Moves)) {
  const m = gen4.moves.get(id);
  out.moves[id] = {
    name: m.name, type: m.type, category: m.category, pp: m.pp, num: m.num,
    gen: m.gen, text: cleanText(MovesText[id]),
  };
}

// ---- abilities ----
for (const id of Object.keys(Dex.data.Abilities)) {
  const a = gen4.abilities.get(id);
  if (!a.exists || !a.num) continue;
  out.abilities[id] = { name: a.name, num: a.num, text: cleanText(AbilitiesText[id]) };
}

// ---- items: full PS item universe (pool filtering happens in python) ----
for (const id of Object.keys(Dex.data.Items)) {
  const it = gen4.items.get(id);
  if (!it.exists || it.num === 0) continue;
  out.items[id] = {
    name: it.name, num: it.num, gen: it.gen,
    text: cleanText(ItemsText[id]),
  };
}

out.meta = {
  species: Object.keys(out.species).length,
  moves: Object.keys(out.moves).length,
  abilities: Object.keys(out.abilities).length,
  items: Object.keys(out.items).length,
};

const dest = path.join(__dirname, "sim_data", "dex_dump.json");
fs.mkdirSync(path.dirname(dest), { recursive: true });
fs.writeFileSync(dest, JSON.stringify(out));
console.log("wrote", dest, out.meta);

// hard invariants — the dump must be self-consistent before python runs
if (out.meta.species !== 493) { console.error("FAIL: expected 493 base species"); process.exit(1); }
if (!out.moves["return"] || !out.moves["return"].text) { console.error("FAIL: Return text missing"); process.exit(1); }
if (!out.abilities["levitate"]) { console.error("FAIL: levitate missing"); process.exit(1); }
