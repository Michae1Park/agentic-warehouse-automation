import { useEffect, useState } from "react";

export const API_URL = import.meta.env.VITE_API_URL || window.location.origin;

export type WarehouseEvent = { id: number; entity: string; entity_id: string; op: string; created_at: string };

type Listener = (event: WarehouseEvent) => void;
const listeners = new Set<Listener>();

function connectEvents() {
  const ws = new WebSocket(API_URL.replace(/^http/, "ws") + "/ws/events");
  ws.onmessage = (msg) => {
    const event = JSON.parse(msg.data) as WarehouseEvent;
    listeners.forEach((listener) => listener(event));
  };
  ws.onclose = () => setTimeout(connectEvents, 2000);
}
connectEvents();

export function onEvent(listener: Listener) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useLive<T>(path: string, entities: string[]): T | undefined {
  const [data, setData] = useState<T>();

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = () =>
      fetch(API_URL + path)
        .then((res) => res.json())
        .then(setData)
        .catch(() => undefined);
    load();
    const unsubscribe = onEvent((event) => {
      if (!entities.includes(event.entity)) return;
      clearTimeout(timer);
      timer = setTimeout(load, 100);
    });
    return () => {
      clearTimeout(timer);
      unsubscribe();
    };
  }, [path, entities.join()]);

  return data;
}
