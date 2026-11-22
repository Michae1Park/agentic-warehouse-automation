import base64
import json
import re
import time

from openai import AsyncOpenAI

from .config import settings
from .db import fetch_all

client = AsyncOpenAI(base_url=settings.llm_base_url, api_key="unused")

PROMPT = """You are the vision system of a warehouse picking robot. The image shows shelf bin {location_id}: \
the shelf section in the center of the image, between the two vertical rack posts.

Product catalog (each product is a box identified by its color: either the whole box is that color, \
or it is a cardboard box with a label of that color on its front):
{catalog}

The warehouse management system expects this bin to contain: {expected}.
The record may be wrong. Report only what you actually see in this bin, ignoring anything outside it. An empty bin shows only the bare shelf board.

Count the boxes of each product in the bin. Reply with JSON only, mapping SKU to box count, \
e.g. {{"SKU-1001": 3}}. Reply {{}} if the bin is empty."""


async def _catalog() -> str:
    rows = await fetch_all("SELECT sku, name, color FROM products ORDER BY sku")
    return "\n".join(f"- {r['color']}: {r['sku']} ({r['name']})" for r in rows)


async def _expected(location_id: str) -> str:
    rows = await fetch_all(
        "SELECT i.sku, p.color, i.quantity FROM inventory i JOIN products p ON p.sku = i.sku "
        "WHERE i.location_id = %s AND i.quantity > 0",
        location_id,
    )
    return ", ".join(f"{r['quantity']} x {r['sku']} ({r['color']})" for r in rows) or "nothing (empty bin)"


def _parse(text: str) -> dict[str, int]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return {str(k): int(v) for k, v in data.items() if str(k).startswith("SKU-") and int(v) > 0}


TOTE_PROMPT = """You are the quality-check camera of an outbound conveyor. The image looks down into a plastic tote \
that a robot has just filled for a customer order.

Product catalog (each product is a box identified by its color: either the whole box is that color, \
or it is a cardboard box with a label of that color):
{catalog}

The order says the tote should contain: {expected}.
The tote itself is dark grey plastic; it is not a product. The order may have been packed wrong. \
Report only the boxes you actually see inside the tote.

Reply with JSON only, mapping SKU to box count, e.g. {{"SKU-1001": 2}}. Reply {{}} if the tote is empty."""


async def identify_products(image_jpeg: bytes, location_id: str) -> dict:
    prompt = PROMPT.format(location_id=location_id, catalog=await _catalog(), expected=await _expected(location_id))
    return await _ask(image_jpeg, prompt)


async def verify_tote(image_jpeg: bytes, expected: dict[str, int]) -> dict:
    colors = {r["sku"]: r["color"] for r in await fetch_all("SELECT sku, color FROM products")}
    listed = ", ".join(f"{qty} x {sku} ({colors.get(sku, '?')})" for sku, qty in sorted(expected.items()))
    return await _ask(image_jpeg, TOTE_PROMPT.format(catalog=await _catalog(), expected=listed))


async def _ask(image_jpeg: bytes, prompt: str) -> dict:
    started = time.perf_counter()
    response = await client.chat.completions.create(
        model=settings.llm_model,
        messages=[{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(image_jpeg).decode()}},
            {"type": "text", "text": prompt},
        ]}],
        temperature=0.0,
        max_tokens=200,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    text = response.choices[0].message.content or ""
    return {"counts": _parse(text), "raw": text, "latency_ms": int((time.perf_counter() - started) * 1000)}
