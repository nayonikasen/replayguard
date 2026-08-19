"""Agent-era and activity-boundary rules (RG3xx).

LLM calls are the new I/O: expensive, slow, non-deterministic, and increasingly
written by people who haven't been paged for a workflow that replayed one.
"""

from __future__ import annotations

import ast

from replayguard.findings import Severity
from replayguard.rules.base import ACTIVITY, WORKFLOW, CallMatchRule, Rule
from replayguard.rules.base import resolved_calls as base_resolved_calls


def _is_readonly_query(node: ast.Call) -> bool:
    """A cursor.execute whose statement is a literal SELECT is a read, not a write."""
    if not node.args:
        return False
    first = node.args[0]
    return (
        isinstance(first, ast.Constant)
        and isinstance(first.value, str)
        and first.value.lstrip().upper().startswith("SELECT")
    )


class ModelCallInWorkflowRule(CallMatchRule):
    id = "RG301"
    title = "Model API call in workflow code"
    severity = Severity.ERROR
    why = (
        "A model call in workflow code is unrecorded I/O: the sandbox may block "
        "it outright, and if it runs, replay re-issues it — paying again for a "
        "different answer. Wrap it in an activity so the result is recorded "
        "once and retried deliberately."
    )
    contexts = frozenset({WORKFLOW})
    prefixes = ("anthropic.", "openai.", "google.genai.", "vertexai.")
    # Multi-segment suffixes only: a bare ".generate_content" would flag any
    # local helper that happens to share the name.
    suffixes = (
        ".messages.create",
        ".messages.stream",
        ".messages.parse",
        ".chat.completions.create",
        ".responses.create",
        ".models.generate_content",
        ".embeddings.create",
    )

    def message_for(self, dotted: str) -> str:
        return f"`{dotted}()` looks like a model API call inside workflow code; move it to an activity"


class UnverifiedSideEffectRule(CallMatchRule):
    id = "RG302"
    title = "External write in activity without visible idempotency"
    severity = Severity.INFO
    why = (
        "Activities retry from the top: a crash after the write but before "
        "completion records nothing, so the retry writes again. Every external "
        "write needs an idempotency key, an upsert, or a dedup check — "
        "`replayguard review` runs a semantic audit of each one."
    )
    contexts = frozenset({ACTIVITY})
    suffixes = (
        ".post",
        ".put",
        ".patch",
        ".delete",
        ".send",
        ".publish",
        ".insert_one",
        ".execute",
    )

    def check(self, func, ctx):
        for node, dotted in base_resolved_calls(func, ctx.imports):
            if not dotted.endswith(self.suffixes):
                continue
            if dotted.endswith(".execute") and _is_readonly_query(node):
                continue
            yield self.finding(node, ctx, self.message_for(dotted))

    def message_for(self, dotted: str) -> str:
        return (
            f"`{dotted}()` looks like an external write in an activity; verify "
            "it is idempotent across retries (or run `replayguard review`)"
        )


RULES: tuple[Rule, ...] = (
    ModelCallInWorkflowRule(),
    UnverifiedSideEffectRule(),
)
