def test_bad_workflow_trips_every_determinism_rule(fixture_rule_counts):
    counts = fixture_rule_counts("bad_workflow.py")
    assert counts["RG101"] == 2  # datetime.now + time.time
    assert counts["RG102"] == 1  # random.randint
    assert counts["RG103"] == 2  # requests.post + open
    assert counts["RG104"] == 2  # os.getenv + os.environ
    assert counts["RG105"] == 1  # time.sleep only — asyncio.sleep is durable
    assert counts["RG106"] == 2  # threading.Thread + subprocess.run
    assert counts["RG107"] == 1  # iteration over a set literal
    assert counts["RG108"] == 1  # global statement


def test_good_workflow_is_clean(fixture_findings):
    assert fixture_findings("good_workflow.py") == []


def test_aliased_imports_are_resolved(fixture_rule_counts):
    counts = fixture_rule_counts("aliased_workflow.py")
    assert counts["RG105"] == 1
    assert counts.total() == 1


def test_plain_python_is_ignored(fixture_findings):
    assert fixture_findings("not_temporal.py") == []


WORKFLOW_TEMPLATE = '''
import asyncio

from temporalio import workflow


@workflow.defn
class W:
    @workflow.run
    async def run(self) -> None:
{body}
'''


def _lint(body: str):
    from replayguard.analyzer import analyze_source

    return analyze_source(WORKFLOW_TEMPLATE.format(body=body), "w.py")


def test_asyncio_sleep_is_a_durable_timer_not_a_finding():
    assert _lint("        await asyncio.sleep(60)") == []


def test_urllib_parse_is_pure_but_urllib_request_is_io():
    clean = '''
from urllib.parse import urlencode

from temporalio import workflow


@workflow.defn
class W:
    @workflow.run
    async def run(self, token: str) -> str:
        return urlencode({"token": token})
'''
    dirty = '''
import urllib.request

from temporalio import workflow


@workflow.defn
class W:
    @workflow.run
    async def run(self) -> None:
        urllib.request.urlopen("https://example.com")
'''
    from replayguard.analyzer import analyze_source

    assert analyze_source(clean, "w.py") == []
    assert [f.rule_id for f in analyze_source(dirty, "w.py")] == ["RG103"]


def test_chained_call_roots_still_match_suffix_rules():
    findings = _lint(
        "        await asyncio.get_event_loop().run_in_executor(None, print)"
    )
    assert [f.rule_id for f in findings] == ["RG106"]
