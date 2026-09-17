#!/usr/bin/env python3
"""
Build the viewer's data bundle: the map graph, plus the ASCII render of every map.

  python3 viewer/export_viewer_data.py <game> [--out DIR]
  python3 viewer/export_viewer_data.py all

Writes, per game:

  viewer/public/data/<game>.json          nodes + edges + nesting + placement
  viewer/public/data/ascii/<game>/*.txt   one render per node

This is the bridge between the extraction code and the web app, and it is the
*only* file that touches both. The app under viewer/src reads the JSON and the
text files and nothing else — no Python, no decomp, no knowledge of how any of
it was derived. Deleting the whole viewer directory leaves the extraction code
untouched, which is the point.

Three things the graph itself does not carry are worked out here, because they
are presentation concerns rather than facts about the games:

`kind`     outdoor / cave / indoor / water, normalised from each game's own
           `map_type` spelling (Emerald says INDOOR, HeartGold INTERIOR,
           Platinum INDOORS; Platinum has no ROUTE type at all, its routes are
           OUTDOORS).

`parent`   what a map sits *inside*. Only outdoor maps are top level; every
           interior and cave hangs off the outdoor map you enter it from, so
           clicking a city opens its Pokecenter, Mart and houses. Derived from
           warp edges, then disambiguated by name — these games name interiors
           after their parent (`MAP_NEW_BARK_ELMS_LAB_1F`), which is a far more
           reliable signal than warp topology when a cave has four entrances.

`pos`      *where* inside the parent, as a 0..1 fraction of the parent's grid.
           Taken from the coordinates of the warp tile that leads there, so a
           Pokecenter in the middle of town lands in the middle of the node,
           and the gym in the top right lands top right. This is why the export
           slices every map: the warp coordinates are matrix-global in Gen 4 and
           only the slice knows the map's origin.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import emerald_data
import heartgold_data
import platinum_data
import map_graph
from terrain_to_ascii import render_terrain
from emerald_data import interior_dims as em_interior_dims, load_wild_encounters

BASE_DIR = Path(__file__).resolve().parent
OUT_DIR  = BASE_DIR / "public" / "data"

GAMES = ("emerald", "heartgold", "platinum")

#: Each game spells its map types differently; the viewer only cares about four
#: behaviours — what is top level, what stacks by floor, what nests inside.
_KIND_BY_TYPE = {
    "MAP_TYPE_TOWN":        "outdoor",  "MAP_TYPE_CITY":       "outdoor",
    "MAP_TYPE_CITY_TOWN":   "outdoor",  "MAP_TYPE_TOWN_CITY":  "outdoor",
    "MAP_TYPE_ROUTE":       "outdoor",  "MAP_TYPE_OCEAN_ROUTE": "outdoor",
    "MAP_TYPE_OUTDOORS":    "outdoor",
    "MAP_TYPE_UNDERGROUND": "cave",     "MAP_TYPE_CAVE":       "cave",
    "MAP_TYPE_INDOOR":      "indoor",   "MAP_TYPE_INDOORS":    "indoor",
    "MAP_TYPE_INTERIOR":    "indoor",   "MAP_TYPE_SECRET_BASE": "indoor",
    "MAP_TYPE_UNDERWATER":  "water",
}

#: Floor suffixes, in the order they must be tried — `B1F` before `1F`, or
#: every basement reads as a first floor. The number is the stacking order:
#: positive up, negative down, so the viewer can pile 2F above 1F above B1F.
_FLOOR_PATTERNS = [
    (re.compile(r"_B(\d+)F$"),  lambda m: -int(m.group(1))),
    (re.compile(r"_(\d+)F$"),   lambda m:  int(m.group(1))),
    (re.compile(r"_ROOF$"),     lambda m:  99),
    (re.compile(r"_BASEMENT$"), lambda m:  -1),
]


def _pretty_emerald(name: str) -> str:
    """`AbandonedShip_CaptainsOffice` -> `Abandoned Ship Captains Office`.

    Emerald names maps by their decomp directory, which is CamelCase with
    underscores between words the devs thought of as separate. The Gen 4
    registries already hand over display names, so only Emerald needs this.
    """
    spaced = re.sub(r"[_/]+", " ", name)
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", spaced)
    spaced = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", spaced)
    return re.sub(r"\s+", " ", spaced).strip()


def _kind(map_type: str) -> str:
    return _KIND_BY_TYPE.get(map_type or "", "indoor")


def _floor(map_id: str) -> tuple[int | None, str]:
    """(stacking order, group id) — the group is the id minus its floor suffix.

    `MAP_NEW_BARK_ELMS_LAB_1F` and `..._2F` share a group, so the viewer knows
    they are one building seen at two heights rather than two buildings.
    """
    for pattern, order in _FLOOR_PATTERNS:
        m = pattern.search(map_id)
        if m:
            return order(m), map_id[:m.start()]
    return None, map_id


# ---------------------------------------------------------------------------
# Per-game slicing: the ASCII render, the grid size, and the local warp coords
# ---------------------------------------------------------------------------

def _entries_for(game: str, node: dict):
    """(tile_entry, warps) for one node, or (None, []).

    `warps` are (local_col, local_row, destination) with coordinates already
    relative to this map's own top-left, which is the whole reason each map has
    to be sliced rather than read straight out of its event file.
    """
    if game == "emerald":
        entry = emerald_data.layout_tile_entry(node["layout_id"])
        if entry is None:
            return None, []
        # Emerald warps are already map-local — the layout is the map's own grid.
        warps = []
        for m in emerald_data.map_entries(node["layout_id"]):
            if m.get("id") != node["id"]:
                continue
            for w in m.get("warp_events") or []:
                if w.get("dest_map"):
                    warps.append((w.get("x", 0), w.get("y", 0), w["dest_map"]))
        return entry, warps

    if game == "heartgold":
        entry = heartgold_data.get_zone_tile_entry([], node["id"], None)
        if entry is None:
            return None, []
        col0 = entry.get("_tile_col_min", 0)
        row0 = entry.get("_tile_row_min", 0)
        warps = [(w.get("x", 0) - col0, w.get("z", 0) - row0, w.get("header"))
                 for w in heartgold_data.zone_warps(node.get("code") or "")
                 if w.get("header")]
        return entry, warps

    entry = platinum_data.get_zone_tile_entry([], node["id"], None)
    if entry is None:
        return None, []
    col0 = entry.get("_tile_col_min", 0)
    row0 = entry.get("_tile_row_min", 0)
    ev   = platinum_data.map_entry(node["id"]) or {}
    warps = [(w.get("x", 0) - col0, w.get("z", 0) - row0, w.get("dest_header_id"))
             for w in ev.get("warp_events") or []
             if w.get("dest_header_id")]
    return entry, warps


def _world_origin(game: str, node: dict, entry: dict):
    """Where this map sits in the world, in tiles, or None if it has no world place.

    Gen 4 hands this over directly: a map sharing the world matrix already knows
    its own origin, because slicing it out of that matrix is how its grid was
    obtained. Maps owning their matrix (caves, interiors) start at 0,0 on a
    matrix of their own, which is not a world position — those return None and
    get placed by nesting instead.
    """
    if game == "heartgold":
        reg = heartgold_data._map_registry().get(node["id"]) or {}
    elif game == "platinum":
        reg = platinum_data._map_registry().get(node["id"]) or {}
    else:
        return None                     # Emerald has no world matrix — see below
    if reg.get("own_matrix", True):
        return None
    return {"x": entry.get("_tile_col_min", 0), "y": entry.get("_tile_row_min", 0)}


def _emerald_world_positions(nodes: dict) -> None:
    """Lay Emerald out from its connection table, which is its own map algebra.

    Emerald has no world matrix; instead each map lists its neighbours as a
    direction plus an offset along the shared edge. Walking that from any map
    reproduces exactly the arrangement the game itself uses when it streams a
    connected map in — `up` means the neighbour's bottom edge meets our top, so
    it sits at our y minus its own height, shifted along x by the offset.

    Not a heuristic and not a guess: it is the same arithmetic overworld.c does.
    Disconnected clusters (the event islands) are each laid out from their own
    seed and then spread apart so they do not overlap.
    """
    conn: dict = {}
    for nid, n in nodes.items():
        if n["kind"] != "outdoor":
            continue
        for m in emerald_data.map_entries(n["layout_id"]):
            if m.get("id") != nid:
                continue
            conn[nid] = [(c.get("direction"), c.get("map"), c.get("offset", 0))
                         for c in m.get("connections") or []
                         if c.get("map") in nodes]

    placed: dict = {}
    seeds = sorted(conn, key=lambda k: -len(conn[k]))
    cluster_x = 0
    for seed in seeds:
        if seed in placed:
            continue
        placed[seed] = (cluster_x, 0)
        queue = [seed]
        members = [seed]
        while queue:
            cur = queue.pop()
            cx, cy = placed[cur]
            cw, ch = nodes[cur]["w"], nodes[cur]["h"]
            for direction, dest, off in conn.get(cur, []):
                if dest in placed:
                    continue
                dw, dh = nodes[dest]["w"], nodes[dest]["h"]
                if   direction == "up":    pos = (cx + off, cy - dh)
                elif direction == "down":  pos = (cx + off, cy + ch)
                elif direction == "left":  pos = (cx - dw, cy + off)
                elif direction == "right": pos = (cx + cw, cy + off)
                else:                      continue   # dive / emerge are vertical
                placed[dest] = pos
                members.append(dest)
                queue.append(dest)
        if len(members) > 1:
            cluster_x = max(placed[m][0] + nodes[m]["w"] for m in members) + 60
        else:
            cluster_x += 60

    for nid, (x, y) in placed.items():
        nodes[nid]["world"] = {"x": x, "y": y}


def _dims(game: str, entry: dict) -> tuple[int, int]:
    if game == "emerald":
        return entry.get("width", 0), entry.get("height", 0)
    return entry.get("grid_width", 0), entry.get("grid_height", 0)


# ---------------------------------------------------------------------------
# Nesting
# ---------------------------------------------------------------------------

def _assign_parents(nodes: dict, edges: list) -> None:
    """Give every interior and cave the outdoor map you enter it from.

    Warp topology alone is ambiguous — Mt. Coronet touches five routes, and a
    Pokecenter 2F touches only its own 1F. Names disambiguate both: these games
    prefix an interior with its parent (`MAP_NEW_BARK_ELMS_LAB_1F`), so the
    longest outdoor neighbour whose id is a prefix wins. Floors that touch no
    outdoor map at all inherit through their building group afterwards.
    """
    neighbours: dict = {nid: set() for nid in nodes}
    for e in edges:
        if e["a"] in neighbours and e["b"] in neighbours:
            neighbours[e["a"]].add(e["b"])
            neighbours[e["b"]].add(e["a"])

    for nid, n in nodes.items():
        if n["kind"] == "outdoor":
            continue
        outdoor = [c for c in neighbours[nid] if nodes[c]["kind"] == "outdoor"]
        if not outdoor:
            continue
        named = [c for c in outdoor if nid.startswith(c + "_")]
        pick  = max(named or outdoor, key=lambda c: (len(c) if named else 0, -len(c), c))
        n["parent"] = pick

    # Upper floors and back rooms: inherit from the building group, then from a
    # neighbour that already has a parent. Repeated because a 3F may only touch
    # a 2F that only just resolved.
    by_group: dict = {}
    for nid, n in nodes.items():
        by_group.setdefault(n["group"], []).append(nid)

    for _ in range(6):
        changed = False
        for nid, n in nodes.items():
            if n["kind"] == "outdoor" or n.get("parent"):
                continue
            found = next((nodes[s]["parent"] for s in by_group[n["group"]]
                          if nodes[s].get("parent")), None)
            if found is None:
                found = next((nodes[c]["parent"] for c in sorted(neighbours[nid])
                              if nodes[c].get("parent")
                              and nodes[c]["kind"] != "outdoor"), None)
            if found and found != nid:
                n["parent"] = found
                changed = True
        if not changed:
            break

    # Last resort: the script-warped rooms. Battle Frontier battle rooms, the
    # Safari Zone areas and Abandoned Ship's hidden floors have no warp edges at
    # all (nothing static points at them), so nothing above can reach them — but
    # their names still say where they belong. Attach to whichever placed map
    # shares the longest run of leading name tokens, requiring at least two so
    # that sharing only `MAP_` counts for nothing.
    def tokens(nid: str) -> list:
        return re.sub(r"^MAP_(HEADER_)?", "", nid).split("_")

    tok    = {nid: tokens(nid) for nid in nodes}
    anchors = [nid for nid, n in nodes.items()
               if n["kind"] == "outdoor" or n.get("parent")]
    for nid, n in nodes.items():
        if n["kind"] == "outdoor" or n.get("parent"):
            continue
        best, best_len = None, 1
        for cand in anchors:
            if cand == nid:
                continue
            shared = 0
            for a, b in zip(tok[nid], tok[cand]):
                if a != b:
                    break
                shared += 1
            if shared > best_len:
                best, best_len = cand, shared
        if best:
            n["parent"] = (best if nodes[best]["kind"] == "outdoor"
                           else nodes[best]["parent"])


def _assign_positions(nodes: dict) -> None:
    """Place each child where its door is, as a 0..1 fraction of the parent grid.

    A child reached by several warps (a cave with four mouths) is placed at the
    mean of them. A child with no warp from its parent — an upper floor — is
    left unplaced; the viewer stacks those on its group instead.
    """
    for nid, n in nodes.items():
        parent = n.get("parent")
        if not parent:
            continue
        pw, ph = nodes[parent]["w"], nodes[parent]["h"]
        if not pw or not ph:
            continue
        hits = [(x, y) for (x, y, dest) in nodes[parent]["_warps"] if dest == nid]
        if not hits:
            continue
        n["pos"] = {"x": round(sum(x for x, _ in hits) / len(hits) / pw, 4),
                    "y": round(sum(y for _, y in hits) / len(hits) / ph, 4)}


# ---------------------------------------------------------------------------

def export(game: str, out_dir: Path = OUT_DIR) -> dict:
    graph     = map_graph.build_graph(game)
    ascii_dir = out_dir / "ascii" / game
    ascii_dir.mkdir(parents=True, exist_ok=True)

    interior_dims = em_interior_dims() if game == "emerald" else {}
    wild_enc      = load_wild_encounters() if game == "emerald" else {}

    nodes: dict = {}
    for raw in graph["nodes"]:
        nid   = raw["id"]
        order, group = _floor(nid)
        name = raw.get("name") or nid
        if game == "emerald":
            name = _pretty_emerald(name)
        nodes[nid] = {**raw, "name": name,
                      "kind": _kind(raw.get("map_type", "")),
                      "floor": order, "group": group,
                      "parent": None, "pos": None, "world": None,
                      "w": 0, "h": 0, "ascii": None, "_warps": []}

    total   = len(nodes)
    rendered = 0
    for i, (nid, n) in enumerate(sorted(nodes.items()), 1):
        entry, warps = _entries_for(game, n)
        if entry is None:
            continue
        n["w"], n["h"] = _dims(game, entry)
        n["_warps"]    = warps
        n["world"]     = _world_origin(game, n, entry)
        text = render_terrain(entry, game, None, interior_dims=interior_dims,
                              wild_encounters=wild_enc)
        (ascii_dir / f"{nid}.txt").write_text(text, encoding="utf-8")
        n["ascii"] = f"data/ascii/{game}/{nid}.txt"
        rendered += 1
        if i % 50 == 0:
            print(f"  {game}: {i}/{total} rendered", flush=True)

    if game == "emerald":
        _emerald_world_positions(nodes)
    _assign_parents(nodes, graph["edges"])
    _assign_positions(nodes)

    for n in nodes.values():
        n.pop("_warps", None)

    bundle = {"game":  game,
              "nodes": [nodes[k] for k in sorted(nodes)],
              "edges": graph["edges"],
              "stats": {**graph["stats"], "rendered": rendered}}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{game}.json").write_text(json.dumps(bundle), encoding="utf-8")

    top = sum(1 for n in nodes.values() if not n["parent"])
    placed = sum(1 for n in nodes.values() if n["pos"])
    world = sum(1 for n in nodes.values() if n["world"])
    missing = [k for k, n in nodes.items() if n["ascii"] is None]
    print(f"{game}: {total} maps, {rendered} rendered, {top} top level, "
          f"{world} world-positioned, {placed} placed in parent "
          f"→ {out_dir / (game + '.json')}")
    if missing:
        print(f"  no terrain, omitted from ASCII: {', '.join(missing)}")
    return bundle


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__.strip())
        return 1
    games = GAMES if args[0] == "all" else (args[0].lower(),)
    for g in games:
        if g not in GAMES:
            print(f"unknown game: {g}")
            return 1
        export(g)
    return 0


if __name__ == "__main__":
    sys.exit(main())
