from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .config import settings

pool = AsyncConnectionPool(settings.database_url, open=False, kwargs={"row_factory": dict_row})


async def fetch_all(query: str, *params) -> list[dict]:
    async with pool.connection() as conn:
        cur = await conn.execute(query, params)
        return await cur.fetchall()


async def fetch_one(query: str, *params) -> dict | None:
    async with pool.connection() as conn:
        cur = await conn.execute(query, params)
        return await cur.fetchone()
