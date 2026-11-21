import ChatPanel from "./components/ChatPanel";
import ExceptionsPanel from "./components/ExceptionsPanel";
import InventoryPanel from "./components/InventoryPanel";
import KpiBar from "./components/KpiBar";
import OrdersPanel from "./components/OrdersPanel";
import RobotPanel from "./components/RobotPanel";
import SimPanel from "./components/SimPanel";

export default function App() {
  return (
    <div className="app">
      <header className="topbar">
        <h1>Warehouse Operations</h1>
        <KpiBar />
      </header>
      <main className="grid">
        <SimPanel />
        <ChatPanel />
        <OrdersPanel />
        <RobotPanel />
        <InventoryPanel />
        <ExceptionsPanel />
      </main>
    </div>
  );
}
