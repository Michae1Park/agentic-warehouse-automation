from fastapi import APIRouter
from pydantic import BaseModel

from ..agent.loop import run_agent
from ..db import fetch_all

router = APIRouter(prefix="/agent", tags=["agent"])


class ChatRequest(BaseModel):
    content: str


@router.post("/conversations/{conversation_id}/messages")
async def send_message(conversation_id: str, request: ChatRequest):
    return await run_agent(conversation_id, request.content)


@router.get("/conversations/{conversation_id}/messages")
async def get_messages(conversation_id: str):
    rows = await fetch_all(
        "SELECT id, message, created_at FROM agent_messages WHERE conversation_id = %s ORDER BY id", conversation_id
    )
    return [{"id": r["id"], "created_at": r["created_at"], **r["message"]} for r in rows]


@router.get("/runs")
async def list_runs(limit: int = 20):
    return await fetch_all("SELECT * FROM agent_runs ORDER BY id DESC LIMIT %s", limit)


@router.get("/conversations/{conversation_id}/harness")
async def harness_stats(conversation_id: str):
    events = await fetch_all(
        "SELECT kind, count(*)::INT AS n FROM agent_harness_events WHERE conversation_id = %s GROUP BY kind",
        conversation_id,
    )
    plans = await fetch_all("SELECT id FROM agent_plans WHERE conversation_id = %s", conversation_id)
    return {"plans": len(plans), **{e["kind"]: e["n"] for e in events}}
