import { useLive } from "../api";
import type { Order } from "../types";
import Panel from "./Panel";

export default function OrdersPanel() {
  const orders = useLive<Order[]>("/api/orders", ["orders", "order_lines"]) ?? [];

  return (
    <Panel title="Orders" area="orders">
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
    </Panel>
  );
}
