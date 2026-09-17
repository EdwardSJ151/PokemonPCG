#!/usr/bin/env python3
"""
Map-connection graph — which maps lead to which, for all three games.

  python3 map_graph.py <game> [output.json] [--include-unused] [--stats]

  game: emerald | heartgold | platinum

Nodes are maps; edges are map pairs. Deliberately coarse: an edge says only
"you can get from A to B", not by which of the 38 cracked-floor tiles on Sky
Pillar 2F. Per-warp identity (`dest_warp_id` / `anchor`) is dropped on purpose —
at pair granularity it carries no information the graph needs, and dropping it
also disposes of warp-id pairing, which is the one genuinely fiddly part.

Two edge kinds, because two different things let the player leave a map:

  warp        a warp tile — doors, stairs, cave mouths, dive spots, holes
  connection  walking off the border into the neighbouring map, no warp at all

`connection` is what makes the overworld a graph at all: Route 101 touches
Oldale Town without either map holding a warp to the other. Emerald states
these outright in `map.json`; the Gen 4 games do not, so they are recovered
from map-matrix adjacency — two maps are connected if their blocks sit next to
each other on the shared world matrix. Maps that own their matrix (every cave
and interior) have no neighbours by construction, which is correct.

Each edge carries `bidirectional`. One-way edges are real — the Trick House
rooms, Seafloor Cavern, a dive warp with no surfacing partner — but most of
them are an artifact instead: a map whose only way out is a *dynamic* warp,
which has no destination written down anywhere (see MAP_DYNAMIC below).
`--stats` reports the split so a one-way edge can be read for what it is.

Dynamic destinations are dropped rather than rendered as dangling. They are
elevators and their kin: the warp reads a destination the script wrote at
runtime, so there is no static answer to record. Each game spells the
placeholder differently — Emerald `MAP_DYNAMIC`, HeartGold the sentinel 4095,
Platinum `MAP_HEADER_DYNAMIC` — and all three are counted, not silently eaten.

Unused maps are excluded exactly as the renderer excludes them, via the shared
`terrain_helpers.skip_map()` — which also drops the `EVERYWHERE` headers, since
those name the shared world matrix rather than any place. `--include-unused`
restores the unused maps here too; it does not restore the non-maps.
This module is *only* a data source: it reads the decomps, writes JSON, and
imports nothing from the renderer beyond that one filter.
"""

import json
import sys
from collections import defaultdict

import emerald_data
import heartgold_data
import platinum_data
from terrain_helpers import is_non_map, skip_map

#: Destinations with no static target — see the module docstring. HeartGold
#: writes its sentinel as a bare number, so the check is by string form.
_DYNAMIC_DESTS = {"MAP_DYNAMIC", "MAP_HEADER_DYNAMIC", "MAP_UNDEFINED",
                  "4095", "MAP_NONE", "MAP_NOTHING", "MAP_HEADER_NOTHING"}


def _is_dynamic(dest: object) -> bool:
    return str(dest).strip().upper() in _DYNAMIC_DESTS


# ---------------------------------------------------------------------------
# Per-game collectors
#
# Each returns (nodes, edges):
#   nodes  {map id: {id, name, map_type, ...}}
#   edges  {(src, dst): {kinds}}   — directed, folded into pairs later
# and a dict of counters for --stats.
# ---------------------------------------------------------------------------

def _collect_emerald() -> tuple[dict, dict, dict]:
    """
    Emerald is layout-driven everywhere else in this repo, but the graph is
    map-driven: two maps sharing `PokemonCenter_1F_Layout` are different
    places with different doors. So the layout registry is walked only to
    enumerate maps, and every map is its own node.
    """
    nodes: dict = {}
    edges: dict = defaultdict(set)
    counts: dict = defaultdict(int)

    for layout in emerald_data.layout_registry_entries():
        for m in emerald_data.map_entries(layout["layout_id"]):
            mid = m.get("id")
            if not mid:
                continue
            nodes[mid] = {"id":        mid,
                          "name":      m.get("name") or m.get("map_name") or mid,
                          "map_type":  m.get("map_type", ""),
                          "layout_id": layout["layout_id"]}

            for w in m.get("warp_events") or []:
                dest = w.get("dest_map")
                if _is_dynamic(dest):
                    counts["dynamic"] += 1
                    continue
                if dest:
                    edges[(mid, dest)].add("warp")

            for c in m.get("connections") or []:
                dest = c.get("map")
                if dest and not _is_dynamic(dest):
                    # `dive` / `emerge` are vertical, not border-crossing, but
                    # they are still a way from one map to another.
                    kind = "warp" if c.get("direction") in ("dive", "emerge") \
                           else "connection"
                    edges[(mid, dest)].add(kind)

    return nodes, edges, counts


def _collect_heartgold() -> tuple[dict, dict, dict]:
    nodes: dict = {}
    edges: dict = defaultdict(set)
    counts: dict = defaultdict(int)

    # Dropped before anything reads the matrix: MAP_EVERYWHERE owns 598 of the
    # world matrix's 799 cells, so left in it would border half of Johto.
    registry = [r for r in heartgold_data.map_registry_entries()
                if not is_non_map(r["constant"])]
    for r in registry:
        nodes[r["constant"]] = {"id":       r["constant"],
                                "name":     r["name"],
                                "map_type": r.get("map_type", ""),
                                "code":     r.get("internal_code", "")}

    for r in registry:
        src = r["constant"]
        for w in heartgold_data.zone_warps(r.get("internal_code") or ""):
            dest = w.get("header")
            if _is_dynamic(dest):
                counts["dynamic"] += 1
                continue
            if dest:
                edges[(src, dest)].add("warp")

    # Border adjacency, for the 97 maps sharing map_matrix_0000_EVERYWHERE.
    # Maps owning their matrix are alone on it and pick up nothing here.
    by_zone = {r["zone_id"]: r["constant"] for r in registry
               if r.get("zone_id") is not None and not r.get("own_matrix")}
    matrix  = heartgold_data._get_matrix()
    width   = matrix["width"]
    headers = matrix["headers"]
    for i, zone in enumerate(headers):
        src = by_zone.get(zone)
        if src is None:
            continue
        row, col = divmod(i, width)
        for nr, nc in ((row - 1, col), (row + 1, col),
                       (row, col - 1), (row, col + 1)):
            if not (0 <= nr and 0 <= nc < width):
                continue
            j = nr * width + nc
            if j >= len(headers):
                continue
            dst = by_zone.get(headers[j])
            if dst and dst != src:
                edges[(src, dst)].add("connection")

    return nodes, edges, counts


def _collect_platinum() -> tuple[dict, dict, dict]:
    nodes: dict = {}
    edges: dict = defaultdict(set)
    counts: dict = defaultdict(int)

    registry = [r for r in platinum_data.map_registry_entries()
                if not is_non_map(r["constant"])]
    for r in registry:
        nodes[r["constant"]] = {"id":       r["constant"],
                                "name":     r["name"],
                                "map_type": r.get("map_type", "")}

    for r in registry:
        src   = r["constant"]
        entry = platinum_data.map_entry(src)
        if entry is None:
            continue
        for w in entry.get("warp_events") or []:
            dest = w.get("dest_header_id")
            if _is_dynamic(dest):
                counts["dynamic"] += 1
                continue
            if dest:
                edges[(src, dest)].add("warp")

        # Border adjacency. _get_connections already walks the matrix headers
        # plane and skips the map's own cells; reusing it keeps the graph and
        # the renderer's connection list from ever disagreeing.
        for c in platinum_data._get_connections(entry):
            dst = c.get("dest")
            if dst and dst != src and not _is_dynamic(dst):
                edges[(src, dst)].add("connection")

    return nodes, edges, counts


_COLLECTORS = {"emerald":   _collect_emerald,
               "heartgold": _collect_heartgold,
               "platinum":  _collect_platinum}


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def build_graph(game: str, include_unused: bool = False) -> dict:
    """
    The full map graph for one game. Directed observations are folded into
    undirected pairs carrying `bidirectional`, since "A and B are connected"
    is the question, and which side the door was recorded on is not.
    """
    collect = _COLLECTORS.get(game)
    if collect is None:
        raise ValueError(f"unknown game: {game}")
    nodes, directed, counts = collect()

    if not include_unused:
        dropped = {mid for mid, n in nodes.items()
                   if skip_map(game, mid, n.get("name"),
                               n.get("layout_id"))}
        counts["unused_maps"] = len(dropped)
        nodes = {k: v for k, v in nodes.items() if k not in dropped}

    # Warps to maps outside the node set are dangling — either an unused map
    # we just dropped, or a destination the registry has no header for.
    edges: dict = {}
    for (src, dst), kinds in directed.items():
        # A map warping to itself — the Regi chambers' puzzle doors, Sky
        # Pillar's internal stairs. Real warps, but at pair granularity a
        # self-loop says nothing beyond "this map exists".
        if src == dst:
            counts["self_loop"] += 1
            continue
        if src not in nodes or dst not in nodes:
            counts["dangling"] += 1
            continue
        key = (src, dst) if src <= dst else (dst, src)
        e = edges.setdefault(key, {"a": key[0], "b": key[1],
                                   "kinds": set(), "seen": set()})
        e["kinds"] |= kinds
        e["seen"].add((src, dst))

    out_edges = []
    for e in edges.values():
        bidi = len(e["seen"]) == 2
        edge = {"a": e["a"], "b": e["b"],
                "kinds": sorted(e["kinds"]),
                "bidirectional": bidi}
        if not bidi:
            src, dst = next(iter(e["seen"]))
            edge["from"] = src
            edge["to"]   = dst
            counts["one_way"] += 1
        else:
            counts["bidirectional"] += 1
        out_edges.append(edge)

    out_edges.sort(key=lambda e: (e["a"], e["b"]))
    isolated = sorted(set(nodes) - {n for e in out_edges for n in (e["a"], e["b"])})
    counts["isolated"] = len(isolated)

    return {"game":     game,
            "source":   "decomp",
            "nodes":    [nodes[k] for k in sorted(nodes)],
            "edges":    out_edges,
            "isolated": isolated,
            "stats":    {"nodes": len(nodes), "edges": len(out_edges),
                         **{k: counts[k] for k in sorted(counts)}}}


def main() -> int:
    args  = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if not args:
        print(__doc__.strip())
        return 1

    game = args[0].lower()
    if game not in _COLLECTORS:
        print(f"unknown game: {game} (emerald | heartgold | platinum)")
        return 1

    graph = build_graph(game, include_unused="--include-unused" in flags)
    out   = args[1] if len(args) > 1 else f"map_graph_{game}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(graph, f, indent=1)

    s = graph["stats"]
    print(f"{game}: {s['nodes']} maps, {s['edges']} edges "
          f"({s.get('bidirectional', 0)} bidirectional, "
          f"{s.get('one_way', 0)} one-way) → {out}")
    if "--stats" in flags:
        for k in sorted(s):
            print(f"  {k:>15}: {s[k]}")
        kinds: dict = defaultdict(int)
        for e in graph["edges"]:
            kinds[",".join(e["kinds"])] += 1
        for k, v in sorted(kinds.items()):
            print(f"  {k:>15}: {v} edges")
        if graph["isolated"]:
            print(f"  isolated: {', '.join(graph['isolated'][:12])}"
                  + (" ..." if len(graph["isolated"]) > 12 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
