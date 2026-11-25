import { useLive } from "../api";
import type { Order } from "../types";
import Panel from "./Panel";

type Props = { title: string; area: string; statuses: Order["status"][]; empty: string; compact?: boolean };

export default function OrdersPanel({ title, area, statuses, empty, compact }: Props) {
  const orders = (useLive<Order[]>("/api/orders", ["orders", "order_lines"]) ?? []).filter((order) =>
    statuses.includes(order.status),
  );

  return (
    <Panel title={`${title} (${orders.length})`} area={area}>
      {orders.length === 0 && <p className="muted">{empty}</p>}
      {compact ? (
        <ul className="order-list">
          {orders.map((order) => (
            <li key={order.id}>
              <span className="mono">#{order.id}</span>
              <span className="progress"><i style={{ width: `${order.units ? (100 * order.picked_units) / order.units : 0}%` }} /></span>
              <span className={`badge ${order.status}`}>{order.status.replace("_", " ")}</span>
            </li>
          ))}
        </ul>
      ) : (
      <table>
        <thead>
          <tr>
            <th>Order</th>
            <th>Customer</th>
            <th>Priority</th>
            <th>Picked</th>
            <th>Dispatch</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((order) => (
            <tr key={order.id}>
              <td className="mono">#{order.id}</td>
              <td>{order.customer}</td>
              <td>P{order.priority}</td>
              <td className="mono">
                {order.picked_units}/{order.units}
              </td>
              <td className="mono">{order.dispatch_location}</td>
              <td>
                <span className={`badge ${order.status}`}>{order.status.replace("_", " ")}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      )}
    </Panel>
  );
}
