import { useLive } from "../api";
import type { Robot, Task } from "../types";
import Panel from "./Panel";

function stepLabel(step: Task["steps"][number]) {
  const args = step.args as Record<string, string | number>;
  const target = [args.sku, args.location_id, args.quantity ? `×${args.quantity}` : ""].filter(Boolean).join(" ");
  return `${step.action} ${target}`;
}

function GraspDetail({ step }: { step: Task["steps"][number] }) {
  const policies = step.result?.grasp_policies ?? [];
  if (!policies.length) return null;
  const instruction = step.result?.instructions?.at(-1);
  return (
    <div className="grasp-detail">
      {policies.map((policy, i) => (
        <span key={i} className={`badge policy-${policy}`}>{policy.replace("_", " ")}</span>
      ))}
      {instruction && <span className="instruction">“{instruction}”</span>}
    </div>
  );
}

function TaskProgress({ taskId }: { taskId: number }) {
  const task = useLive<Task>(`/api/tasks/${taskId}`, ["tasks", "task_steps"]);
  if (!task?.steps) return null;
  return (
    <ol className="steps">
      {task.steps.map((step) => (
        <li key={step.seq} className={step.status}>
          <span className="step-dot" />
          <span className="mono">{stepLabel(step)}</span>
          <GraspDetail step={step} />
        </li>
      ))}
    </ol>
  );
}

export default function RobotPanel() {
  const robots = useLive<Robot[]>("/api/robots", ["robots"]) ?? [];

  return (
    <Panel title="Robot" area="robots">
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
            <dt>Tote</dt>
            <dd className="mono">{robot.tote.length ? robot.tote.join(", ") : "—"}</dd>
            <dt>Task</dt>
            <dd className="mono">{robot.current_task_id ?? "—"}</dd>
          </dl>
          {robot.current_task_id && <TaskProgress taskId={robot.current_task_id} />}
        </div>
      ))}
    </Panel>
  );
}
