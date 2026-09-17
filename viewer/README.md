# Map graph viewer

Interactive view of the map-connection graph, with the ASCII render of every map.

```
python3 viewer/export_viewer_data.py all     # build the data (a few minutes)
cd viewer && npm install && npm run dev      # http://localhost:5173
```

`npm run build` produces a static `dist/` that opens straight off the filesystem.

## What it shows

The canvas is the **overworld**: only outdoor maps are top level, positioned
geographically rather than by a force layout. Gen 4 maps carry their own world
coordinates (they were sliced out of the shared world matrix, so their origin is
known); Emerald has no world matrix, so its 68 outdoor maps are laid out by
walking its connection table — direction plus offset, the same arithmetic the
game uses when it streams a connected map in.

**Click a map** to select it. Three things happen.

The map opens, if it contains anything: interiors and caves appear *inside* the
node, placed at the warp tile that leads to them. A Pokecenter in the middle of
town appears in the middle; a gym in the top right appears top right. Floors of
one building stack vertically, 2F above 1F above B1F.

Every connection touching it lights up gold and is drawn **above** the node
layer, so an opened map no longer buries the connections you opened it to see.
Everything unrelated fades rather than disappearing, keeping the region as
context. Click empty canvas to clear.

The **dock** opens on the right, in two halves. The top is that map on its own
with only its internals and the edges among them — the town without the rest of
the region in the way. The bottom is the ASCII render. Drag the dock's left edge
to resize it, and the bar between the halves to give either one more room.

Click again to collapse. The search box jumps to any map by name or constant and
opens its parent on the way.

Edge styling: thick green is a **border crossing** (walking off the map edge),
thin grey is a **warp**, and dashed with an arrowhead is **one-way**.

## Layout

```
export_viewer_data.py   the only file touching both sides — reads the decomps
                        via map_graph.py, writes public/data/
src/layout.js           bundle -> React Flow nodes and edges; all placement
src/App.jsx             canvas, selection, search, ASCII panel
public/data/            generated, gitignored
```

The app reads JSON and text files and nothing else. It has no knowledge of the
decomps and imports no Python. Deleting this directory leaves the extraction code
untouched.

## Caveats

- **Emerald ASCII is per layout, not per map.** The renderer is layout-driven for
  Gen 3, so the 24 maps sharing `PokemonCenter_1F_Layout` all show that one
  render, and its event sections list every map on the layout. The terrain is
  genuinely identical; the events are a merge.
- **Maps with no edges are not broken.** The Safari Zone, Battle Frontier rooms
  and the Distortion World are entered by script warps, which record no static
  destination. They appear as top-level nodes with no connections, and are
  nested by name where the name says where they belong.
- `MAP_UNDERGROUND` (HeartGold) holds no terrain blocks, so it has no render.
