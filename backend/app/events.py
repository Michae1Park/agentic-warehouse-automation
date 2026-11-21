import asyncio
import json
import logging

import psycopg
from fastapi import WebSocket

from .config import settings

log = logging.getLogger(__name__)


class EventHub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast(self, message: dict) -> None:
        dead = []
        for ws in self.clients:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def listen(self) -> None:
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(settings.database_url, autocommit=True) as conn:
                    await conn.execute("LISTEN warehouse_events")
                    async for notify in conn.notifies():
                        await self.broadcast(json.loads(notify.payload))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("event listener dropped, reconnecting")
                await asyncio.sleep(2)


hub = EventHub()
