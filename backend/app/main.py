import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .agent.loop import notify_task_finished
from .config import settings
from .db import pool
from .events import hub
from .executor import TaskExecutor
from .robot.mock import MockRobot
from .routers import agent, exceptions, inventory, locations, orders, robots, tasks


@asynccontextmanager
async def lifespan(app: FastAPI):
    await pool.open()
    workers = [asyncio.create_task(hub.listen())]
    for robot_id in settings.robot_ids:
        executor = TaskExecutor(MockRobot(robot_id), on_task_finished=notify_task_finished)
        workers.append(asyncio.create_task(executor.run_forever()))
    yield
    for worker in workers:
        worker.cancel()
    await pool.close()


app = FastAPI(title="Warehouse Operations API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])

for router in (agent.router, locations.router, orders.router, inventory.router, robots.router, tasks.router, exceptions.router):
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
