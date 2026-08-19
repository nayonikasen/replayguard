from collections import Counter
from pathlib import Path

import pytest

from replayguard.analyzer import analyze_path

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_findings():
    def _run(filename: str):
        return analyze_path(FIXTURES / filename)

    return _run


@pytest.fixture
def fixture_rule_counts(fixture_findings):
    def _run(filename: str) -> Counter:
        return Counter(f.rule_id for f in fixture_findings(filename))

    return _run
