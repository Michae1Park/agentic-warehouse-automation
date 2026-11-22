import { useEffect, useState } from "react";
import ChatPanel from "./components/ChatPanel";
import ExceptionsPanel from "./components/ExceptionsPanel";
import InventoryPanel from "./components/InventoryPanel";
import KpiBar from "./components/KpiBar";
import OrdersPanel from "./components/OrdersPanel";
import RobotPanel from "./components/RobotPanel";
import SimPanel from "./components/SimPanel";
import SimPreview from "./components/SimPreview";

const PAGES = [
  { id: "operations", label: "Operations" },
  { id: "agent", label: "Agent" },
  { id: "sim", label: "Live Simulation" },
] as const;

type PageId = (typeof PAGES)[number]["id"];

function currentPage(): PageId {
  const id = window.location.hash.replace(/^#\/?/, "");
  return PAGES.some((page) => page.id === id) ? (id as PageId) : "operations";
}

function usePage() {
  const [page, setPage] = useState(currentPage);
  useEffect(() => {
    const onChange = () => setPage(currentPage());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return page;
}

const awaiting = (
  <OrdersPanel
    title="Awaiting fulfillment"
    area="orders"
    statuses={["pending", "in_progress"]}
    empty="No orders waiting."
  />
);

export default function App() {
  const page = usePage();

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <h1>Warehouse Operations</h1>
          <nav className="nav">
            {PAGES.map(({ id, label }) => (
              <a key={id} href={`#/${id}`} className={page === id ? "active" : ""}>
                {label}
              </a>
            ))}
          </nav>
        </div>
        <KpiBar />
      </header>
      {page === "operations" && (
        <main className="grid page-operations">
          {awaiting}
          <OrdersPanel
            title="Completed"
            area="completed"
            statuses={["completed", "failed", "cancelled"]}
            empty="No completed orders yet."
          />
          <RobotPanel />
          <InventoryPanel />
          <ExceptionsPanel />
        </main>
      )}
      {page === "agent" && (
        <main className="grid page-agent">
          <ChatPanel />
          <SimPreview />
          <RobotPanel />
        </main>
      )}
      {page === "sim" && (
        <main className="grid page-sim">
          <SimPanel />
          <RobotPanel />
          {awaiting}
        </main>
      )}
    </div>
  );
}
