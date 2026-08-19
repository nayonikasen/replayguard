from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from replayguard.findings import Finding, Severity
from replayguard.imports import ImportTable
from replayguard.rules import ACTIVITY, ALL_RULES, MODULE, WORKFLOW, FunctionContext, Rule
from replayguard.rules.base import FunctionNode

_SKIP_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "site-packages",
    "venv",
}

_WORKFLOW_DEFN = "temporalio.workflow.defn"
_ACTIVITY_DEFN = "temporalio.activity.defn"

# Line-level escape hatch: "# replayguard: ignore" or "# replayguard: ignore[RG101,RG202]"
_SUPPRESS_RE = re.compile(r"replayguard:\s*ignore(?:\[([A-Z0-9,\s]+)\])?")

PARSE_ERROR_RULE_ID = "RG000"


@dataclass(frozen=True)
class AnalyzedFunction:
    node: FunctionNode
    ctx: FunctionContext


def _decorator_names(
    node: FunctionNode | ast.ClassDef, imports: ImportTable
) -> Iterator[str]:
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        dotted = imports.resolve(target)
        if dotted:
            yield dotted


def _prune_nested(func: FunctionNode, targets: set[FunctionNode]) -> None:
    """Replace separately-classified nested defs with `pass` in this subtree.

    Without this, an @activity.defn nested inside a workflow method would be
    checked twice — once by the activity rules on its own node and once by the
    workflow rules walking the enclosing method.
    """
    for child in ast.walk(func):
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(child, field, None)
            if isinstance(stmts, list):
                for i, stmt in enumerate(stmts):
                    if stmt in targets:
                        stmts[i] = ast.copy_location(ast.Pass(), stmt)


def classify_functions(
    tree: ast.Module, imports: ImportTable, path: str
) -> list[AnalyzedFunction]:
    """Find every function that runs in a Temporal workflow or activity context.

    Workflow context covers *all* methods of an @workflow.defn class: signal
    and update handlers are recorded in history and replay like run does, and
    query handlers execute in workflow context too (they are never replayed,
    but must still be deterministic and side-effect free).
    """
    functions: list[AnalyzedFunction] = []
    activity_nodes: set[FunctionNode] = set()

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _ACTIVITY_DEFN in _decorator_names(node, imports):
                activity_nodes.add(node)
                functions.append(
                    AnalyzedFunction(
                        node,
                        FunctionContext(
                            path=path, kind=ACTIVITY, imports=imports, owner=node.name
                        ),
                    )
                )

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if _WORKFLOW_DEFN not in _decorator_names(node, imports):
            continue
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if item in activity_nodes:
                    continue
                _prune_nested(item, activity_nodes)
                functions.append(
                    AnalyzedFunction(
                        item,
                        FunctionContext(
                            path=path,
                            kind=WORKFLOW,
                            imports=imports,
                            owner=f"{node.name}.{item.name}",
                        ),
                    )
                )
    return functions


def _suppressed(finding: Finding, lines: list[str]) -> bool:
    if not 0 < finding.line <= len(lines):
        return False
    match = _SUPPRESS_RE.search(lines[finding.line - 1])
    if not match:
        return False
    if match.group(1) is None:
        return True
    ids = {part.strip() for part in match.group(1).split(",")}
    return finding.rule_id in ids


def analyze_source(
    source: str,
    path: str,
    rules: Sequence[Rule] = ALL_RULES,
    select: set[str] | None = None,
    ignore: set[str] | None = None,
) -> list[Finding]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [
            Finding(
                rule_id=PARSE_ERROR_RULE_ID,
                severity=Severity.ERROR,
                message=f"could not parse file: {exc.msg}",
                path=path,
                line=exc.lineno or 0,
                col=(exc.offset or 1) - 1,
                why="ReplayGuard analyzes the AST; unparseable files are unverifiable.",
            )
        ]

    imports = ImportTable.from_module(tree)

    def active(rule: Rule) -> bool:
        if select and rule.id not in select:
            return False
        return not (ignore and rule.id in ignore)

    findings: list[Finding] = []
    module_ctx = FunctionContext(path=path, kind=MODULE, imports=imports, owner="<module>")
    for rule in rules:
        if MODULE in rule.contexts and active(rule):
            findings.extend(rule.check(tree, module_ctx))

    for fn in classify_functions(tree, imports, path):
        for rule in rules:
            if fn.ctx.kind in rule.contexts and active(rule):
                findings.extend(rule.check(fn.node, fn.ctx))

    lines = source.splitlines()
    findings = [f for f in findings if not _suppressed(f, lines)]
    findings.sort(key=lambda f: (f.path, f.line, f.col, f.rule_id))
    return findings


def analyze_path(
    path: str | Path,
    rules: Sequence[Rule] = ALL_RULES,
    select: set[str] | None = None,
    ignore: set[str] | None = None,
) -> list[Finding]:
    p = Path(path)
    return analyze_source(
        p.read_text(encoding="utf-8"), str(p), rules, select, ignore
    )


def analyze_paths(
    paths: Iterable[str | Path],
    rules: Sequence[Rule] = ALL_RULES,
    select: set[str] | None = None,
    ignore: set[str] | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    for file in iter_python_files(paths):
        findings.extend(analyze_path(file, rules, select, ignore))
    return findings


def iter_python_files(paths: Iterable[str | Path]) -> Iterator[Path]:
    """Yield .py files, raising on paths that don't exist.

    Silence here would be dangerous: a typo'd path in CI would "pass" forever.
    """
    seen: set[Path] = set()

    def emit(f: Path) -> Iterator[Path]:
        resolved = f.resolve()
        if resolved not in seen:
            seen.add(resolved)
            yield f

    for raw in paths:
        p = Path(raw)
        if p.is_file():
            if p.suffix == ".py":
                yield from emit(p)
        elif p.is_dir():
            for f in sorted(p.rglob("*.py")):
                # Skip-dirs apply only below the requested root, so scanning a
                # project that happens to live under a dir named "build" works.
                if _SKIP_DIRS.isdisjoint(f.relative_to(p).parts):
                    yield from emit(f)
        else:
            raise FileNotFoundError(f"no such file or directory: {p}")
