"""Fixture: ordinary Python — none of this runs in a workflow, so no findings."""

import random
import time


def jitter() -> float:
    time.sleep(0.1)
    return random.random()
