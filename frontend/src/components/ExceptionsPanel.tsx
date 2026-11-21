import { useLive } from "../api";
import type { WarehouseException } from "../types";
import Panel from "./Panel";

export default function ExceptionsPanel() {
  const exceptions = useLive<WarehouseException[]>("/api/exceptions", ["exceptions"]) ?? [];

  return (
    <Panel title="Exceptions" area="exceptions">
      {exceptions.length === 0 && <p className="muted">No exceptions reported.</p>}
      <ul className="exceptions">
        {exceptions.map((exc) => (
          <li key={exc.id} className={exc.severity}>
            <div className="robot-head">
              <strong>{exc.type}</strong>
              <span className={`badge ${exc.status}`}>{exc.status}</span>
            </div>
            <p>{exc.message}</p>
            {exc.order_id && <span className="muted mono">Order #{exc.order_id}</span>}
          </li>
        ))}
      </ul>
    </Panel>
  );
}
