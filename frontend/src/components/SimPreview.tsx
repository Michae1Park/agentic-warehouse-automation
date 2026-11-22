import Panel from "./Panel";

const SIM_URL = import.meta.env.VITE_SIM_URL as string | undefined;

export default function SimPreview() {
  return (
    <Panel title="Live Simulation" area="preview">
      {SIM_URL ? (
        <a className="sim-preview" href="#/sim" title="Open the live simulation">
          <img className="sim-stream" src={`${SIM_URL}/stream.mjpg`} alt="Isaac Sim live stream" />
          <span className="sim-help">Open full view →</span>
        </a>
      ) : (
        <div className="sim-placeholder">Isaac Sim not connected.</div>
      )}
    </Panel>
  );
}
