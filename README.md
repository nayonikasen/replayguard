# ReplayGuard

**A linter + review agent for Temporal Python workflows. Catches determinism violations, replay hazards, and non-idempotent activities in CI — before they page you at 3am.**

[![CI](https://github.com/nayonikasen/replayguard/actions/workflows/ci.yml/badge.svg)](https://github.com/nayonikasen/replayguard/actions/workflows/ci.yml)

Durable execution makes a hard promise: crash anywhere, resume exactly. That promise holds only if your workflow code is deterministic and your activities are safe to retry — and today both invariants are enforced by convention, reviewed by humans, and broken in production. ReplayGuard checks them statically, plus an optional LLM pass that audits each activity for the question no AST rule can answer: *if this function is killed after its side effect and re-run from the top, what happens?*

```console
$ replayguard check orders/

orders/workflow.py:23:18: RG101 [error] `datetime.datetime.now()` reads the wall clock; use `workflow.now()` instead
orders/workflow.py:41:14: RG201 [error] `temporalio.workflow.execute_activity` without start_to_close_timeout or schedule_to_close_timeout
orders/workflow.py:48:25: RG202 [warning] RetryPolicy sets no maximum_attempts and no non_retryable_error_types; retries stop only when schedule_to_close_timeout says so — never, if only start_to_close_timeout is set
orders/workflow.py:60:18: RG301 [error] `self.client.messages.create()` looks like a model API call inside workflow code; move it to an activity
replayguard: 3 error(s), 1 warning(s), 0 info in 4 file(s)
```

## Install

```bash
pip install replayguard            # static rules, zero dependencies
pip install 'replayguard[llm]'     # + the LLM review pass
```

## Usage

```bash
replayguard check path/to/code       # static rules; exit 1 on errors
replayguard check . --explain        # print the production rationale behind each finding
replayguard check . --format github  # annotations in GitHub Actions
replayguard review path/to/code      # static rules + LLM retry-safety audit of activities
replayguard rules                    # list every rule and why it exists
```

The `review` command needs Anthropic credentials (`ANTHROPIC_API_KEY`, or a profile from `ant auth login`). Pick the model with `--model` or `REPLAYGUARD_MODEL` (default: `claude-opus-5`).

Silence a single finding inline instead of disabling the whole rule:

```python
stamp = time.time()  # replayguard: ignore[RG101] — display only, never branched on
```

(`# replayguard: ignore` without a rule list suppresses everything on that line.)

## The rules

Every rule exists because the failure it catches happens in real production systems — usually at the worst possible time, because replay bugs by definition only fire during recovery.

### Determinism (RG1xx) — workflow code must replay identically

| Rule | Catches | Because on replay… |
|------|---------|--------------------|
| RG101 | `time.time()`, `datetime.now()`, … | the clock has moved; the run diverges from history |
| RG102 | `random.*`, `uuid.uuid4()`, `secrets.*` | the dice roll differently; use `workflow.random()` / `workflow.uuid4()` |
| RG103 | `requests.*`, `open()`, sockets, DB clients | unrecorded I/O returns different data — or fails — silently |
| RG104 | `os.environ`, `os.getenv()` | a redeployed worker sees different config than the original run |
| RG105 | `time.sleep()` | it blocks the worker and isn't durable — `asyncio.sleep()` / `workflow.sleep()` are Temporal's durable timers |
| RG106 | threads, subprocesses, executors | their interleavings aren't in the event history |
| RG107 | iterating directly over a `set` literal or `set()`/`frozenset()` call | str hash randomization reorders iteration across worker processes |
| RG108 | `global` statements | module globals reset on worker restart; replay can't reconstruct them |

### Replay & retry semantics (RG2xx) — what durable execution doesn't save you from

| Rule | Catches | Because… |
|------|---------|----------|
| RG201 | activity calls with no timeout | the SDK raises at runtime; catch it in CI, not after deploy |
| RG202 | `RetryPolicy` with no bound | unbounded retries against a paid API silently multiply your bill |
| RG203 | bare `except:` / `except BaseException:` | it swallows `CancelledError` — the workflow keeps running after you cancel it |

### Agent-era rules (RG3xx–RG4xx) — LLM calls are the new I/O

| Rule | Catches | Because… |
|------|---------|----------|
| RG301 | model API calls in workflow code | replay would re-issue the call: pay again, get a different answer |
| RG302 | external writes in activities | retries re-run activities from the top; every write needs an idempotency story |
| RG401 | *(LLM pass)* effects that duplicate on retry with no visible guard | "the agent did it twice" starts here |

## Doesn't Temporal's sandbox already do this?

Partly — and where it does, ReplayGuard is defense in depth, not a replacement. The Python SDK's workflow sandbox intercepts many non-deterministic calls **at runtime, on the worker**. ReplayGuard is complementary on three axes:

1. **Shift left.** A sandbox violation surfaces when the workflow executes; a lint failure surfaces in the pull request.
2. **Different coverage.** The sandbox doesn't police set-iteration ordering, unbounded retry cost, swallowed cancellation, missing timeouts (until the call raises), or the semantic idempotency of your activities. Those are exactly the RG107/RG2xx/RG3xx/RG401 lanes.
3. **Sandbox-off code.** Plenty of production code runs with the sandbox disabled or passed-through for performance; static analysis doesn't care.

## Honest limitations

- Python + `temporalio` only, for now. Name matching is import-resolved but not scope-aware: dynamic dispatch can evade rules, and a local variable shadowing an imported name can trigger one (a linter, not a prover).
- `from temporalio.workflow import *` defeats classification entirely — the file lints clean. Don't star-import in workflow files (you shouldn't anyway).
- RG107 only sees literal sets in iteration position; a set bound to a variable first is missed.
- The LLM pass reports what's *visible in the code* — it can't know your gateway dedupes downstream. Treat RG401 findings as review prompts, not verdicts.
- No config file yet; use `--select` / `--ignore` or inline `# replayguard: ignore[...]` comments.

## Roadmap

- `pre-commit` hook
- Rule config in `pyproject.toml`
- Local assignment tracking (catch the variable-bound set, drop the shadowed-name false positive)
- Worker-versioning / `workflow.patched()` hazard checks (needs git history)
- TypeScript SDK support

## License

MIT © Nayonika Sen
