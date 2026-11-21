from fastapi import APIRouter

from ..db import fetch_all

router = APIRouter(prefix="/exceptions", tags=["exceptions"])


@router.get("")
async def list_exceptions(status: str | None = None):
    where = "WHERE status = %s" if status else ""
    params = (status,) if status else ()
    return await fetch_all(f"SELECT * FROM exceptions {where} ORDER BY created_at DESC", *params)
