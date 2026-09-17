"""Derive Emerald BUILDING_LEFT_BORDER_IDS. Run from the repo root.

Emerald has no object layer, so two buildings drawn side by side are one
undifferentiated run of impassable art — which is how a warp in one of them
used to paint the other. Gen 4 gets instance identity free from its meshes;
Gen 3 has to recover it from the way the maps were authored.

Buildings were stamped: the same block of metatiles is copy-pasted into every
map that uses it. So a bond *inside* a building is fixed (0x2A1 is preceded by
0x2A4 in every occurrence, everywhere), while the bond *between* two buildings
varies with whatever happens to sit alongside. The tell is open ground: a
metatile that is ever the first impassable cell of a horizontal run is a
building's left border, and a left border always begins a new instance.

Evidence for that: the Mauville house's neighbour also stands on Route 110 and
Route 111, alone in one and against a different building in the other, so its
own left column shows up preceded by open ground, by 0x138 and by 0x134.
"""
import collections, pickle, sys
sys.path.insert(0, '/home/pressprexx/Code/GamingResearch/PokemonPCG')
from terrain_helpers import load_tiles, BUILDING_METATILE_IDS


def main():
    left = collections.defaultdict(set)
    for e in load_tiles('emerald'):
        ts = e.get('secondary_tileset') or ''
        art = BUILDING_METATILE_IDS.get(ts)
        if not art:
            continue
        for row in e['grid']:
            ids = [x['metatile_id'] if (x and not x.get('passable', True)) else None
                   for x in row]
            for i, m in enumerate(ids):
                # first impassable cell of a run — nothing solid to its left
                if m in art and (i == 0 or ids[i - 1] is None):
                    left[ts].add(m)
    return {ts: sorted(v) for ts, v in left.items() if v}


if __name__ == '__main__':
    o = main()
    S = ('/tmp/claude-1000/-home-pressprexx-Code-GamingResearch-PokemonPCG/'
         'a7b349bd-a3a0-4099-97b3-f5d406344531/scratchpad/')
    pickle.dump(o, open(S + 'border_table.pkl', 'wb'))
    for k, v in sorted(o.items()):
        print(f'{k:36s} {len(v):4d}')
    print('total', sum(len(v) for v in o.values()))
