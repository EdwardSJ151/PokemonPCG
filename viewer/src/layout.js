// Turning the exported bundle into React Flow nodes and edges.
//
// Everything here is placement. The data already says what connects to what and
// what sits inside what; this decides where it all goes on screen, and the one
// rule it follows throughout is that position must mean something. Nothing is
// laid out by a force simulation or dropped in a grid if real coordinates exist.

// Screen pixels per map tile. Maps run from 20 tiles (a house) to a few hundred
// (Route 119), so this is the knob that decides how much of a region fits.
export const TILE = 6;

// A node never gets smaller than this, or the label stops fitting.
const MIN_W = 120;
const MIN_H = 52;

export const KIND_COLOR = {
  outdoor: "#3f7d4f",
  cave:    "#7a6244",
  indoor:  "#3c5c86",
  water:   "#2f6f7d",
};

export function indexBundle(bundle) {
  const byId = new Map();
  for (const n of bundle.nodes) byId.set(n.id, n);

  const children = new Map();
  for (const n of bundle.nodes) {
    if (!n.parent) continue;
    if (!children.has(n.parent)) children.set(n.parent, []);
    children.get(n.parent).push(n);
  }
  // Inside a parent, group floors of one building together and order them
  // bottom-up, so B1F/1F/2F read as a stack rather than three unrelated rooms.
  for (const list of children.values()) {
    list.sort((a, b) =>
      a.group === b.group ? (a.floor ?? 0) - (b.floor ?? 0)
                          : a.group.localeCompare(b.group));
  }
  return { byId, children };
}

function sizeOf(n) {
  return {
    width:  Math.max(MIN_W, Math.round((n.w || 32) * TILE)),
    height: Math.max(MIN_H, Math.round((n.h || 32) * TILE)),
  };
}

// Top-level maps that have real world coordinates are placed at them. The rest —
// event islands, Gen 4 maps owning their own matrix — have no world position to
// honour, so they go in a tidy column off to the side, clearly separated rather
// than scattered through the region and implying adjacency that does not exist.
function placeTopLevel(tops) {
  const withWorld = tops.filter((n) => n.world);
  const without   = tops.filter((n) => !n.world);
  const pos = new Map();

  if (withWorld.length) {
    const minX = Math.min(...withWorld.map((n) => n.world.x));
    const minY = Math.min(...withWorld.map((n) => n.world.y));
    for (const n of withWorld) {
      pos.set(n.id, {
        x: Math.round((n.world.x - minX) * TILE),
        y: Math.round((n.world.y - minY) * TILE),
      });
    }
  }

  const right = withWorld.length
    ? Math.max(...withWorld.map((n) => pos.get(n.id).x + sizeOf(n).width)) + 220
    : 0;
  let y = 0;
  for (const n of without) {
    pos.set(n.id, { x: right, y });
    y += sizeOf(n).height + 24;
  }
  return pos;
}

// Children are placed at the door that leads to them: `pos` is the warp tile as
// a fraction of the parent's grid, so a Pokecenter in the middle of town lands
// in the middle of the expanded node. Floors above and below share one door, so
// they stack off their group's placed member.
function placeChildren(parent, kids, box) {
  const PAD = 12;
  const CW = 132, CH = 40;
  const innerW = box.width  - CW - PAD * 2;
  const innerH = box.height - CH - PAD * 2 - 26;   // 26 = parent's title bar

  const anchors = new Map();   // group -> {x, y} of the floor that has a door
  for (const k of kids) {
    if (k.pos && !anchors.has(k.group)) {
      anchors.set(k.group, {
        x: PAD + Math.max(0, Math.min(1, k.pos.x)) * innerW,
        y: 26 + PAD + Math.max(0, Math.min(1, k.pos.y)) * innerH,
      });
    }
  }

  // Anything whose group never got a door — a back room reached only from
  // inside — is dealt out along the bottom so it is still visible and clickable.
  let spare = 0;
  const out = new Map();
  const perGroup = new Map();
  for (const k of kids) {
    let base = anchors.get(k.group);
    if (!base) {
      base = { x: PAD + (spare % 4) * (CW + 10),
               y: box.height - CH - PAD - Math.floor(spare / 4) * (CH + 8) };
      anchors.set(k.group, base);
      spare += 1;
    }
    // Stack floors: higher floor number sits higher on screen.
    const seen = perGroup.get(k.group) ?? 0;
    perGroup.set(k.group, seen + 1);
    out.set(k.id, { x: Math.round(base.x), y: Math.round(base.y - seen * (CH + 6)) });
  }
  return { positions: out, childSize: { width: CW, height: CH } };
}

// The town-and-internals view: one map opened on its own, with only what sits
// inside it and only the edges among that set. Same placement rules as the main
// canvas, so a building is in the same relative spot in both.
export function buildFocus(bundle, id) {
  const { byId, children } = indexBundle(bundle);
  const town = byId.get(id);
  if (!town) return { nodes: [], edges: [] };
  const kids = children.get(id) ?? [];

  const natural = sizeOf(town);
  const box = {
    width:  Math.max(natural.width, 560),
    height: Math.max(natural.height, 200 + Math.ceil(kids.length / 4) * 54),
  };

  const nodes = [{
    id: town.id, type: "map", position: { x: 0, y: 0 },
    style: { width: box.width, height: box.height, zIndex: 0 },
    data: { node: town, childCount: kids.length, expanded: true,
            selected: false, container: true },
  }];

  const { positions, childSize } = placeChildren(town, kids, box);
  for (const k of kids) {
    nodes.push({
      id: k.id, type: "map", parentNode: town.id, extent: "parent",
      position: positions.get(k.id),
      style: { width: childSize.width, height: childSize.height, zIndex: 5 },
      data: { node: k, childCount: 0, expanded: false,
              selected: false, container: false, child: true },
    });
  }

  const inside = new Set(nodes.map((n) => n.id));
  const edges = bundle.edges
    .filter((e) => inside.has(e.a) && inside.has(e.b))
    .map((e) => ({
      id: `f-${e.a}--${e.b}`, source: e.a, target: e.b,
      style: { stroke: "#e0b341", strokeWidth: 2,
               strokeDasharray: e.bidirectional ? undefined : "5 4" },
      zIndex: 20,
      data: e,
    }));

  return { nodes, edges, focusId: id };
}

export function buildFlow(bundle, expanded, selected) {
  const { byId, children } = indexBundle(bundle);
  const tops = bundle.nodes.filter((n) => !n.parent);
  const topPos = placeTopLevel(tops);

  const nodes = [];
  const visible = new Set();

  for (const n of tops) {
    const kids = children.get(n.id) ?? [];
    const isOpen = expanded.has(n.id) && kids.length > 0;
    let box = sizeOf(n);
    if (isOpen) {
      // Give an opened map room for its contents without letting it swallow the
      // region: enough for a 4-wide grid of children plus the doors' spread.
      box = { width:  Math.max(box.width, 470),
              height: Math.max(box.height, 150 + Math.ceil(kids.length / 4) * 52) };
    }

    nodes.push({
      id: n.id,
      type: "map",
      position: topPos.get(n.id) ?? { x: 0, y: 0 },
      style: { width: box.width, height: box.height, zIndex: isOpen ? 0 : 1 },
      data: { node: n, childCount: kids.length, expanded: isOpen,
              selected: selected === n.id, container: isOpen },
      _hl: n.id,
    });
    visible.add(n.id);

    if (!isOpen) continue;
    const { positions, childSize } = placeChildren(n, kids, box);
    for (const k of kids) {
      nodes.push({
        id: k.id,
        type: "map",
        parentNode: n.id,
        extent: "parent",
        position: positions.get(k.id),
        style: { width: childSize.width, height: childSize.height, zIndex: 5 },
        data: { node: k, childCount: 0, expanded: false,
                selected: selected === k.id, container: false, child: true },
      });
      visible.add(k.id);
    }
  }

  // An edge is drawn only when both ends are on screen. Edges into a collapsed
  // map are not lost — they are simply part of what opening it reveals.
  //
  // Selecting a map lights every edge that touches it and pushes those edges
  // above the node layer, so an opened map no longer buries the very connections
  // you opened it to look at. Everything unrelated fades rather than disappears,
  // which keeps the region readable as context.
  const neighbours = new Set();
  if (selected) {
    for (const e of bundle.edges) {
      if (e.a === selected) neighbours.add(e.b);
      if (e.b === selected) neighbours.add(e.a);
    }
  }

  const edges = bundle.edges
    .filter((e) => visible.has(e.a) && visible.has(e.b))
    .map((e) => {
      const isConn = e.kinds.includes("connection");
      const lit = selected && (e.a === selected || e.b === selected);
      const dim = selected && !lit;
      return {
        id: `${e.a}--${e.b}`,
        source: e.a,
        target: e.b,
        animated: false,
        zIndex: lit ? 2000 : 0,
        className: lit ? "edge-lit" : dim ? "edge-dim" : "",
        style: {
          stroke: lit ? "#e0b341" : isConn ? "#6f9f7a" : "#7d8596",
          strokeWidth: lit ? 3.4 : isConn ? 2.4 : 1.3,
          strokeDasharray: e.bidirectional ? undefined : "5 4",
        },
        markerEnd: e.bidirectional ? undefined
                                   : { type: "arrowclosed",
                                       color: lit ? "#e0b341" : "#7d8596" },
        data: e,
      };
    });

  // Fade maps that have nothing to do with the selection. The selected map, its
  // neighbours and its own contents all stay lit.
  if (selected) {
    for (const n of nodes) {
      const id = n.id;
      const related = id === selected || neighbours.has(id) ||
                      n.parentNode === selected || byId.get(id)?.parent === selected;
      if (!related) n.data = { ...n.data, dim: true };
    }
  }

  return { nodes, edges };
}
