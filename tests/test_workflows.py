"""Tests for deterministic workflow wrappers.

External GitHub/Git operations are faked here. The wrappers should turn noisy
mechanics into concise summaries without requiring network, real CI, or branch
mutation during tests.
"""
import io
import os
import sys
import subprocess
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_rails.workflows import (  # noqa: E402
    RunResult,
    ci_status,
    ci_failures,
    cleanup_after_merge,
    default_runner,
    merge_pr,
    summarize_pytest_log,
    test_summary as workflow_test_summary,
)


class FakeRunner:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        if not self.results:
            return RunResult(list(args), 0, "", "")
        result = self.results.pop(0)
        if callable(result):
            return result(args)
        return result


def _capture(fn, *args, **kwargs):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = fn(*args, **kwargs)
    return rc, buf.getvalue()


def test_cleanup_after_merge_runs_checkout_pull_delete():
    runner = FakeRunner([
        RunResult(["git", "branch", "--show-current"], 0, "feature\n", ""),
        RunResult(["git", "rev-parse", "--verify", "feature"], 0, "abc\n", ""),
        RunResult(["git", "checkout", "main"], 0, "", ""),
        RunResult(["git", "pull", "--ff-only", "origin", "main"], 0, "", ""),
        RunResult(["git", "branch", "-d", "feature"], 0, "", ""),
    ])
    rc, out = _capture(cleanup_after_merge, runner=runner)
    assert rc == 0
    assert runner.calls == [
        ["git", "branch", "--show-current"],
        ["git", "rev-parse", "--verify", "feature"],
        ["git", "checkout", "main"],
        ["git", "pull", "--ff-only", "origin", "main"],
        ["git", "branch", "-d", "feature"],
    ]
    assert "post-merge cleanup" in out
    assert "ok: git branch -d feature" in out


def test_cleanup_after_merge_refuses_to_delete_main():
    runner = FakeRunner([
        RunResult(["git", "branch", "--show-current"], 0, "main\n", ""),
    ])
    rc, out = _capture(cleanup_after_merge, runner=runner)
    assert rc == 1
    assert "refusing to delete main" in out
    assert runner.calls == [["git", "branch", "--show-current"]]


def test_merge_pr_polls_until_merged_then_cleans_up():
    runner = FakeRunner([
        RunResult(
            ["gh", "pr", "view", "12", "--json", "headRefName,state,url"],
            0,
            '{"headRefName":"topic","state":"OPEN"}',
            "",
        ),
        RunResult(["gh", "pr", "merge", "12", "--squash", "--delete-branch"], 0, "", ""),
        RunResult(["gh", "pr", "view", "12", "--json", "state,url"], 0, '{"state":"OPEN"}', ""),
        RunResult(["gh", "pr", "view", "12", "--json", "state,url"], 0, '{"state":"MERGED"}', ""),
        RunResult(["git", "branch", "--show-current"], 0, "topic\n", ""),
        RunResult(["git", "rev-parse", "--verify", "topic"], 0, "abc\n", ""),
        RunResult(["git", "checkout", "main"], 0, "", ""),
        RunResult(["git", "pull", "--ff-only", "origin", "main"], 0, "", ""),
        RunResult(["git", "branch", "-D", "topic"], 0, "", ""),
    ])
    rc, out = _capture(merge_pr, "12", runner=runner, sleeper=lambda _s: None, poll_s=0)
    assert rc == 0
    assert ["gh", "pr", "merge", "12", "--squash", "--delete-branch"] in runner.calls
    assert "state: MERGED" in out
    assert "post-merge cleanup" in out
    assert ["git", "branch", "-D", "topic"] in runner.calls


def test_cleanup_after_squash_uses_force_delete_to_avoid_not_merged_failure():
    runner = FakeRunner([
        RunResult(["git", "branch", "--show-current"], 0, "topic\n", ""),
        RunResult(["git", "rev-parse", "--verify", "topic"], 0, "abc\n", ""),
        RunResult(["git", "checkout", "main"], 0, "", ""),
        RunResult(["git", "pull", "--ff-only", "origin", "main"], 0, "", ""),
        RunResult(["git", "branch", "-D", "topic"], 0, "", ""),
    ])
    rc, out = _capture(cleanup_after_merge, runner=runner, force_delete=True)
    assert rc == 0
    assert ["git", "branch", "-D", "topic"] in runner.calls
    assert "ok: git branch -D topic" in out


def test_cleanup_after_merge_without_force_delete_reports_not_merged_failure():
    runner = FakeRunner([
        RunResult(["git", "branch", "--show-current"], 0, "topic\n", ""),
        RunResult(["git", "rev-parse", "--verify", "topic"], 0, "abc\n", ""),
        RunResult(["git", "checkout", "main"], 0, "", ""),
        RunResult(["git", "pull", "--ff-only", "origin", "main"], 0, "", ""),
        RunResult(
            ["git", "branch", "-d", "topic"],
            1,
            "",
            "error: The branch 'topic' is not fully merged.\n",
        ),
    ])
    rc, out = _capture(cleanup_after_merge, runner=runner)
    assert rc == 1
    assert "not fully merged" in out


def test_cleanup_dry_run_does_not_probe_branch_existence_when_branch_given():
    runner = FakeRunner([])
    rc, out = _capture(cleanup_after_merge, "topic", runner=runner, dry_run=True)
    assert rc == 0
    assert runner.calls == []
    assert "would run: git branch -d topic" in out


def test_ci_status_summarizes_failing_checks():
    runner = FakeRunner([
        RunResult(
            ["gh", "pr", "checks", "--json", "name,state,link"],
            0,
            '[{"name":"test-python","state":"FAILURE","link":"https://ci/job"}]',
            "",
        )
    ])
    rc, out = _capture(ci_status, runner=runner)
    assert rc == 1
    assert "1 total, 1 failing" in out
    assert "test-python" in out
    assert "https://ci/job" in out


def test_ci_status_counts_pending_without_double_counting_failures():
    runner = FakeRunner([
        RunResult(
            ["gh", "pr", "checks", "--json", "name,state,link"],
            0,
            '['
            '{"name":"queued","state":"PENDING","link":""},'
            '{"name":"failed","state":"FAILURE","link":""},'
            '{"name":"ok","state":"SUCCESS","link":""}'
            ']',
            "",
        )
    ])
    rc, out = _capture(ci_status, runner=runner)
    assert rc == 1
    assert "3 total, 1 failing, 1 pending" in out


def test_ci_failures_pr_scopes_to_head_branch():
    runner = FakeRunner([
        RunResult(["gh", "pr", "view", "12", "--json", "headRefName"], 0, '{"headRefName":"topic"}', ""),
        RunResult(
            [
                "gh", "run", "list", "--limit", "1", "--json",
                "databaseId,conclusion,status,workflowName,url", "--branch", "topic",
            ],
            0,
            '[{"databaseId":456,"workflowName":"tests","url":"https://ci/run"}]',
            "",
        ),
        RunResult(
            ["gh", "run", "view", "456", "--log-failed"],
            0,
            "FAILED tests/foo_test.py::test_bar - AssertionError: expected 1\n",
            "",
        ),
    ])
    rc, out = _capture(ci_failures, pr="12", runner=runner)
    assert rc == 1
    assert runner.calls[1] == [
        "gh", "run", "list", "--limit", "1", "--json",
        "databaseId,conclusion,status,workflowName,url", "--branch", "topic",
    ]
    assert "tests (topic) #456" in out
    assert "failing test: tests/foo_test.py::test_bar" in out


def test_ci_failures_run_id_fetches_workflow_context():
    runner = FakeRunner([
        RunResult(
            ["gh", "run", "view", "456", "--json", "workflowName,url"],
            0,
            '{"workflowName":"tests","url":"https://ci/run"}',
            "",
        ),
        RunResult(
            ["gh", "run", "view", "456", "--log-failed"],
            0,
            "FAILED tests/foo_test.py::test_bar - AssertionError: expected 1\n",
            "",
        ),
    ])
    rc, out = _capture(ci_failures, run_id="456", runner=runner)
    assert rc == 1
    assert "tests #456 (https://ci/run)" in out


def test_default_runner_times_out():
    def slow_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["gh", "pr", "view"], timeout=0.01)

    original = subprocess.run
    subprocess.run = slow_run
    try:
        res = default_runner(["gh", "pr", "view"], timeout_s=0.01)
    finally:
        subprocess.run = original
    assert res.returncode == 124
    assert "timed out after 0.01s" in res.stderr


def test_summarize_pytest_log_extracts_failures_and_errors():
    text = """
    E   AssertionError: expected 1
FAILED tests/foo_test.py::test_bar - AssertionError: expected 1
ERROR tests/bad_test.py::test_import - ImportError: nope
================ 1 failed, 1 error, 2 passed in 0.12s ================
"""
    summary = summarize_pytest_log(text)
    assert summary.failures == ["tests/foo_test.py::test_bar - AssertionError: expected 1"]
    assert summary.errors == ["tests/bad_test.py::test_import - ImportError: nope"]
    assert summary.final_line == "1 failed, 1 error, 2 passed in 0.12s"
    assert "E   AssertionError: expected 1" in summary.trace_lines


def test_summarize_pytest_log_extracts_gh_prefixed_lines():
    text = (
        "test-python\tRun pytest\t2026-06-05T00:00:00Z\t"
        "FAILED tests/foo_test.py::test_bar - AssertionError: expected 1\n"
    )
    summary = summarize_pytest_log(text)
    assert summary.failures == ["tests/foo_test.py::test_bar - AssertionError: expected 1"]


def test_test_summary_missing_file_reports_error():
    rc, out = _capture(workflow_test_summary, Path("/definitely/missing/.pytest_output.log"))
    assert rc == 1
    assert "could not read" in out


def test_test_summary_reads_saved_log():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / ".pytest_output.log"
        p.write_text(
            "FAILED tests/foo_test.py::test_bar - AssertionError: expected 1\n",
            encoding="utf-8",
        )
        rc, out = _capture(workflow_test_summary, p)
    assert rc == 1
    assert "failing test: tests/foo_test.py::test_bar" in out


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _run import run_module_tests
    sys.exit(run_module_tests(globals()))
