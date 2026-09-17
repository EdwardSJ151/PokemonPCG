"""Derive Emerald TREE_BACKED_METATILE_IDS — building art drawn over tree canopy.

Run from the repo root.

Game Freak drew a building's edge tiles twice: once over grass, once over the
canopy of whatever tree stands behind it. Petalburg's Mart is the clear case —
0x250/0x251 are its top roof edge on grass and 0x258/0x259 are the same roof
overlay on tree canopy. Both land in BUILDING_METATILE_IDS, so the canopy pair
painted the Mart one row taller than it is and ate the bottom of the tree.

A cell is tree-backed when most of what you actually see in it is canopy. That
needs a foliage test at TILE level, not metatile level: a half-roof/half-canopy
metatile averages out to nothing, which is why the whole-metatile green test in
derive_plant_metatiles.py cannot see these. An 8x8 tile is uniform enough to
classify outright.

A tile counts as foliage only if it passes all three of:

  drawn by a confirmed foliage metatile (TREE_METATILE_IDS / PLANT_METATILE_IDS)
      Without this the Mauville Mart's flat teal wall 0x3AD scores 0.81 — teal
      satisfies g > b + 16.
  never drawn by a walkable metatile
      Grass is green and is ground. Without this the roof corners over grass
      (Mauville/Fortree 0x048, 0x04B) score 0.61 and punch holes in real roofs.
      This is also what settles Fortree: the leaf texture flanking the
      treehouses is walkable ground, not canopy, so the houses stay 5 wide.
  >= 80% green pixels
      Some tree metatiles draw the rock their trunk stands on, so the structural
      tests alone admit brown cliff backdrops (BattleFrontierOutsideEast
      0x365-0x367, Mauville 0x3B2/0x3B6).

Each filter removes a failure the other two cannot, so none is redundant.

The cut then sets itself: over the 1732 metatiles in the building vocabulary the
0.30-0.40 band is EMPTY and 0.40-0.50 holds a single entry, so 0.45 separates
two populations rather than slicing one.
"""
import collections, pickle, re, struct, sys
sys.path.insert(0, '/home/pressprexx/Code/GamingResearch/PokemonPCG')
from pathlib import Path
import numpy as np
from terrain_helpers import (load_tiles, BUILDING_METATILE_IDS,
                             TREE_METATILE_IDS, PLANT_METATILE_IDS)
from tileset_view import TS, BASE

TSD = Path('/home/pressprexx/Code/GamingResearch/PokemonPCG/pokeemerald/data/tilesets')
PMT = (TSD / 'primary/general' / 'metatiles.bin').read_bytes()
FOLIAGE_MIN = 0.45   # empty 0.30-0.40 band; see module docstring
GREEN_MIN   = 0.80


def dirname(ts):
    return re.sub(r'(?<!^)(?=[A-Z])', '_', ts.replace('gTileset_', '')).lower()


def walkable_metatiles():
    """Every metatile ever stood on, per secondary tileset — i.e. ground art."""
    out = collections.defaultdict(set)
    for e in load_tiles('emerald'):
        if e.get('primary_tileset') != 'gTileset_General':
            continue
        ts = e.get('secondary_tileset') or ''
        for row in e['grid']:
            for x in row:
                if x and x.get('passable', True):
                    out[ts].add(x['metatile_id'])
    return out


def analyse(tsname, ground):
    d = TSD / 'secondary' / dirname(tsname)
    if not (d / 'metatiles.bin').exists():
        return None
    ts = TS(BASE / 'primary/general', d)
    smt = (d / 'metatiles.bin').read_bytes()

    def slots(gid):
        src, base = (PMT, gid) if gid < 512 else (smt, gid - 512)
        if (base + 1) * 16 > len(src):
            return []
        return [struct.unpack_from('<H', src, base * 16 + 2 * j) for j in range(8)]

    def raw(gid):
        src, base = (PMT, gid) if gid < 512 else (smt, gid - 512)
        if (base + 1) * 16 > len(src):
            return []
        return [struct.unpack_from('<H', src, base * 16 + 2 * j)[0] for j in range(8)]

    def tiles_of(ms):
        s = set()
        for m in ms:
            for v in raw(m):
                if v & 0x3FF:
                    s.add((v & 0x3FF, (v >> 12) & 0xF))
        return s

    seeds = set(TREE_METATILE_IDS) | set(PLANT_METATILE_IDS.get(tsname, ()))
    cand = tiles_of(seeds) - tiles_of(ground.get(tsname, ()))

    def green(idx, pal):
        a = np.array(ts.tile(idx, pal, 0, 0).convert('RGBA'))
        op = a[..., 3] > 0
        if op.sum() < 32:
            return False
        r, g, b = (a[..., i].astype(int) for i in range(3))
        return ((g > r + 16) & (g > b + 16) & op).sum() / op.sum() >= GREEN_MIN

    FOL = {t for t in cand if green(*t)}

    def fraction(gid):
        sl = raw(gid)
        if not sl:
            return 0.0
        prov = np.full((16, 16), -1, dtype=np.int8)
        for layer in range(2):
            for j in range(4):
                v = sl[layer * 4 + j]
                idx, xf, yf, pal = v & 0x3FF, (v >> 10) & 1, (v >> 11) & 1, (v >> 12) & 0xF
                if layer and idx == 0:
                    continue
                a = np.array(ts.tile(idx, pal, xf, yf).split()[3]) > 0
                y, x = (j // 2) * 8, (j % 2) * 8
                prov[y:y + 8, x:x + 8][a] = 1 if (idx, pal) in FOL else 0
        vis = prov >= 0
        return float((prov == 1).sum()) / max(1, vis.sum())

    return fraction


def main():
    ground = walkable_metatiles()
    scores, out = {}, {}
    for tsname, ids in sorted(BUILDING_METATILE_IDS.items()):
        f = analyse(tsname, ground)
        if not f:
            continue
        scores[tsname] = {m: round(f(m), 3) for m in sorted(ids)}
        keep = [m for m, v in scores[tsname].items() if v >= FOLIAGE_MIN]
        if keep:
            out[tsname] = keep
    return out, scores


if __name__ == '__main__':
    o, sc = main()
    S = ('/tmp/claude-1000/-home-pressprexx-Code-GamingResearch-PokemonPCG/'
         'a7b349bd-a3a0-4099-97b3-f5d406344531/scratchpad/')
    pickle.dump((o, sc), open(S + 'treebacked.pkl', 'wb'))
    allv = [v for d in sc.values() for v in d.values()]
    h = collections.Counter(min(int(v * 10), 9) for v in allv)
    print('histogram over', len(allv), 'vocabulary metatiles')
    for k in range(10):
        print(f'  {k/10:.1f}-{k/10+0.1:.1f}  {h.get(k,0):5d}')
    print()
    for k, v in o.items():
        old = PLANT_METATILE_IDS.get(k, frozenset())
        print(f'{k:36s} {len(v):3d}  ' +
              ' '.join(f'{m:03X}{"*" if m in old else ""}' for m in v))
    print('total', sum(len(v) for v in o.values()), '(* already a plant)')
