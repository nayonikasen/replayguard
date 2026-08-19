"""Fixture: a read-only activity — nothing to flag."""

import requests
from temporalio import activity


@activity.defn
async def fetch_order_status(order_id: str) -> str:
    response = requests.get(f"https://payments.internal/orders/{order_id}")
    response.raise_for_status()
    return response.json()["status"]
