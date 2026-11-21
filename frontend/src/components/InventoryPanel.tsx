import { useLive } from "../api";
import type { InventoryLevel } from "../types";
import Panel from "./Panel";

const LOW_STOCK = 3;

export default function InventoryPanel() {
  const levels = useLive<InventoryLevel[]>("/api/inventory", ["inventory"]) ?? [];

  return (
    <Panel title="Inventory" area="inventory">
      <table>
        <thead>
          <tr>
            <th>SKU</th>
            <th>Product</th>
            <th>On hand</th>
            <th>Reserved</th>
            <th>Available</th>
          </tr>
        </thead>
        <tbody>
          {levels.map((item) => (
            <tr key={item.sku} className={item.available <= LOW_STOCK ? "low" : ""}>
              <td className="mono">{item.sku}</td>
              <td>{item.name}</td>
              <td className="mono">{item.on_hand}</td>
              <td className="mono">{item.reserved}</td>
              <td className="mono">{item.available}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}
