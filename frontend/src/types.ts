export type Order = {
  id: number;
  customer: string;
  status: "pending" | "in_progress" | "completed" | "failed" | "cancelled";
  priority: number;
  dispatch_location: string;
  line_count: number;
  units: number;
  picked_units: number;
  completed_at: string | null;
};

export type InventoryLevel = {
  sku: string;
  name: string;
  category: string;
  on_hand: number;
  reserved: number;
  available: number;
  locations: number;
};

export type Robot = {
  id: string;
  name: string;
  model: string;
  status: string;
  x: string;
  y: string;
  battery_pct: string;
  holding_sku: string | null;
  current_task_id: number | null;
};

export type WarehouseException = {
  id: number;
  order_id: number | null;
  type: string;
  severity: string;
  message: string;
  status: string;
  created_at: string;
};
