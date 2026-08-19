"""LLM review pass: a semantic side-effect audit of each activity.

Static rules can see *that* an activity posts to an API; they cannot see
whether the write is safe to run twice. This pass sends each activity to
Claude and asks the one question retries force on you: if this function is
killed after the side effect and re-run from the top, what happens?
"""

from __future__ import annotations

import ast
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from replayguard.analyzer import classify_functions
from replayguard.findings import Finding, Severity
from replayguard.imports import ImportTable
from replayguard.rules import ACTIVITY

DEFAULT_MODEL = "claude-opus-5"
LLM_RULE_ID = "RG401"
_WHY = (
    "Activities retry from the top; any effect without an idempotency "
    "mechanism can be applied more than once. Flagged by the LLM review pass — "
    "verify against the code, not vibes."
)

_SYSTEM_PROMPT = """You are a senior reviewer auditing Temporal activity functions for retry safety.

An activity can be killed at any line and re-executed from the top; only its final return value is recorded. For EACH external side effect in the code (HTTP writes, DB writes, messages sent, files uploaded, model API calls):
- decide whether a retry could apply it twice,
- report any idempotency mechanism VISIBLE IN THE CODE (idempotency key, upsert, conditional write, dedup check). Do not assume mechanisms you cannot see.

Reads and pure computation are not effects. Be terse and concrete; name the exact call."""

_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "effects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "call": {"type": "string"},
                    "kind": {"type": "string"},
                    "duplicated_on_retry": {"type": "boolean"},
                    "idempotency_mechanism": {"type": ["string", "null"]},
                    "concern": {"type": ["string", "null"]},
                },
                "required": [
                    "call",
                    "kind",
                    "duplicated_on_retry",
                    "idempotency_mechanism",
                    "concern",
                ],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["effects", "summary"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ActivitySource:
    path: str
    name: str
    line: int
    source: str


def collect_activities(path: Path) -> list[ActivitySource]:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []  # the static pass already reports RG000 for this file
    imports = ImportTable.from_module(tree)
    return [
        ActivitySource(
            path=str(path),
            name=fn.ctx.owner,
            line=fn.node.lineno,
            source=ast.get_source_segment(source, fn.node) or "",
        )
        for fn in classify_functions(tree, imports, str(path))
        if fn.ctx.kind == ACTIVITY
    ]


def _client():
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "the review command needs the anthropic SDK: pip install 'replayguard[llm]'"
        ) from exc
    try:
        return anthropic.Anthropic()
    except TypeError as exc:
        # The SDK raises TypeError at construction when it can't resolve any
        # credential source.
        raise SystemExit(
            "no Anthropic credentials: set ANTHROPIC_API_KEY or run `ant auth login`"
        ) from exc


def review_findings(review: dict, activity: ActivitySource) -> list[Finding]:
    """Turn a structured review into findings. Pure — unit-testable offline."""
    findings = []
    for effect in review.get("effects", []):
        if not effect.get("duplicated_on_retry"):
            continue
        if effect.get("idempotency_mechanism"):
            continue
        concern = effect.get("concern") or "no idempotency mechanism visible"
        findings.append(
            Finding(
                rule_id=LLM_RULE_ID,
                severity=Severity.WARNING,
                message=(
                    f"activity `{activity.name}`: `{effect['call']}` "
                    f"({effect.get('kind', 'effect')}) can be applied twice on retry — {concern}"
                ),
                path=activity.path,
                line=activity.line,
                col=0,
                why=_WHY,
            )
        )
    return findings


def review_paths(
    files: Iterable[Path], model: str | None = None
) -> list[Finding]:
    model = model or os.getenv("REPLAYGUARD_MODEL") or DEFAULT_MODEL
    client = _client()
    import anthropic  # safe now — _client() verified the SDK is installed
    findings: list[Finding] = []

    for path in files:
        for activity in collect_activities(path):
            prompt = (
                f"Audit this Temporal activity from `{activity.path}` for retry safety:\n\n"
                f"```python\n{activity.source}\n```"
            )
            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=2000,
                    system=_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                    output_config={
                        "format": {"type": "json_schema", "schema": _REVIEW_SCHEMA}
                    },
                )
            except anthropic.AuthenticationError:
                raise SystemExit(
                    "no Anthropic credentials: set ANTHROPIC_API_KEY or run `ant auth login`"
                )
            if response.stop_reason == "refusal":
                print(
                    f"replayguard: model declined to review {activity.name}; skipping",
                    file=sys.stderr,
                )
                continue
            text = next((b.text for b in response.content if b.type == "text"), "")
            try:
                review = json.loads(text)
            except json.JSONDecodeError:
                print(
                    f"replayguard: unparseable review for {activity.name}; skipping",
                    file=sys.stderr,
                )
                continue
            findings.extend(review_findings(review, activity))
    findings.sort(key=lambda f: (f.path, f.line, f.col))
    return findings
