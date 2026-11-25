import { useEffect, useState } from "react";
import ChatPanel from "./components/ChatPanel";
import ExceptionsPanel from "./components/ExceptionsPanel";
import Icon, { type IconName } from "./components/Icon";
import InventoryPanel from "./components/InventoryPanel";
import KpiBar from "./components/KpiBar";
import OrdersPanel from "./components/OrdersPanel";
import RobotPanel from "./components/RobotPanel";
import SimPanel from "./components/SimPanel";
import SimPreview from "./components/SimPreview";

const PAGES: { id: string; label: string; icon: IconName }[] = [
  { id: "agent", label: "Agent", icon: "chat" },
  { id: "sim", label: "Live", icon: "live" },
  { id: "operations", label: "Operations", icon: "ops" },
];

function currentPage() {
  const id = window.location.hash.replace(/^#\/?/, "");
  return PAGES.some((page) => page.id === id) ? id : "agent";
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

export default function App() {
  const page = usePage();

  return (
    <div className="app">
      <nav className="rail">
        <div className="logo">
          <Icon name="cube" size={20} />
        </div>
        {PAGES.map(({ id, label, icon }) => (
          <a key={id} href={`#/${id}`} className={page === id ? "active" : ""} title={label}>
            <Icon name={icon} />
            <span>{label}</span>
          </a>
        ))}
      </nav>
      {page === "agent" && (
        <main className="page page-agent">
          <ChatPanel />
          <aside className="side">
            <SimPreview />
            <RobotPanel />
          </aside>
        </main>
      )}
      {page === "sim" && (
        <main className="page page-sim">
          <SimPanel />
        </main>
      )}
      {page === "operations" && (
        <main className="page page-operations">
          <header className="page-head">
            <h1>Operations</h1>
          </header>
          <KpiBar />
          <div className="ops-grid">
            <OrdersPanel title="Awaiting fulfillment" area="orders" statuses={["pending", "in_progress"]} empty="No orders waiting." />
            <RobotPanel />
            <OrdersPanel title="Completed" area="completed" statuses={["completed", "failed", "cancelled"]} empty="No completed orders yet." />
            <ExceptionsPanel />
            <InventoryPanel />
          </div>
        </main>
      )}
    </div>
  );
}
