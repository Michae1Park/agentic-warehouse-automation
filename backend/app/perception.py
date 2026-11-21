import base64
import json
import re
import time

from openai import AsyncOpenAI

from .config import settings
from .db import fetch_all

client = AsyncOpenAI(base_url=settings.llm_base_url, api_key="unused")

PROMPT = """You are the vision system of a warehouse picking robot. The image shows one shelf bin.
Each product is a box with a distinct solid color:
{catalog}
Identify which products are present in the bin and count the boxes of each.
Reply with JSON only, mapping SKU to box count, e.g. {{"SKU-1001": 3}}. Use {{}} if the bin is empty."""


async def _catalog() -> str:
    rows = await fetch_all("SELECT sku, name, color FROM products ORDER BY sku")
    return "\n".join(f"- {r['color']} box = {r['sku']} ({r['name']})" for r in rows)


def _parse(text: str) -> dict[str, int]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return {str(k): int(v) for k, v in data.items() if str(k).startswith("SKU-") and int(v) > 0}


async def identify_products(image_jpeg: bytes) -> dict:
    started = time.perf_counter()
    image_url = "data:image/jpeg;base64," + base64.b64encode(image_jpeg).decode()
    response = await client.chat.completions.create(
        model=settings.llm_model,
        messages=[{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": image_url}},
            {"type": "text", "text": PROMPT.format(catalog=await _catalog())},
        ]}],
        temperature=0.0,
        max_tokens=200,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    text = response.choices[0].message.content or ""
    return {"counts": _parse(text), "raw": text, "latency_ms": int((time.perf_counter() - started) * 1000)}
