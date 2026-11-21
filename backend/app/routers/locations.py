from fastapi import APIRouter

from ..db import fetch_all

router = APIRouter(prefix="/locations", tags=["locations"])


@router.get("")
async def list_locations():
    return await fetch_all(
        """
        SELECT l.*, COALESCE(json_agg(json_build_object('sku', i.sku, 'quantity', i.quantity))
               FILTER (WHERE i.id IS NOT NULL), '[]') AS stock
        FROM locations l LEFT JOIN inventory i ON i.location_id = l.id
        GROUP BY l.id ORDER BY l.id
        """
    )
