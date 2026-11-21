import { useLive } from "../api";
import type { Order } from "../types";

type Kpis = {
  orders_requested: number;
  orders_completed: number;
  human_interventions: number;
  faults: number;
  faults_recovered: number;
  avg_agent_turn_ms: number | null;
};

const pct = (num: number, den: number) => (den ? `${Math.round((num / den) * 100)}%` : "—");

export default function KpiBar() {
  const orders = useLive<Order[]>("/api/orders", ["orders"]) ?? [];
  const kpis = useLive<Kpis>("/api/kpis", ["orders", "exceptions", "tasks", "task_steps", "agent_messages"]);
  const count = (status: Order["status"]) => orders.filter((o) => o.status === status).length;

  const tiles = [
    { label: "Pending", value: String(count("pending")) },
    { label: "In progress", value: String(count("in_progress")) },
    { label: "Fulfilled", value: kpis ? pct(kpis.orders_completed, kpis.orders_requested) : "—" },
    { label: "Interventions", value: kpis ? String(kpis.human_interventions) : "—" },
    { label: "Recovered faults", value: kpis ? pct(kpis.faults_recovered, kpis.faults) : "—" },
    { label: "Agent latency", value: kpis?.avg_agent_turn_ms ? `${(kpis.avg_agent_turn_ms / 1000).toFixed(1)}s` : "—" },
  ];

  return (
    <div className="kpis">
      {tiles.map((tile) => (
        <div key={tile.label} className="kpi">
          <span className="kpi-value">{tile.value}</span>
          <span className="kpi-label">{tile.label}</span>
        </div>
      ))}
    </div>
  );
}
