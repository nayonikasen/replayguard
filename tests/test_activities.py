def test_model_call_in_workflow_is_flagged(fixture_rule_counts):
    assert fixture_rule_counts("bad_workflow.py")["RG301"] == 1


def test_external_write_in_activity_is_info(fixture_findings):
    findings = fixture_findings("bad_activities.py")
    assert [f.rule_id for f in findings] == ["RG302"]
    assert findings[0].severity.value == "info"


def test_read_only_activity_is_clean(fixture_findings):
    assert fixture_findings("good_activities.py") == []


def test_select_queries_are_reads_not_writes():
    from replayguard.analyzer import analyze_source

    source = '''
from temporalio import activity


@activity.defn
def count_users(cur) -> int:
    cur.execute("SELECT COUNT(*) FROM users")
    return cur.fetchone()[0]
'''
    assert analyze_source(source, "a.py") == []


def test_local_helpers_named_like_model_methods_are_not_flagged():
    from replayguard.analyzer import analyze_source

    source = '''
from temporalio import workflow


@workflow.defn
class W:
    def generate_content(self) -> str:
        return "digest"

    @workflow.run
    async def run(self) -> str:
        return self.generate_content()
'''
    assert analyze_source(source, "w.py") == []
