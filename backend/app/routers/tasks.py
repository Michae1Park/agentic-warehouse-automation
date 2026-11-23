from fastapi import APIRouter, HTTPException

from ..db import fetch_all, fetch_one

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("")
async def list_tasks(limit: int = 20):
    return await fetch_all("SELECT * FROM tasks ORDER BY id DESC LIMIT %s", limit)


@router.get("/{task_id}")
async def get_task(task_id: int):
    task = await fetch_one("SELECT * FROM tasks WHERE id = %s", task_id)
    if not task:
        raise HTTPException(404, f"task {task_id} not found")
    task["steps"] = await fetch_all("SELECT * FROM task_steps WHERE task_id = %s ORDER BY seq", task_id)
    return task
