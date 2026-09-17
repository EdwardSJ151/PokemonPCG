"""Derive Emerald PLANT_METATILE_IDS — foliage wrongly inside the building
vocabulary. Run from the repo root.

Hedges and ornamental shrubs are landscaping: they sit against the buildings
they decorate, so the near-door usage filter in derive_building_metatiles.py
cannot reject them (their near-door ratio is a clean 1.0) and the graphics
closure walks straight into them from the wall they abut. In Petalburg the
hedge 0x24C runs between the house and the Gym, so both were painted as one
structure; Fortree and Slateport have the same problem with canopy pieces.

What separates foliage from masonry is colour, and by a wide margin: measured
over the composed 16x16 metatile, plant art is 0.94-1.00 green while walls and
roofs are 0.00-0.12. The cut at 0.80 is nowhere near either cluster.

Do NOT lower the threshold. The 0.45-0.80 band is genuinely mixed — it holds
roof corners drawn over a grass background (Mauville 0x048, Fortree 0x04B) that
are real building art, and dropping those would punch holes in the buildings.
A tree overhanging a roof (Petalburg 0x258/0x259, the Mart's top corner) also
lands in that band and is still misclassified; separating it needs a tightened
tree-graphics set, not a colour threshold.
"""
import collections, pickle, re, sys
sys.path.insert(0, '/home/pressprexx/Code/GamingResearch/PokemonPCG')
from terrain_helpers import BUILDING_METATILE_IDS
from tileset_view import TS, BASE

GREEN_MIN = 0.80


def dirname(ts):
    return re.sub(r'(?<!^)(?=[A-Z])', '_', ts.replace('gTileset_', '')).lower()


def green_fraction(ts, mid):
    im = ts.metatile(mid).convert('RGB')
    px = im.load()
    g = 0
    for y in range(16):
        for x in range(16):
            r, gg, b = px[x, y]
            if gg > r + 16 and gg > b + 16:
                g += 1
    return g / 256


def main():
    out = {}
    for tsname, ids in sorted(BUILDING_METATILE_IDS.items()):
        d = BASE / 'secondary' / dirname(tsname)
        if not (d / 'metatiles.bin').exists():
            continue
        ts = TS(BASE / 'primary/general', d)
        keep = [m for m in sorted(ids) if green_fraction(ts, m) >= GREEN_MIN]
        if keep:
            out[tsname] = keep
    return out


if __name__ == '__main__':
    o = main()
    S = ('/tmp/claude-1000/-home-pressprexx-Code-GamingResearch-PokemonPCG/'
         'a7b349bd-a3a0-4099-97b3-f5d406344531/scratchpad/')
    pickle.dump(o, open(S + 'plant_table.pkl', 'wb'))
    for k, v in o.items():
        print(f'{k:36s} {len(v):3d}  {[hex(m) for m in v]}')
    print('total', sum(len(v) for v in o.values()))
