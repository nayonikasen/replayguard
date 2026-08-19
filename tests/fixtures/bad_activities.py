"""Fixture: an activity with an unverified external write."""

import requests
from temporalio import activity


@activity.defn
async def send_receipt(order_id: str) -> None:
    requests.post(  # RG302
        "https://mailer.internal/send",
        json={"order": order_id},
    )
