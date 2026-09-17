"""Derive Emerald BUILDING_METATILE_IDS. Run from the repo root."""
import collections, re, struct, sys
sys.path.insert(0, '/home/pressprexx/Code/GamingResearch/PokemonPCG')
from pathlib import Path
from terrain_helpers import (load_tiles, load_maps_optional, _classify_building,
                             TREE_METATILE_IDS)

TSDIR = Path('/home/pressprexx/Code/GamingResearch/PokemonPCG/pokeemerald/data/tilesets')
# A building's own art is never further than this from its door — so this is a
# claim about how big Emerald's buildings are, and 5 was simply too small.
# Devon Corp is 10x6 with its door on the bottom row, so its whole top row sat
# outside the radius and every metatile in it was branded terrain; Rustboro's
# flats and houses lost their middle columns the same way. 9 covers the largest
# building in the game and still rejects Mauville's fence, which is the case
# this filter exists for (it runs the length of the map, so its ratio stays
# ~0.6 at every radius out to 12 — see NEAR_MIN).
DOOR_RADIUS = 9
NEAR_MIN    = 1.0     # share of a metatile's impassable uses that must be near a door
MIN_USES    = 2       # uses required before usage alone can admit a metatile

def metatiles(path):
    return (path/'metatiles.bin').read_bytes()

def tiles_of(pmt, smt, gid):
    src, base = (pmt, gid) if gid < 512 else (smt, gid-512)
    if src is None or (base+1)*16 > len(src): return set()
    return {struct.unpack_from('<H', src, base*16+2*j)[0] & 0x3FF for j in range(8)}

def dirname(ts): return re.sub(r'(?<!^)(?=[A-Z])','_',ts.replace('gTileset_','')).lower()

def main():
    tiles = load_tiles('emerald'); maps = load_maps_optional('emerald')
    by_layout = collections.defaultdict(list)
    for m in maps: by_layout[m.get('map_name')].append(m)

    seeds  = collections.defaultdict(set)
    ground = collections.defaultdict(set)
    near   = collections.defaultdict(collections.Counter)
    far    = collections.defaultdict(collections.Counter)
    wet    = collections.defaultdict(set)

    for e in tiles:
        if e.get('primary_tileset') != 'gTileset_General': continue
        ts = e.get('secondary_tileset') or ''
        g = e['grid']; rows = len(g); cols = len(g[0]) if rows else 0
        doors = {(int(w.get('y',0)), int(w.get('x',0)))
                 for mn in (e.get('maps') or []) for m in by_layout.get(mn, [])
                 for w in (m.get('warp_events') or [])
                 if _classify_building(w.get('dest_map',''), 'emerald')}
        # A map with no building door says nothing about door proximity — and
        # the cutscene duplicates (Sootopolis LegendsBattle) are whole cities
        # with every building and no warps at all, which would brand their art
        # as terrain. Skip them for the statistics.
        if not doors: continue
        for r, row in enumerate(g):
            for c, x in enumerate(row):
                if not x: continue
                # Ocean is impassable, so a shoreline metatile that happens to
                # share tile graphics with a seafront building passes both the
                # closure and the usage filter — Route 110 painted a square of
                # sea as part of the Cycling Road gate. Behaviour settles it:
                # whatever else a building is made of, it is not water.
                if any(t in (x.get('behavior_name') or '')
                       for t in ('WATER', 'WATERFALL', 'CURRENT', 'GRASS')):
                    wet[ts].add(x['metatile_id'])
                isnear = any(max(abs(r-dr), abs(c-dc)) <= DOOR_RADIUS for dr, dc in doors)
                if x.get('passable', True):
                    # ground = walkable art seen away from every door; near a
                    # door a walkable metatile may be the building's own doorway
                    if not isnear: ground[ts].add(x['metatile_id'])
                else:
                    (near if isnear else far)[ts][x['metatile_id']] += 1
        # seed: the door tile and the impassable run above it, 5 columns wide
        for dr, dc in doors:
            for dx in (-2,-1,0,1,2):
                cx = dc+dx
                if not (0 <= cx < cols): continue
                for r in range(dr, max(-1, dr-5), -1):
                    cell = g[r][cx]
                    if not cell or cell['metatile_id'] in TREE_METATILE_IDS: break
                    if cell.get('passable', True) and r != dr: break
                    seeds[ts].add(cell['metatile_id'])

    out = {}
    for ts in sorted(seeds):
        d = TSDIR/'secondary'/dirname(ts)
        if not (d/'metatiles.bin').exists(): continue
        pmt = metatiles(TSDIR/'primary/general'); smt = metatiles(d)
        tof = lambda g: tiles_of(pmt, smt, g)
        banned = set().union(*[tof(m) for m in ground[ts]]) if ground[ts] else set()
        banned |= set().union(*[tof(m) for m in TREE_METATILE_IDS]) | {0}
        art = set().union(*[tof(m) for m in seeds[ts]]) - banned
        allg = list(range(len(pmt)//16)) + [512+i for i in range(len(smt)//16)]
        cand = [g for g in allg if tof(g) & art]
        # Keep only metatiles whose impassable uses cluster at doors. A building
        # wall is always within a few tiles of the door it belongs to; a fence
        # runs the length of the map, and a cliff is everywhere.
        keep = []
        for g in cand:
            n, f = near[ts].get(g, 0), far[ts].get(g, 0)
            if g in wet[ts]: continue
            if n and n/(n+f) >= NEAR_MIN: keep.append(g)
        # The closure above can only reach a metatile that shares tile graphics
        # with the seeded interior, and a building's left and right edge columns
        # share none of it — they are silhouette and shadow tiles that appear
        # nowhere else in the building. Rustboro's Pokecenter and Mart corner
        # (0x21A) and both edges of the Battle Frontier lounges are invisible to
        # it: `tiles_shared_with_art` is 0 of 6 or 7, not a low score but no
        # overlap whatsoever. So candidacy needs a second, independent source.
        #
        # Usage alone is that source, at a strict bar: every single impassable
        # use within DOOR_RADIUS of a building door, over at least MIN_USES of
        # them. That is what a fence cannot do — Mauville's runs the length of
        # the map, so it keeps far > 0 out to radius 12 and stays rejected here
        # exactly as it is above.
        for g in list(near[ts]):
            if g in keep or g in wet[ts]: continue
            if far[ts].get(g, 0) == 0 and near[ts][g] >= MIN_USES: keep.append(g)
        out[ts] = sorted(keep)
    return out

if __name__ == '__main__':
    import pickle
    o = main()
    S='/tmp/claude-1000/-home-pressprexx-Code-GamingResearch-PokemonPCG/a7b349bd-a3a0-4099-97b3-f5d406344531/scratchpad/'
    pickle.dump(o, open(S+'final_table.pkl','wb'))
    for k, v in o.items(): print(f'{k:36s} {len(v):4d}')
    print('total', sum(len(v) for v in o.values()))
