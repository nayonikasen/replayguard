from pathlib import Path

from replayguard.analyzer import analyze_source, iter_python_files
from replayguard.findings import Severity


def test_syntax_error_yields_parse_finding():
    findings = analyze_source("def broken(:\n", "broken.py")
    assert len(findings) == 1
    assert findings[0].rule_id == "RG000"
    assert findings[0].severity is Severity.ERROR


def test_select_and_ignore_filter_rules():
    source = Path(__file__).parent.joinpath("fixtures", "bad_workflow.py").read_text()
    only_time = analyze_source(source, "w.py", select={"RG101"})
    assert {f.rule_id for f in only_time} == {"RG101"}
    without_time = analyze_source(source, "w.py", ignore={"RG101"})
    assert "RG101" not in {f.rule_id for f in without_time}


def test_iter_python_files_skips_virtualenvs(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "wf.py").write_text("x = 1\n")
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "junk.py").write_text("x = 1\n")
    files = list(iter_python_files([tmp_path]))
    assert [f.name for f in files] == ["wf.py"]


def test_skip_dirs_only_apply_below_the_scan_root(tmp_path):
    project = tmp_path / "build" / "myproject"
    project.mkdir(parents=True)
    (project / "wf.py").write_text("x = 1\n")
    assert [f.name for f in iter_python_files([project])] == ["wf.py"]


def test_nonexistent_paths_raise_instead_of_passing_silently(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        list(iter_python_files([tmp_path / "wrokflows"]))


def test_duplicate_spellings_of_one_file_are_linted_once(tmp_path):
    f = tmp_path / "wf.py"
    f.write_text("x = 1\n")
    files = list(iter_python_files([tmp_path, f]))
    assert len(files) == 1


def test_suppression_comment_silences_a_finding():
    source = '''
import time

from temporalio import workflow


@workflow.defn
class W:
    @workflow.run
    async def run(self) -> None:
        time.sleep(1)  # replayguard: ignore[RG105]
        time.sleep(2)  # replayguard: ignore
'''
    assert analyze_source(source, "w.py") == []


def test_suppression_comment_only_silences_listed_rules():
    source = '''
import time

from temporalio import workflow


@workflow.defn
class W:
    @workflow.run
    async def run(self) -> None:
        time.sleep(1)  # replayguard: ignore[RG101]
'''
    assert [f.rule_id for f in analyze_source(source, "w.py")] == ["RG105"]


def test_nested_activity_is_not_double_reported():
    source = '''
import requests

from temporalio import activity, workflow


@workflow.defn
class W:
    @workflow.run
    async def run(self) -> None:
        @activity.defn
        async def send(order: str) -> None:
            requests.post("https://mailer/send", json={"order": order})
'''
    findings = analyze_source(source, "w.py")
    assert [f.rule_id for f in findings] == ["RG302"]


def test_signal_handlers_are_workflow_context():
    source = '''
import time

from temporalio import workflow


@workflow.defn
class W:
    @workflow.run
    async def run(self) -> None:
        pass

    @workflow.signal
    def on_pin(self) -> None:
        time.sleep(1)
'''
    findings = analyze_source(source, "w.py")
    assert [f.rule_id for f in findings] == ["RG105"]
