"""Offline tests for the LLM pass: collection and review→finding mapping.

No network, no API key — the reviewer's only untestable part is the model call
itself, which is why review_findings() is a pure function.
"""

from pathlib import Path

from replayguard.llm.reviewer import ActivitySource, collect_activities, review_findings

FIXTURES = Path(__file__).parent / "fixtures"


def _activity() -> ActivitySource:
    return ActivitySource(path="a.py", name="send_receipt", line=8, source="...")


def test_collect_activities_finds_decorated_functions():
    activities = collect_activities(FIXTURES / "bad_activities.py")
    assert [a.name for a in activities] == ["send_receipt"]
    assert "requests.post" in activities[0].source


def test_duplicate_effect_without_mechanism_becomes_finding():
    review = {
        "effects": [
            {
                "call": "requests.post",
                "kind": "http write",
                "duplicated_on_retry": True,
                "idempotency_mechanism": None,
                "concern": "resends the receipt email",
            }
        ],
        "summary": "one unguarded write",
    }
    findings = review_findings(review, _activity())
    assert len(findings) == 1
    assert findings[0].rule_id == "RG401"
    assert "requests.post" in findings[0].message


def test_guarded_or_safe_effects_produce_no_findings():
    review = {
        "effects": [
            {
                "call": "db.upsert",
                "kind": "db write",
                "duplicated_on_retry": True,
                "idempotency_mechanism": "upsert keyed on order_id",
                "concern": None,
            },
            {
                "call": "requests.get",
                "kind": "http read",
                "duplicated_on_retry": False,
                "idempotency_mechanism": None,
                "concern": None,
            },
        ],
        "summary": "all safe",
    }
    assert review_findings(review, _activity()) == []
