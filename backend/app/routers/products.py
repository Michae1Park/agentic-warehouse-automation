from fastapi import APIRouter

from ..db import fetch_all

router = APIRouter(prefix="/products", tags=["products"])


@router.get("")
async def list_products():
    return await fetch_all("SELECT * FROM products ORDER BY sku")
