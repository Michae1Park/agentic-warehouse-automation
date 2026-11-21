import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import pool
from .events import hub
from .routers import exceptions, inventory, orders, robots, tasks


@asynccontextmanager
async def lifespan(app: FastAPI):
    await pool.open()
    listener = asyncio.create_task(hub.listen())
    yield
    listener.cancel()
    await pool.close()


app = FastAPI(title="Warehouse Operations API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])

for router in (orders.router, inventory.router, robots.router, tasks.router, exceptions.router):
    app.include_router(router, prefix="/api")


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.websocket("/ws/events")
async def events_socket(ws: WebSocket):
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(ws)
