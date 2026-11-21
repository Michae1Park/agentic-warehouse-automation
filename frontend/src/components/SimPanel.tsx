import { useState } from "react";
import FloorMap from "./FloorMap";
import Panel from "./Panel";

const STREAM_URL = import.meta.env.VITE_SIM_STREAM_URL as string | undefined;

export default function SimPanel() {
  const [view, setView] = useState<"map" | "sim">(STREAM_URL ? "sim" : "map");

  return (
    <Panel title="Live Simulation" area="sim">
      <div className="tabs">
        <button className={view === "sim" ? "active" : ""} onClick={() => setView("sim")}>
          Isaac Sim
        </button>
        <button className={view === "map" ? "active" : ""} onClick={() => setView("map")}>
          Floor map
        </button>
      </div>
      {view === "map" && <FloorMap />}
      {view === "sim" &&
        (STREAM_URL ? (
          <iframe className="sim-stream" src={STREAM_URL} title="Isaac Sim stream" allow="autoplay" />
        ) : (
          <div className="sim-placeholder">Isaac Sim stream not configured (set VITE_SIM_STREAM_URL).</div>
        ))}
    </Panel>
  );
}
