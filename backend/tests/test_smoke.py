import os
import time

import httpx
import pytest

API = os.environ.get("API_URL", "http://localhost:8000")


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=API, timeout=30) as client:
        for _ in range(60):
            try:
                if client.get("/api/health").status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(1)
        assert client.post("/api/sim/reset", json={"seed": 1, "time_scale": 50}).status_code == 200
        yield client


def wait_for_task(client: httpx.Client, task_id: int, timeout: float = 120) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = client.get(f"/api/tasks/{task_id}").json()
        if task["status"] not in ("queued", "running"):
            return task
        time.sleep(1)
    raise AssertionError(f"task {task_id} did not finish")


def test_catalog_flags_pickable_products(client):
    products = {p["sku"]: p for p in client.get("/api/products").json()}
    assert products["SKU-1001"]["robot_pickable"]
    assert not products["SKU-2001"]["robot_pickable"]


def test_order_1042_is_fulfilled_by_the_mock_robot(client):
    response = client.post("/api/orders/1042/fulfill")
    assert response.status_code == 200, response.text
    task = wait_for_task(client, response.json()["task_id"])
    assert task["status"] == "succeeded", task
    order = client.get("/api/orders/1042").json()
    assert all(line["picked_qty"] == line["quantity"] for line in order["lines"])


def test_short_order_is_refused(client):
    response = client.post("/api/orders/1044/fulfill")
    assert response.status_code == 409
    exceptions = client.get("/api/exceptions").json()
    assert any(e["type"] == "planning_failed" and e["order_id"] == 1044 for e in exceptions)
