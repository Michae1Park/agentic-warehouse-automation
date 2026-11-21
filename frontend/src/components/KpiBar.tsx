import { useLive } from "../api";
import type { Order, WarehouseException } from "../types";

export default function KpiBar() {
  const orders = useLive<Order[]>("/api/orders", ["orders"]) ?? [];
  const exceptions = useLive<WarehouseException[]>("/api/exceptions?status=open", ["exceptions"]) ?? [];
  const count = (status: Order["status"]) => orders.filter((o) => o.status === status).length;

  const kpis = [
    { label: "Pending", value: count("pending") },
    { label: "In progress", value: count("in_progress") },
    { label: "Completed", value: count("completed") },
    { label: "Open exceptions", value: exceptions.length },
  ];

  return (
    <div className="kpis">
      {kpis.map((kpi) => (
        <div key={kpi.label} className="kpi">
          <span className="kpi-value">{kpi.value}</span>
          <span className="kpi-label">{kpi.label}</span>
        </div>
      ))}
    </div>
  );
}
