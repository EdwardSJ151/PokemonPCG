import React, { useCallback, useEffect, useRef, useState } from "react";

// The right-hand dock: drag its left edge to resize, drag the bar between the
// two halves to give more room to either the town view or the ASCII.
//
// Sizes are held here rather than in CSS because both are dragged, and both are
// remembered for the session so the layout does not reset every time you click
// a different map.

const MIN_W = 340;
const MIN_PANE = 90;

export function useDrag(onMove) {
  const active = useRef(false);

  const down = useCallback((e) => {
    active.current = true;
    e.preventDefault();
    const move = (ev) => active.current && onMove(ev);
    const up = () => {
      active.current = false;
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      document.body.classList.remove("dragging");
    };
    document.body.classList.add("dragging");
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }, [onMove]);

  return down;
}

export default function Dock({ title, subtitle, onClose, top, bottom,
                              topLabel, bottomLabel }) {
  const [width, setWidth] = useState(() =>
    Math.min(760, Math.max(MIN_W, Math.round(window.innerWidth * 0.42))));
  const [split, setSplit] = useState(0.5);      // share of height for the top pane
  const ref = useRef(null);

  const onWidth = useCallback((e) => {
    setWidth(Math.max(MIN_W, Math.min(window.innerWidth - 260,
                                      window.innerWidth - e.clientX)));
  }, []);

  const onSplit = useCallback((e) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box) return;
    const frac = (e.clientY - box.top) / box.height;
    const minFrac = MIN_PANE / box.height;
    setSplit(Math.max(minFrac, Math.min(1 - minFrac, frac)));
  }, []);

  const startWidth = useDrag(onWidth);
  const startSplit = useDrag(onSplit);

  useEffect(() => {
    const onResize = () =>
      setWidth((w) => Math.min(w, Math.max(MIN_W, window.innerWidth - 260)));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  return (
    <aside className="dock" style={{ width }}>
      <div className="grip grip-x" onPointerDown={startWidth}
           title="Drag to resize" />
      <header className="dock-head">
        <div className="dock-title">
          <h2>{title}</h2>
          {subtitle && <code>{subtitle}</code>}
        </div>
        <button className="close" onClick={onClose} title="Close">×</button>
      </header>

      <div className="dock-body" ref={ref}>
        <section className="dock-pane" style={{ flexBasis: `${split * 100}%` }}>
          <div className="dock-pane-label">{topLabel}</div>
          <div className="dock-pane-inner">{top}</div>
        </section>

        <div className="grip grip-y" onPointerDown={startSplit}
             title="Drag to resize" />

        <section className="dock-pane" style={{ flexBasis: `${(1 - split) * 100}%` }}>
          <div className="dock-pane-label">{bottomLabel}</div>
          <div className="dock-pane-inner">{bottom}</div>
        </section>
      </div>
    </aside>
  );
}
