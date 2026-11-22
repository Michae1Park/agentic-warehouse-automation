import { useRef, useState } from "react";
import FloorMap from "./FloorMap";
import Panel from "./Panel";

const SIM_URL = import.meta.env.VITE_SIM_URL as string | undefined;
const CAMERAS = ["overview", "chase", "inspect"] as const;

export default function SimPanel() {
  const [view, setView] = useState<"map" | "sim">(SIM_URL ? "sim" : "map");
  const [camera, setCamera] = useState<(typeof CAMERAS)[number]>("overview");

  const selectCamera = (next: (typeof CAMERAS)[number]) => {
    setCamera(next);
    fetch(`${SIM_URL}/view`, { method: "POST", body: JSON.stringify({ view: next }) }).catch(() => undefined);
  };

  const drag = useRef<{ x: number; y: number; pan: boolean } | null>(null);
  const pending = useRef<{ orbit: [number, number]; pan: [number, number]; zoom: number } | null>(null);

  const sendCamera = (body: object) =>
    fetch(`${SIM_URL}/camera`, { method: "POST", body: JSON.stringify(body) }).catch(() => undefined);

  const queueCamera = (orbit: [number, number], pan: [number, number], zoom: number) => {
    if (pending.current) {
      const p = pending.current;
      p.orbit = [p.orbit[0] + orbit[0], p.orbit[1] + orbit[1]];
      p.pan = [p.pan[0] + pan[0], p.pan[1] + pan[1]];
      p.zoom *= zoom;
      return;
    }
    pending.current = { orbit, pan, zoom };
    setCamera("overview");
    setTimeout(() => {
      const body = pending.current;
      pending.current = null;
      if (body) sendCamera(body);
    }, 50);
  };

  const onPointerDown = (e: React.PointerEvent) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, pan: e.button === 2 || e.shiftKey };
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag.current) return;
    const { width, height } = e.currentTarget.getBoundingClientRect();
    const dx = (e.clientX - drag.current.x) / width;
    const dy = (e.clientY - drag.current.y) / height;
    drag.current = { ...drag.current, x: e.clientX, y: e.clientY };
    if (drag.current.pan) queueCamera([0, 0], [dx, dy], 1);
    else queueCamera([dx * Math.PI * 1.5, dy * Math.PI * 0.75], [0, 0], 1);
  };

  const onWheel = (e: React.WheelEvent) => queueCamera([0, 0], [0, 0], Math.exp(e.deltaY * 0.0015));

  return (
    <Panel title="Live Simulation" area="sim">
      <div className="tabs">
        <button className={view === "sim" ? "active" : ""} onClick={() => setView("sim")}>
          Isaac Sim
        </button>
        <button className={view === "map" ? "active" : ""} onClick={() => setView("map")}>
          Floor map
        </button>
        {view === "sim" && SIM_URL && (
          <span className="camera-select">
            {CAMERAS.map((name) => (
              <button key={name} className={camera === name ? "active" : ""} onClick={() => selectCamera(name)}>
                {name}
              </button>
            ))}
          </span>
        )}
      </div>
      {view === "map" && <FloorMap />}
      {view === "sim" &&
        (SIM_URL ? (
          <img
            className="sim-stream interactive"
            src={`${SIM_URL}/stream.mjpg`}
            alt="Isaac Sim live stream"
            title="Drag: orbit · Right/Shift-drag: pan · Wheel: zoom · Double-click: reset"
            draggable={false}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={() => (drag.current = null)}
            onWheel={onWheel}
            onDoubleClick={() => {
              setCamera("overview");
              sendCamera({ reset: true });
            }}
            onContextMenu={(e) => e.preventDefault()}
          />
        ) : (
          <div className="sim-placeholder">Isaac Sim not connected (start with the isaac profile and set SIM_URL).</div>
        ))}
    </Panel>
  );
}
