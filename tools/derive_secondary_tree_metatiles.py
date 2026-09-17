"""Derive Emerald SECONDARY_TREE_METATILE_IDS — trees drawn by a secondary tileset.

Run from the repo root:  python3 tools/derive_secondary_tree_metatiles.py [tileset...]
With no arguments it does every tileset that has a seed below.

TREE_METATILE_IDS covers only gTileset_General, because it closes over that
tileset's tile graphics. A secondary tileset draws its own trees with its own
tile indices, so those trees are invisible to it and render as bare wall. This
tool is the same derivation, run per secondary tileset.

The mechanism, on Mossdeep:

  An Emerald metatile is two 2x2 layers — background underneath, art on top. A
  tree is a background the map already uses as ground with tree art composited
  over it. So one tree is many metatile ids: sand, grass, rock, dirt and the
  shadow decal each get their own copy of the same canopy and the same trunk.
  That is why marking ids by hand does not converge — Mossdeep needs 12, and
  the two the eye lands on first are just trunk-on-sand.

  What is constant across all of them is the ART, so the art is what we key on.
  Seed from a couple of confirmed trees, take the layer-1 tiles they draw, and
  every metatile drawing any of those tiles is a tree.

Two filters, each removing a failure the other cannot:

  the tile must never appear on layer 0
      Layer 0 is the background. A tile used as background is ground, not tree
      art, and closing over it would drag in everything that stands on the same
      ground. (Mossdeep: no seed tile appears on layer 0, so this is a no-op
      here and a guard for the next tileset.)
  the metatile must be used impassably
      A canopy overhangs the walkable tile below it, so half of a tileset's
      tree metatiles are walkable ground wearing leaves. Same rule the primary
      table already follows. In Mossdeep the split is total: of the 28
      metatiles drawing tree art, 12 are always impassable and 16 always
      passable, none both.

A colour test cannot do this job. Mossdeep's rocks are mossy — 0x2F0 scores
0.71 green and 0x31A 0.88, while the confirmed trunk 0x310 scores 0.27 — so
derive_plant_metatiles' >= 0.80 cut would take the cliffs and drop the trees.

Verified against layout_mossdeep_city_manual.txt: all 56 hand-marked tiles are
predicted, none missed. The 76 further cells it finds are the stand in the
shallow water at rows 28-38, which the markup stopped short of.

Everything here is scoped per tileset. Tile indices are tileset-local exactly
as metatile ids >= 0x200 are, so 0x220 is Mossdeep canopy and something else
everywhere else. Never flatten either the seed or the output.
"""
import struct
import sys
from pathlib import Path

BASE = Path('/home/pressprexx/Code/GamingResearch/PokemonPCG/pokeemerald/data/tilesets')

# Two confirmed trees per tileset, as (metatile id, ...) — the closure finds the
# rest. Pick a canopy and a trunk if they use different art, as Mossdeep does.
SEEDS = {
    # 0x310/0x311 are trunk-on-sand, 0x355/0x356 the alt canopy, 0x322/0x323
    # the alt trunk. Between them they name all four art groups.
    'gTileset_Mossdeep': (0x310, 0x311, 0x322, 0x323, 0x355, 0x356),
    # Three groups again: 0x243 canopy, 0x23A the shadowed base (art 0x268/
    # 0x278, reached from neither other seed), 0x211 the second palm.
    'gTileset_Dewford': (0x243, 0x23A, 0x211),
}


def dirname(ts):
    import re
    return re.sub(r'(?<!^)(?=[A-Z])', '_', ts.replace('gTileset_', '')).lower()


def layers(smt, pmt, gid):
    """(background tiles, art tiles) for one metatile, zeroes dropped."""
    src, base = (pmt, gid) if gid < 512 else (smt, gid - 512)
    v = [struct.unpack_from('<H', src, base * 16 + 2 * k)[0] & 0x3FF
         for k in range(8)]
    return set(v[:4]) - {0}, set(v[4:]) - {0}


def derive(tsname):
    d = BASE / 'secondary' / dirname(tsname)
    if not (d / 'metatiles.bin').exists():
        return None
    smt = (d / 'metatiles.bin').read_bytes()
    pmt = (BASE / 'primary/general' / 'metatiles.bin').read_bytes()
    n = len(smt) // 16
    ids = range(0x200, 0x200 + n)

    art = set()
    for s in SEEDS[tsname]:
        art |= layers(smt, pmt, s)[1]
    # A tile that is ever a background is ground, not tree art.
    background = set()
    for gid in ids:
        background |= layers(smt, pmt, gid)[0]
    art -= background

    drawing = {gid for gid in ids if layers(smt, pmt, gid)[1] & art}
    return sorted(art), sorted(drawing)


def impassable_uses(tsname, drawing):
    """Split the closure by how the maps actually use each metatile."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import emerald_data as ed
    blocked, walkable = {}, {}
    for const in ed.layout_registry():
        te = ed.layout_tile_entry(const)
        if not te or (te.get('secondary_tileset') or '') != tsname:
            continue
        for row in te['grid']:
            for cell in row:
                m = cell.get('metatile_id')
                if m not in drawing:
                    continue
                tgt = walkable if cell.get('passable', True) else blocked
                tgt[m] = tgt.get(m, 0) + 1
    return blocked, walkable


def main(names):
    for tsname in names:
        art, drawing = derive(tsname)
        blocked, walkable = impassable_uses(tsname, set(drawing))
        both = sorted(set(blocked) & set(walkable))
        print(f'{tsname}')
        print(f'  art tiles ({len(art)}): {[hex(t) for t in art]}')
        print(f'  metatiles drawing it: {len(drawing)}')
        print(f'  impassable (-> the table): {[hex(m) for m in sorted(blocked)]}')
        print(f'  passable (canopy overhang, left as ground): '
              f'{[hex(m) for m in sorted(walkable)]}')
        print(f'  used BOTH ways (would need a per-cell rule): '
              f'{[hex(m) for m in both] or "none"}')


if __name__ == '__main__':
    main(sys.argv[1:] or sorted(SEEDS))
