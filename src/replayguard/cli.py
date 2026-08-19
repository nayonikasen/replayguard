from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from replayguard import __version__
from replayguard.analyzer import analyze_paths, iter_python_files
from replayguard.findings import Finding, Severity
from replayguard.rules import ALL_RULES

_GITHUB_LEVEL = {"error": "error", "warning": "warning", "info": "notice"}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "rules":
        return _cmd_rules()
    if args.command == "check":
        return _cmd_check(args)
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="replayguard",
        description="Lint Temporal Python workflows for determinism violations, "
        "replay hazards, and non-idempotent activities.",
    )
    parser.add_argument("--version", action="version", version=f"replayguard {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("paths", nargs="*", default=["."], help="files or directories (default: .)")
        p.add_argument("--format", choices=["text", "json", "github"], default="text")
        p.add_argument(
            "--fail-on",
            choices=["error", "warning", "info", "never"],
            default="error",
            help="lowest severity that causes exit code 1 (default: error)",
        )
        p.add_argument("--select", default="", help="comma-separated rule ids to run exclusively")
        p.add_argument("--ignore", default="", help="comma-separated rule ids to skip")
        p.add_argument("--explain", action="store_true", help="print the why behind each finding")

    check = sub.add_parser("check", help="run the rules")
    add_common(check)

    sub.add_parser("rules", help="list every rule with its rationale")
    return parser


def _cmd_check(args: argparse.Namespace) -> int:
    select = {r.strip() for r in args.select.split(",") if r.strip()} or None
    ignore = {r.strip() for r in args.ignore.split(",") if r.strip()} or None

    try:
        files = list(iter_python_files(args.paths))
    except FileNotFoundError as exc:
        print(f"replayguard: {exc}", file=sys.stderr)
        return 2
    if not files:
        print("replayguard: no Python files matched", file=sys.stderr)

    findings = analyze_paths(files, select=select, ignore=ignore)

    _emit(findings, args.format, args.explain)
    _summary(findings, len(files))

    if args.fail_on == "never":
        return 0
    threshold = Severity(args.fail_on).rank
    return 1 if any(f.severity.rank >= threshold for f in findings) else 0


def _emit(findings: list[Finding], fmt: str, explain: bool) -> None:
    if fmt == "json":
        print(json.dumps([f.to_dict() for f in findings], indent=2))
        return
    for f in findings:
        if fmt == "github":
            level = _GITHUB_LEVEL[f.severity.value]
            print(
                f"::{level} file={f.path},line={f.line},col={f.col},"
                f"title={f.rule_id}::{f.message}"
            )
        else:
            print(f"{f.path}:{f.line}:{f.col}: {f.rule_id} [{f.severity.value}] {f.message}")
            if explain:
                print(f"    why: {f.why}")


def _summary(findings: list[Finding], file_count: int) -> None:
    counts = {sev: 0 for sev in Severity}
    for f in findings:
        counts[f.severity] += 1
    print(
        f"replayguard: {counts[Severity.ERROR]} error(s), "
        f"{counts[Severity.WARNING]} warning(s), {counts[Severity.INFO]} info "
        f"in {file_count} file(s)",
        file=sys.stderr,
    )


def _cmd_rules() -> int:
    for rule in ALL_RULES:
        print(f"{rule.id} [{rule.severity.value}] {rule.title}")
        print(f"    {rule.why}")
    print("RG000 [error] Unparseable file")
    print("    ReplayGuard analyzes the AST; unparseable files are unverifiable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
