from __future__ import annotations

from replayguard.rules import activities, determinism, replay
from replayguard.rules.base import ACTIVITY, MODULE, WORKFLOW, FunctionContext, Rule

ALL_RULES: tuple[Rule, ...] = determinism.RULES + replay.RULES + activities.RULES

__all__ = ["ACTIVITY", "ALL_RULES", "MODULE", "WORKFLOW", "FunctionContext", "Rule"]
