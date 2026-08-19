"""Replay and retry-semantics rules (RG2xx).

These target the failure modes durable execution does NOT save you from:
activities that retry forever against a paid API, cancellation that gets
swallowed mid-state-change, and calls the SDK will reject only after deploy.
"""

from __future__ import annotations

import ast
from typing import Iterator

from replayguard.findings import Finding, Severity
from replayguard.rules.base import (
    MODULE,
    WORKFLOW,
    FunctionContext,
    FunctionNode,
    Rule,
    resolved_calls,
)

# All temporalio activity-invocation entry points share the same timeout
# validation, including the *_method/*_class variants for class-based
# activities.
_ACTIVITY_CALL_NAMES = frozenset(
    f"temporalio.workflow.{stem}{variant}"
    for stem in (
        "execute_activity",
        "start_activity",
        "execute_local_activity",
        "start_local_activity",
    )
    for variant in ("", "_method", "_class")
)

_TIMEOUT_KWARGS = {"start_to_close_timeout", "schedule_to_close_timeout"}


def _has_star_kwargs(node: ast.Call) -> bool:
    return any(kw.arg is None for kw in node.keywords)


class MissingActivityTimeoutRule(Rule):
    id = "RG201"
    title = "Activity invocation without a timeout"
    severity = Severity.ERROR
    why = (
        "The SDK requires start_to_close_timeout or schedule_to_close_timeout "
        "and raises at runtime without one — catch it in CI, not after deploy. "
        "Without a start-to-close bound, a hung worker also stalls retries."
    )
    contexts = frozenset({WORKFLOW})

    def check(self, func: FunctionNode, ctx: FunctionContext) -> Iterator[Finding]:
        for node, dotted in resolved_calls(func, ctx.imports):
            # Exact-name matching (not suffix) so timeout-injecting wrapper
            # helpers like myapp.utils.execute_activity are not flagged.
            if dotted not in _ACTIVITY_CALL_NAMES:
                continue
            if _has_star_kwargs(node):
                continue  # can't see through **kwargs
            passed = {kw.arg for kw in node.keywords}
            if not passed & _TIMEOUT_KWARGS:
                yield self.finding(
                    node,
                    ctx,
                    f"`{dotted}` without start_to_close_timeout or schedule_to_close_timeout",
                )


class UnboundedRetryRule(Rule):
    id = "RG202"
    title = "Retry policy with no bound"
    severity = Severity.WARNING
    # Module context: retry policies are commonly shared module-level
    # constants, and an unbounded one is dangerous wherever it is defined.
    contexts = frozenset({MODULE})
    why = (
        "A RetryPolicy without maximum_attempts or non_retryable_error_types "
        "is bounded only by schedule_to_close_timeout — and not at all if the "
        "call sets only start_to_close_timeout. Point that at a paid API and "
        "every flaky response silently multiplies your bill."
    )

    def check(self, func: ast.AST, ctx: FunctionContext) -> Iterator[Finding]:
        if not ctx.imports.imports_package("temporalio"):
            return
        for node, dotted in resolved_calls(func, ctx.imports):
            # Dot boundary: application classes like HttpRetryPolicy are not
            # temporalio's RetryPolicy.
            if dotted != "RetryPolicy" and not dotted.endswith(".RetryPolicy"):
                continue
            if _has_star_kwargs(node):
                continue
            kwargs = {kw.arg: kw.value for kw in node.keywords}
            if "non_retryable_error_types" in kwargs:
                continue
            max_attempts = kwargs.get("maximum_attempts")
            if max_attempts is None:
                yield self.finding(
                    node,
                    ctx,
                    "RetryPolicy sets no maximum_attempts and no "
                    "non_retryable_error_types; retries stop only when "
                    "schedule_to_close_timeout says so — never, if only "
                    "start_to_close_timeout is set",
                )
            elif isinstance(max_attempts, ast.Constant) and max_attempts.value == 0:
                yield self.finding(
                    node,
                    ctx,
                    "maximum_attempts=0 means unlimited retries; set an explicit bound",
                )


class SwallowedCancellationRule(Rule):
    id = "RG203"
    title = "Workflow cancellation swallowed"
    severity = Severity.WARNING
    why = (
        "asyncio.CancelledError inherits from BaseException, so a bare "
        "`except:` or `except BaseException:` eats workflow cancellation — the "
        "run keeps executing after the operator asked it to stop, often against "
        "external state that has already moved on."
    )
    contexts = frozenset({WORKFLOW})

    _CANCELLED_TYPES = frozenset(
        {
            "BaseException",
            "CancelledError",
            "asyncio.CancelledError",
            "concurrent.futures.CancelledError",
        }
    )

    def check(self, func: FunctionNode, ctx: FunctionContext) -> Iterator[Finding]:
        for node in ast.walk(func):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if not self._catches_cancellation(node, ctx):
                continue
            if self._reraises(node):
                continue
            yield self.finding(
                node,
                ctx,
                "this handler swallows CancelledError, so workflow cancellation "
                "is ignored; catch Exception instead, or re-raise",
            )

    def _catches_cancellation(
        self, handler: ast.ExceptHandler, ctx: FunctionContext
    ) -> bool:
        if handler.type is None:
            return True
        types = (
            handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
        )
        return any(ctx.imports.resolve(t) in self._CANCELLED_TYPES for t in types)

    @staticmethod
    def _reraises(handler: ast.ExceptHandler) -> bool:
        """True if the handler re-raises the exception it caught.

        A bare ``raise`` inside a *nested* except handler re-raises that inner
        exception, not ours — so the search stops at nested handlers and at
        function boundaries.
        """
        bound = handler.name

        def search(node: ast.AST) -> bool:
            if isinstance(
                node,
                (ast.ExceptHandler, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda),
            ):
                return False
            if isinstance(node, ast.Raise):
                if node.exc is None:
                    return True
                if bound and isinstance(node.exc, ast.Name) and node.exc.id == bound:
                    return True
            return any(search(child) for child in ast.iter_child_nodes(node))

        return any(search(stmt) for stmt in handler.body)


RULES: tuple[Rule, ...] = (
    MissingActivityTimeoutRule(),
    UnboundedRetryRule(),
    SwallowedCancellationRule(),
)
