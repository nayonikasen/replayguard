from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Iterator

from replayguard.findings import Finding, Severity
from replayguard.imports import ImportTable

WORKFLOW = "workflow"
ACTIVITY = "activity"
MODULE = "module"  # rules that scan a whole file once, outside any function

FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True)
class FunctionContext:
    path: str
    kind: str  # WORKFLOW or ACTIVITY
    imports: ImportTable
    owner: str  # "ClassName.method" or bare function name


class Rule:
    id: str
    title: str
    severity: Severity
    why: str
    contexts: frozenset[str]

    def check(self, func: FunctionNode, ctx: FunctionContext) -> Iterator[Finding]:
        raise NotImplementedError

    def finding(self, node: ast.AST, ctx: FunctionContext, message: str) -> Finding:
        return Finding(
            rule_id=self.id,
            severity=self.severity,
            message=message,
            path=ctx.path,
            line=getattr(node, "lineno", 0),
            col=getattr(node, "col_offset", 0),
            why=self.why,
        )


def resolved_calls(
    func: FunctionNode, imports: ImportTable
) -> Iterator[tuple[ast.Call, str]]:
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            dotted = imports.resolve(node.func)
            if dotted:
                yield node, dotted


class CallMatchRule(Rule):
    """Flags calls whose resolved dotted name matches exact/prefix/suffix sets.

    Prefixes carry a trailing dot and suffixes a leading dot so that
    "requests." can never match "requestsmock" and ".post" never matches
    "compost".
    """

    exact: frozenset[str] = frozenset()
    prefixes: tuple[str, ...] = ()
    suffixes: tuple[str, ...] = ()

    def message_for(self, dotted: str) -> str:
        raise NotImplementedError

    def check(self, func: FunctionNode, ctx: FunctionContext) -> Iterator[Finding]:
        for node, dotted in resolved_calls(func, ctx.imports):
            if (
                dotted in self.exact
                or (self.prefixes and dotted.startswith(self.prefixes))
                or (self.suffixes and dotted.endswith(self.suffixes))
            ):
                yield self.finding(node, ctx, self.message_for(dotted))
