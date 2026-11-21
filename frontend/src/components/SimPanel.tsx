import { useState } from "react";
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
          <img className="sim-stream" src={`${SIM_URL}/stream.mjpg`} alt="Isaac Sim live stream" />
        ) : (
          <div className="sim-placeholder">Isaac Sim not connected (start with the isaac profile and set SIM_URL).</div>
        ))}
    </Panel>
  );
}
