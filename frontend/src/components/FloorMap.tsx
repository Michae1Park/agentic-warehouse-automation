import { useLive } from "../api";
import type { Location, Robot, Task } from "../types";

const WIDTH = 14;
const HEIGHT = 8;

function ActiveRoute({ taskId, locations }: { taskId: number; locations: Map<string, Location> }) {
  const task = useLive<Task>(`/api/tasks/${taskId}`, ["task_steps", "tasks"]);
  if (!task?.steps) return null;
  const stops = task.steps
    .filter((step) => step.action === "navigate")
    .map((step) => ({ step, location: locations.get(step.args.location_id as string) }))
    .filter((stop): stop is { step: Task["steps"][number]; location: Location } => !!stop.location);

  return (
    <g>
      <polyline
        className="route"
        points={stops.map(({ location }) => `${location.x},${location.y}`).join(" ")}
      />
      {stops.map(({ step, location }, i) => (
        <g key={step.seq}>
          <circle className={`stop ${step.status}`} cx={location.x} cy={location.y} r={0.28} />
          <text className="stop-label" x={location.x} y={Number(location.y) + 0.1}>
            {i + 1}
          </text>
        </g>
      ))}
    </g>
  );
}

export default function FloorMap() {
  const locations = useLive<Location[]>("/api/locations", ["inventory"]) ?? [];
  const robots = useLive<Robot[]>("/api/robots", ["robots"]) ?? [];
  const byId = new Map(locations.map((l) => [l.id, l]));
  const aisles = [...new Set(locations.filter((l) => l.aisle).map((l) => l.aisle!))];

  return (
    <svg className="floor" viewBox={`-1 -1.2 ${WIDTH + 2} ${HEIGHT + 2.8}`} preserveAspectRatio="xMidYMid meet">
      {aisles.map((aisle) => {
        const slots = locations.filter((l) => l.aisle === aisle);
        const x = Number(slots[0].x);
        const ys = slots.map((l) => Number(l.y));
        return (
          <g key={aisle}>
            <rect className="shelf" x={x - 0.35} y={Math.min(...ys) - 0.6} width={0.7} height={Math.max(...ys) - Math.min(...ys) + 1.2} />
            <text className="aisle-label" x={x} y={Math.min(...ys) - 0.8}>
              Aisle {aisle}
            </text>
          </g>
        );
      })}
      {locations
        .filter((l) => l.kind === "storage" && l.level === 1)
        .map((l) => {
          const stocked = locations.some((o) => o.aisle === l.aisle && o.bay === l.bay && o.stock.length > 0);
          return <rect key={l.id} className={`bin ${stocked ? "stocked" : ""}`} x={Number(l.x) - 0.25} y={Number(l.y) - 0.25} width={0.5} height={0.5} />;
        })}
      {locations
        .filter((l) => l.kind !== "storage")
        .map((l) => (
          <g key={l.id}>
            <rect className={`zone ${l.kind}`} x={Number(l.x) - 0.7} y={Number(l.y) - 0.6} width={1.4} height={1.2} />
            <text className="zone-label" x={l.x} y={Number(l.y) + 1}>
              {l.id}
            </text>
          </g>
        ))}
      {robots.map((robot) => robot.current_task_id && <ActiveRoute key={robot.id} taskId={robot.current_task_id} locations={byId} />)}
      {robots.map((robot) => (
        <g key={robot.id} transform={`translate(${robot.x} ${robot.y}) rotate(${(Number(robot.theta) * 180) / Math.PI})`}>
          <circle className={`robot-dot ${robot.status}`} r={0.35} />
          <polygon className="robot-heading" points="0.45,0 0.15,-0.18 0.15,0.18" />
        </g>
      ))}
    </svg>
  );
}
