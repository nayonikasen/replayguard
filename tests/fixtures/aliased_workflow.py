"""Fixture: violations hidden behind import aliases must still be caught."""

import temporalio.workflow as wf
from time import sleep


@wf.defn
class AliasedWorkflow:
    @wf.run
    async def run(self) -> None:
        sleep(1)  # RG105 through `from time import sleep`
