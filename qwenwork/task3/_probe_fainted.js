
"use strict";
const Sim = require("/home/pressprexx/Code/GamingResearch/PokemonPCG/pokemon-showdown/dist/sim");
const ZERO = { hp:0, atk:0, def:0, spa:0, spd:0, spe:0 };
const pkm = (species, moves) => ({ species, level: 50, ability: "", item: "", nature: "quiet", ivs: ZERO, moves });
const TEAM_A = [
  pkm("groudon", ["earthquake","flamethrower","stealthrock","roar"]),
  pkm("gyarados", ["waterfall","icefang","outrage","bodyslam"]),
];
const TEAM_B = [
  pkm("gengar", ["shadowball","thunderbolt","sludgebomb","recover"]),
  pkm("hypno", ["psychic","thunderbolt","shadowball","calmmind"]),
];
(async () => {
  const stream = new Sim.BattleStream();
  const st = Sim.getPlayerStreams(stream);
  (async () => { for await (const _ of st.omniscient) {} })().catch(() => {});
  for (const k of ["p1", "p2"])
    (async () => {
      for await (const chunk of st[k])
        for (const line of chunk.split("\n")) {
          const m = line.match(/^\|request\|(.*)$/);
          if (m && JSON.parse(m[1]).teamPreview) st[k].write("team 12");
        }
    })().catch(() => {});
  st.omniscient.write(`>start ${JSON.stringify({ formatid: "gen4customgame", seed: [7,3,11,5] })}`);
  st.omniscient.write(`>player p1 ${JSON.stringify({ name: "A", team: TEAM_A })}`);
  st.omniscient.write(`>player p2 ${JSON.stringify({ name: "B", team: TEAM_B })}`);
  let sides = null;
  for (let t = 0; t < 400 && (!sides || !sides[0] || !sides[1]); t++) {
    await new Promise(r => setTimeout(r, 10));
    if (stream.battle && stream.battle.sides[0] && stream.battle.sides[1]) sides = stream.battle.sides;
  }
  const b = stream.battle;
  const gengar = b.sides[1].active[0];
  const groudon = b.sides[0].active[0];
  gengar.fainted = true; gengar.hp = 0;
  try {
    const d1 = b.actions.getDamage(gengar, groudon, "shadowball");
    const d2 = b.actions.getDamage(groudon, gengar, "earthquake");
    console.log("fainted-attacker shadowball:", JSON.stringify(d1));
    console.log("live-attacker earthquake  :", JSON.stringify(d2));
  } catch (e) { console.log("THREW:", e.message); }
  b.end();
  setTimeout(() => process.exit(0), 100);
})();
