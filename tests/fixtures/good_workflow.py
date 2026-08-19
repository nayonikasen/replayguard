"""Fixture: the same workflow written the way replay demands."""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy


@workflow.defn
class HealthyOrderWorkflow:
    @workflow.run
    async def run(self, order_id: str) -> str:
        started = workflow.now()
        attempt_id = workflow.uuid4()
        await workflow.sleep(1)
        # In Temporal's event loop this is a durable server-side timer.
        await asyncio.sleep(1)
        result = await workflow.execute_activity(
            "charge_card",
            args=[order_id, str(attempt_id)],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=1),
                maximum_attempts=5,
            ),
        )
        for shard in sorted({"us", "eu", "ap"}):
            workflow.logger.info("shard %s at %s", shard, started)
        return result
