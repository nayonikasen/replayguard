"""Determinism rules (RG1xx): workflow code must produce identical results on replay.

Temporal recovers a crashed workflow by re-executing its code against the
recorded event history. Anything that can return a different value the second
time — wall clocks, RNGs, I/O, environment, thread scheduling, hash ordering —
diverges from history and kills the workflow with a nondeterminism error, or
worse, silently corrupts state.
"""

from __future__ import annotations

import ast
from typing import Iterator

from replayguard.findings import Finding, Severity
from replayguard.rules.base import (
    WORKFLOW,
    CallMatchRule,
    FunctionContext,
    FunctionNode,
    Rule,
    resolved_calls,
)


class WallClockRule(CallMatchRule):
    id = "RG101"
    title = "Wall-clock time in workflow code"
    severity = Severity.ERROR
    why = (
        "Replay re-executes workflow code; a wall-clock read returns a different "
        "value the second time and the run diverges from history."
    )
    contexts = frozenset({WORKFLOW})
    exact = frozenset(
        {
            "time.time",
            "time.time_ns",
            "time.monotonic",
            "time.monotonic_ns",
            "time.perf_counter",
            "time.perf_counter_ns",
            "datetime.datetime.now",
            "datetime.datetime.utcnow",
            "datetime.datetime.today",
            "datetime.date.today",
        }
    )

    def message_for(self, dotted: str) -> str:
        if "monotonic" in dotted or "perf_counter" in dotted:
            return (
                f"`{dotted}()` reads a process clock that restarts with the "
                "worker; use `workflow.now()` instead"
            )
        return f"`{dotted}()` reads the wall clock; use `workflow.now()` instead"


class RandomnessRule(CallMatchRule):
    id = "RG102"
    title = "Non-deterministic randomness in workflow code"
    severity = Severity.ERROR
    why = (
        "An unseeded RNG rolls different values on replay. Temporal provides "
        "deterministic equivalents seeded from the run itself."
    )
    contexts = frozenset({WORKFLOW})
    exact = frozenset({"uuid.uuid1", "uuid.uuid4", "os.urandom"})
    prefixes = ("random.", "secrets.")

    def message_for(self, dotted: str) -> str:
        return (
            f"`{dotted}()` is non-deterministic; use `workflow.random()` or "
            "`workflow.uuid4()` instead"
        )


class DirectIORule(CallMatchRule):
    id = "RG103"
    title = "Direct I/O in workflow code"
    severity = Severity.ERROR
    why = (
        "Network and file I/O are not recorded in workflow history: results "
        "differ on replay and failures aren't retried by the engine. Activities "
        "exist precisely to record and retry these."
    )
    contexts = frozenset({WORKFLOW})
    exact = frozenset({"open"})
    # urllib.parse is pure string manipulation — only the I/O submodules count.
    prefixes = (
        "requests.",
        "httpx.",
        "urllib.request.",
        "urllib.error.",
        "socket.",
        "aiohttp.",
        "http.client.",
        "smtplib.",
        "boto3.",
        "psycopg2.",
        "pymongo.",
        "redis.",
    )

    def message_for(self, dotted: str) -> str:
        return f"`{dotted}()` performs I/O inside workflow code; move it to an activity"


class EnvironmentRule(Rule):
    id = "RG104"
    title = "Environment access in workflow code"
    severity = Severity.WARNING
    why = (
        "Environment variables differ across workers and deployments, so the "
        "same replay can see different config. Pass config through workflow "
        "arguments or memo instead."
    )
    contexts = frozenset({WORKFLOW})

    def check(self, func: FunctionNode, ctx: FunctionContext) -> Iterator[Finding]:
        for node in ast.walk(func):
            if isinstance(node, (ast.Attribute, ast.Name)):
                if ctx.imports.resolve(node) == "os.environ":
                    yield self.finding(
                        node, ctx, "`os.environ` read in workflow code; pass config via workflow arguments"
                    )
            elif isinstance(node, ast.Call):
                if ctx.imports.resolve(node.func) == "os.getenv":
                    yield self.finding(
                        node, ctx, "`os.getenv()` in workflow code; pass config via workflow arguments"
                    )


class NonDurableSleepRule(CallMatchRule):
    id = "RG105"
    title = "Blocking sleep in workflow code"
    severity = Severity.ERROR
    why = (
        "`time.sleep` blocks the worker thread and is not a durable timer. "
        "In Temporal's event loop, `asyncio.sleep()` and `workflow.sleep()` "
        "ARE durable server-side timers — use one of those."
    )
    contexts = frozenset({WORKFLOW})
    exact = frozenset({"time.sleep"})

    def message_for(self, dotted: str) -> str:
        return (
            f"`{dotted}()` blocks the worker and is not a durable timer; "
            "use `asyncio.sleep()` or `workflow.sleep()`"
        )


class ConcurrencyRule(CallMatchRule):
    id = "RG106"
    title = "Threads or subprocesses in workflow code"
    severity = Severity.ERROR
    why = (
        "Thread scheduling and child processes are invisible to the event "
        "history, so their interleavings and results cannot be replayed."
    )
    contexts = frozenset({WORKFLOW})
    prefixes = (
        "threading.",
        "multiprocessing.",
        "subprocess.",
        "concurrent.futures.",
    )
    suffixes = (".run_in_executor",)

    def message_for(self, dotted: str) -> str:
        return (
            f"`{dotted}()` escapes the workflow's deterministic event loop; "
            "move the work to an activity"
        )


class SetIterationRule(Rule):
    id = "RG107"
    title = "Iteration over a set in workflow code"
    severity = Severity.WARNING
    why = (
        "str hashes are randomized per process (PYTHONHASHSEED), so set "
        "iteration order can differ between the original run and a replay on "
        "a restarted worker — a nondeterminism bug that only fires in "
        "production."
    )
    contexts = frozenset({WORKFLOW})

    def check(self, func: FunctionNode, ctx: FunctionContext) -> Iterator[Finding]:
        for node in ast.walk(func):
            iters: list[ast.expr] = []
            if isinstance(node, (ast.For, ast.AsyncFor)):
                iters.append(node.iter)
            elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                iters.extend(gen.iter for gen in node.generators)
            for it in iters:
                if isinstance(it, ast.Set) or (
                    isinstance(it, ast.Call)
                    and ctx.imports.resolve(it.func) in {"set", "frozenset"}
                ):
                    yield self.finding(
                        it,
                        ctx,
                        "iteration order over a set is not stable across worker "
                        "processes; iterate `sorted(...)` instead",
                    )


class GlobalStateRule(Rule):
    id = "RG108"
    title = "Module-global rebinding in workflow code"
    severity = Severity.WARNING
    why = (
        "Module globals are shared across every workflow run on the worker and "
        "reset on restart; replay cannot reconstruct them, so they diverge "
        "from what re-execution computes."
    )
    contexts = frozenset({WORKFLOW})

    def check(self, func: FunctionNode, ctx: FunctionContext) -> Iterator[Finding]:
        for node in ast.walk(func):
            if isinstance(node, ast.Global):
                names = ", ".join(node.names)
                yield self.finding(
                    node,
                    ctx,
                    f"`global {names}` in workflow code; keep state on `self` "
                    "so replay rebuilds it deterministically from event history",
                )


RULES: tuple[Rule, ...] = (
    WallClockRule(),
    RandomnessRule(),
    DirectIORule(),
    EnvironmentRule(),
    NonDurableSleepRule(),
    ConcurrencyRule(),
    SetIterationRule(),
    GlobalStateRule(),
)
