import { useLive } from "../api";
import type { Robot } from "../types";
import Panel from "./Panel";

export default function RobotPanel() {
  const robots = useLive<Robot[]>("/api/robots", ["robots"]) ?? [];

  return (
    <Panel title="Robots" area="robots">
      {robots.map((robot) => (
        <div key={robot.id} className="robot">
          <div className="robot-head">
            <strong>{robot.name}</strong>
            <span className={`badge ${robot.status}`}>{robot.status}</span>
          </div>
          <dl>
            <dt>Model</dt>
            <dd>{robot.model}</dd>
            <dt>Position</dt>
            <dd className="mono">
              ({Number(robot.x).toFixed(1)}, {Number(robot.y).toFixed(1)})
            </dd>
            <dt>Battery</dt>
            <dd className="mono">{Number(robot.battery_pct).toFixed(0)}%</dd>
            <dt>Holding</dt>
            <dd className="mono">{robot.holding_sku ?? "—"}</dd>
            <dt>Task</dt>
            <dd className="mono">{robot.current_task_id ?? "—"}</dd>
          </dl>
        </div>
      ))}
    </Panel>
  );
}
