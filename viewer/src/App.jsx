import React, { useCallback, useEffect, useMemo, useState } from "react";
import ReactFlow, {
  Background, Controls, MiniMap, Handle, Position, ReactFlowProvider,
} from "reactflow";
import { buildFlow, buildFocus, indexBundle, KIND_COLOR } from "./layout.js";
import Dock from "./Dock.jsx";

const GAMES = [
  { id: "emerald",   label: "Emerald" },
  { id: "heartgold", label: "HeartGold" },
  { id: "platinum",  label: "Platinum" },
];

/* ------------------------------------------------------------------ node -- */

function MapNode({ data }) {
  const { node, childCount, expanded, selected, container, child, dim } = data;
  const colour = KIND_COLOR[node.kind] ?? "#555";
  return (
    <div
      className={
        "mapnode" + (container ? " container" : "") +
        (child ? " child" : "") + (selected ? " selected" : "") +
        (dim ? " dim" : "")
      }
      style={{ borderColor: colour }}
    >
      <Handle type="target" position={Position.Top} className="handle" />
      <div className="mapnode-bar" style={{ background: colour }}>
        <span className="mapnode-name">{node.name}</span>
        {node.floor != null && (
          <span className="mapnode-floor">
            {node.floor === 99 ? "roof"
              : node.floor < 0 ? `B${-node.floor}F` : `${node.floor}F`}
          </span>
        )}
        {childCount > 0 && (
          <span className="mapnode-count">{expanded ? "−" : `+${childCount}`}</span>
        )}
      </div>
      {!child && <div className="mapnode-meta">{node.w}×{node.h}</div>}
      <Handle type="source" position={Position.Bottom} className="handle" />
    </div>
  );
}

const nodeTypes = { map: MapNode };

/* ------------------------------------------------------------- focus view -- */

// The town on its own, with only what is inside it. Same placement as the main
// canvas, so a building sits in the same relative spot in both views.
function FocusView({ bundle, focusId, onPick }) {
  const flow = useMemo(
    () => (bundle && focusId ? buildFocus(bundle, focusId)
                             : { nodes: [], edges: [] }),
    [bundle, focusId]
  );

  if (!flow.nodes.length) return <p className="muted pad">Nothing inside this map.</p>;

  return (
    <ReactFlowProvider>
      <ReactFlow
        nodes={flow.nodes}
        edges={flow.edges}
        nodeTypes={nodeTypes}
        onNodeClick={(_, n) => onPick(n.id)}
        minZoom={0.15}
        maxZoom={2.5}
        fitView
        fitViewOptions={{ padding: 0.1 }}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={22} color="#1c212a" />
        <Controls showInteractive={false} position="bottom-right" />
      </ReactFlow>
    </ReactFlowProvider>
  );
}

/* ------------------------------------------------------------- ascii pane -- */

function AsciiView({ node }) {
  const [text, setText] = useState(null);
  const [error, setError] = useState(null);
  const [size, setSize] = useState(11);

  useEffect(() => {
    setText(null);
    setError(null);
    if (!node?.ascii) {
      setError("This map has no terrain to render.");
      return;
    }
    let live = true;
    fetch(node.ascii)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(r.status))))
      .then((t) => live && setText(t))
      .catch(() => live && setError("Could not load the render."));
    return () => { live = false; };
  }, [node]);

  return (
    <div className="ascii">
      <div className="ascii-bar">
        <span className="muted">{node?.name}</span>
        <div className="zoom">
          <button onClick={() => setSize((s) => Math.max(5, s - 1))}>−</button>
          <span>{size}px</span>
          <button onClick={() => setSize((s) => Math.min(20, s + 1))}>+</button>
        </div>
      </div>
      <div className="ascii-wrap">
        {error && <p className="muted">{error}</p>}
        {!error && !text && <p className="muted">Loading…</p>}
        {text && <pre style={{ fontSize: size + "px", lineHeight: 1.05 }}>{text}</pre>}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------- app -- */

export default function App() {
  const [game, setGame]         = useState("emerald");
  const [bundle, setBundle]     = useState(null);
  const [expanded, setExpanded] = useState(() => new Set());
  const [selected, setSelected] = useState(null);
  const [query, setQuery]       = useState("");

  useEffect(() => {
    setBundle(null);
    setExpanded(new Set());
    setSelected(null);
    fetch(`data/${game}.json`)
      .then((r) => r.json())
      .then(setBundle)
      .catch(() => setBundle({ nodes: [], edges: [], error: true }));
  }, [game]);

  const index = useMemo(
    () => (bundle ? indexBundle(bundle) : { byId: new Map(), children: new Map() }),
    [bundle]
  );

  const { nodes, edges } = useMemo(
    () => (bundle ? buildFlow(bundle, expanded, selected)
                  : { nodes: [], edges: [] }),
    [bundle, expanded, selected]
  );

  const onNodeClick = useCallback((_, rfNode) => {
    const id = rfNode.id;
    setSelected(id);
    setExpanded((prev) => {
      const next = new Set(prev);
      if ((index.children.get(id) ?? []).length > 0) {
        next.has(id) ? next.delete(id) : next.add(id);
      }
      return next;
    });
  }, [index]);

  const pick = useCallback((id) => {
    setSelected(id);
    const n = index.byId.get(id);
    if (n?.parent) setExpanded((prev) => new Set(prev).add(n.parent));
  }, [index]);

  const hits = useMemo(() => {
    if (!bundle || query.trim().length < 2) return [];
    const q = query.trim().toLowerCase();
    return bundle.nodes
      .filter((n) => n.name.toLowerCase().includes(q) || n.id.toLowerCase().includes(q))
      .slice(0, 12);
  }, [bundle, query]);

  const selectedNode = selected ? index.byId.get(selected) : null;

  // The dock always frames a *container*: click a house and it still shows the
  // town, with the house highlighted inside it, because that is the context you
  // need to see where the house sits.
  const focusId = selectedNode
    ? ((index.children.get(selectedNode.id) ?? []).length > 0
        ? selectedNode.id
        : selectedNode.parent ?? selectedNode.id)
    : null;
  const focusNode = focusId ? index.byId.get(focusId) : null;
  const focusKids = focusId ? (index.children.get(focusId) ?? []) : [];

  return (
    <div className="app">
      <nav className="bar">
        <strong>Map graph</strong>
        <div className="games">
          {GAMES.map((g) => (
            <button key={g.id} className={g.id === game ? "on" : ""}
                    onClick={() => setGame(g.id)}>
              {g.label}
            </button>
          ))}
        </div>

        <div className="search">
          <input value={query} placeholder="Find a map…"
                 onChange={(e) => setQuery(e.target.value)} />
          {hits.length > 0 && (
            <ul className="hits">
              {hits.map((h) => (
                <li key={h.id}>
                  <button onClick={() => { pick(h.id); setQuery(""); }}>
                    <span>{h.name}</span>
                    <em style={{ color: KIND_COLOR[h.kind] }}>{h.kind}</em>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="legend">
          <span><i className="solid" /> border</span>
          <span><i className="thin" /> warp</span>
          <span><i className="dashed" /> one-way</span>
          <span><i className="lit" /> selected</span>
        </div>

        {bundle?.stats && (
          <span className="muted count">
            {bundle.stats.nodes} maps · {bundle.stats.edges} edges
          </span>
        )}
      </nav>

      <div className="canvas">
        <ReactFlowProvider>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodeClick={onNodeClick}
            onPaneClick={() => setSelected(null)}
            minZoom={0.05}
            maxZoom={3}
            fitView
            fitViewOptions={{ padding: 0.15 }}
            proOptions={{ hideAttribution: true }}
          >
            <Background gap={28} color="#20242c" />
            <Controls showInteractive={false} />
            <MiniMap pannable zoomable
                     nodeColor={(n) => KIND_COLOR[n.data?.node?.kind] ?? "#555"}
                     maskColor="rgba(10,12,16,0.7)" />
          </ReactFlow>
        </ReactFlowProvider>

        {selectedNode && (
          <Dock
            title={focusNode?.name ?? selectedNode.name}
            subtitle={focusNode?.id ?? selectedNode.id}
            onClose={() => setSelected(null)}
            topLabel={
              focusKids.length
                ? `Inside — ${focusKids.length} map${focusKids.length > 1 ? "s" : ""}`
                : "Inside"
            }
            bottomLabel="ASCII layout"
            top={<FocusView bundle={bundle} focusId={focusId} onPick={pick} />}
            bottom={<AsciiView node={selectedNode} />}
          />
        )}
      </div>
    </div>
  );
}
