import { useEffect, useRef, useState } from "react";
import FloorMap from "./FloorMap";
import Icon from "./Icon";
import KpiBar from "./KpiBar";
import OrdersPanel from "./OrdersPanel";
import RobotPanel from "./RobotPanel";

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

  const stream = useRef<HTMLImageElement>(null);
  const zoom = useRef(queueCamera);
  zoom.current = queueCamera;

  useEffect(() => {
    const element = stream.current;
    if (!element) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      zoom.current([0, 0], [0, 0], Math.exp(e.deltaY * 0.0015));
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [view]);

  const resetView = () => {
    setCamera("overview");
    sendCamera({ reset: true });
  };

  return (
    <section className="stage">
      <div className="viewport">
      {view === "map" && <FloorMap />}
      {view === "sim" &&
        (SIM_URL ? (
          <img
            ref={stream}
            className="sim-stream interactive"
            src={`${SIM_URL}/stream.mjpg`}
            alt="Isaac Sim live stream"
            draggable={false}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={() => (drag.current = null)}
            onDoubleClick={resetView}
            onContextMenu={(e) => e.preventDefault()}
          />
        ) : (
          <div className="sim-placeholder">Isaac Sim not connected</div>
        ))}
      <div className="toolbar glass">
        <span className="live-dot" />
        <div className="segmented">
          <button className={view === "sim" ? "active" : ""} onClick={() => setView("sim")}>
            3D
          </button>
          <button className={view === "map" ? "active" : ""} onClick={() => setView("map")}>
            Map
          </button>
        </div>
        {view === "sim" && SIM_URL && (
          <div className="segmented">
            {CAMERAS.map((name) => (
              <button key={name} className={camera === name ? "active" : ""} onClick={() => selectCamera(name)}>
                {name}
              </button>
            ))}
            <button onClick={resetView} title="Reset view">
              <Icon name="reset" size={14} />
            </button>
          </div>
        )}
      </div>
      {view === "sim" && SIM_URL && (
        <div className="sim-help">Drag orbit · Shift-drag pan · Scroll zoom · Double-click reset</div>
      )}
      </div>
      <div className="stage-kpis">
        <KpiBar />
      </div>
      <aside className="stage-side">
        <RobotPanel />
        <OrdersPanel title="Awaiting" area="orders" statuses={["pending", "in_progress"]} empty="No orders waiting." compact />
      </aside>
    </section>
  );
}
