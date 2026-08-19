"""Fixture: a workflow that commits every sin ReplayGuard knows about."""

import os
import random
import subprocess
import threading
import time
from datetime import datetime, timedelta

import requests
from temporalio import workflow
from temporalio.common import RetryPolicy

_cache = None


@workflow.defn
class OrderWorkflow:
    @workflow.run
    async def run(self, order_id: str) -> str:
        started = datetime.now()  # RG101
        stamp = time.time()  # RG101
        token = random.randint(0, 10)  # RG102
        attempt = os.getenv("ATTEMPT")  # RG104
        region = os.environ["REGION"]  # RG104
        requests.post("https://api.example.com/orders", json={"id": order_id})  # RG103
        with open("/tmp/state.json") as f:  # RG103
            f.read()
        time.sleep(5)  # RG105
        threading.Thread(target=print).start()  # RG106
        subprocess.run(["ls"])  # RG106
        for shard in {"us", "eu", "ap"}:  # RG107
            pass
        global _cache  # RG108
        _cache = started

        await workflow.execute_activity(  # RG201
            "charge_card",
            order_id,
        )
        await workflow.execute_activity(
            "send_email",
            order_id,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(initial_interval=timedelta(seconds=1)),  # RG202
        )
        try:
            await workflow.execute_activity(
                "release_hold",
                order_id,
                start_to_close_timeout=timedelta(seconds=30),
            )
        except BaseException:  # RG203
            pass
        summary = self.client.messages.create(  # RG301
            model="claude-opus-5",
            max_tokens=100,
            messages=[{"role": "user", "content": "summarize"}],
        )
        return f"{stamp}-{token}-{attempt}-{region}-{summary}"
