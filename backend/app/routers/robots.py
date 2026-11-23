from fastapi import APIRouter

from ..db import fetch_all

router = APIRouter(prefix="/robots", tags=["robots"])


@router.get("")
async def list_robots():
    return await fetch_all("SELECT * FROM robots ORDER BY id")
