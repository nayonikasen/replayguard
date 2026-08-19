def test_replay_rules_fire_on_bad_workflow(fixture_rule_counts):
    counts = fixture_rule_counts("bad_workflow.py")
    assert counts["RG201"] == 1  # execute_activity without a timeout
    assert counts["RG202"] == 1  # RetryPolicy with no bound
    assert counts["RG203"] == 1  # except BaseException swallowing cancellation


def test_reraise_is_not_flagged(tmp_path):
    from replayguard.analyzer import analyze_source

    source = '''
from temporalio import workflow


@workflow.defn
class W:
    @workflow.run
    async def run(self) -> None:
        try:
            pass
        except BaseException:
            raise
'''
    assert analyze_source(source, "w.py") == []


def test_star_kwargs_are_not_flagged(tmp_path):
    from replayguard.analyzer import analyze_source

    source = '''
from temporalio import workflow


@workflow.defn
class W:
    @workflow.run
    async def run(self, opts: dict) -> None:
        await workflow.execute_activity("a", **opts)
'''
    assert analyze_source(source, "w.py") == []


def _lint(source: str):
    from replayguard.analyzer import analyze_source

    return analyze_source(source, "w.py")


def test_method_and_class_activity_variants_need_timeouts_too():
    source = '''
from temporalio import workflow


@workflow.defn
class W:
    @workflow.run
    async def run(self) -> None:
        await workflow.execute_activity_method(W.step)
        await workflow.start_local_activity_class(W)
'''
    assert [f.rule_id for f in _lint(source)] == ["RG201", "RG201"]


def test_wrapper_helpers_named_execute_activity_are_not_flagged():
    source = '''
from temporalio import workflow

from myapp.temporal_utils import execute_activity


@workflow.defn
class W:
    @workflow.run
    async def run(self) -> None:
        await execute_activity("reindex")
'''
    assert _lint(source) == []


def test_raise_bound_name_counts_as_reraise():
    source = '''
from temporalio import workflow


@workflow.defn
class W:
    @workflow.run
    async def run(self) -> None:
        try:
            pass
        except BaseException as e:
            raise e
'''
    assert _lint(source) == []


def test_bare_raise_in_nested_handler_does_not_exempt_the_outer_one():
    source = '''
from temporalio import workflow


@workflow.defn
class W:
    @workflow.run
    async def run(self) -> None:
        try:
            pass
        except BaseException:
            try:
                pass
            except OSError:
                raise
            return
'''
    assert [f.rule_id for f in _lint(source)] == ["RG203"]


def test_explicitly_swallowing_cancelled_error_is_flagged():
    source = '''
import asyncio

from temporalio import workflow


@workflow.defn
class W:
    @workflow.run
    async def run(self) -> None:
        try:
            pass
        except asyncio.CancelledError:
            pass
'''
    assert [f.rule_id for f in _lint(source)] == ["RG203"]


def test_app_classes_merely_named_retrypolicy_are_not_flagged():
    source = '''
from temporalio import workflow

from myapp.http import HttpRetryPolicy


@workflow.defn
class W:
    @workflow.run
    async def run(self) -> None:
        policy = HttpRetryPolicy(max_retries=3)
'''
    assert _lint(source) == []


def test_module_level_retry_policy_constants_are_flagged():
    source = '''
from temporalio.common import RetryPolicy

UNBOUNDED = RetryPolicy()
'''
    assert [f.rule_id for f in _lint(source)] == ["RG202"]


def test_zero_attempts_with_non_retryable_types_is_a_deliberate_bound():
    source = '''
from temporalio.common import RetryPolicy

POLICY = RetryPolicy(maximum_attempts=0, non_retryable_error_types=["InvalidJob"])
'''
    assert _lint(source) == []
