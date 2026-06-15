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
    create_pr,
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


def test_create_pr_uses_body_file_and_prints_url():
    with tempfile.TemporaryDirectory() as raw:
        body = Path(raw) / "body.md"
        body.write_text("Summary\n", encoding="utf-8")
        runner = FakeRunner([
            RunResult(["gh", "pr", "create"], 0, "https://github.test/pull/1\n", ""),
        ])

        rc, out = _capture(
            create_pr,
            title="Add wrapper",
            body_file=body,
            base="main",
            head="topic",
            draft=True,
            runner=runner,
        )

    assert rc == 0
    assert runner.calls == [[
        "gh", "pr", "create",
        "--title", "Add wrapper",
        "--body-file", str(body),
        "--base", "main",
        "--head", "topic",
        "--draft",
    ]]
    assert "ok: gh pr create --body-file" in out
    assert "https://github.test/pull/1" in out


def test_create_pr_pushes_current_branch_when_no_upstream():
    with tempfile.TemporaryDirectory() as raw:
        body = Path(raw) / "body.md"
        body.write_text("Summary\n", encoding="utf-8")
        runner = FakeRunner([
            RunResult(["git", "branch", "--show-current"], 0, "topic\n", ""),
            RunResult(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], 128, "", "no upstream"),
            RunResult(["git", "push", "-u", "origin", "HEAD:refs/heads/topic"], 0, "", ""),
            RunResult(["gh", "pr", "create"], 0, "https://github.test/pull/2\n", ""),
        ])

        rc, out = _capture(
            create_pr,
            title="Add wrapper",
            body_file=body,
            base="main",
            runner=runner,
        )

    assert rc == 0
    assert runner.calls == [
        ["git", "branch", "--show-current"],
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        ["git", "push", "-u", "origin", "HEAD:refs/heads/topic"],
        [
            "gh", "pr", "create",
            "--title", "Add wrapper",
            "--body-file", str(body),
            "--base", "main",
        ],
    ]
    assert "ok: git push -u origin HEAD:refs/heads/topic" in out
    assert "https://github.test/pull/2" in out


def test_create_pr_uses_existing_upstream_without_push():
    with tempfile.TemporaryDirectory() as raw:
        body = Path(raw) / "body.md"
        body.write_text("Summary\n", encoding="utf-8")
        runner = FakeRunner([
            RunResult(["git", "branch", "--show-current"], 0, "topic\n", ""),
            RunResult(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], 0, "origin/topic\n", ""),
            RunResult(["gh", "pr", "create"], 0, "https://github.test/pull/3\n", ""),
        ])

        rc, out = _capture(
            create_pr,
            title="Add wrapper",
            body_file=body,
            base="main",
            runner=runner,
        )

    assert rc == 0
    assert runner.calls == [
        ["git", "branch", "--show-current"],
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        [
            "gh", "pr", "create",
            "--title", "Add wrapper",
            "--body-file", str(body),
            "--base", "main",
        ],
    ]
    assert "git push" not in out


def test_create_pr_head_skips_auto_push():
    with tempfile.TemporaryDirectory() as raw:
        body = Path(raw) / "body.md"
        body.write_text("Summary\n", encoding="utf-8")
        runner = FakeRunner([
            RunResult(["gh", "pr", "create"], 0, "https://github.test/pull/4\n", ""),
        ])

        rc, _out = _capture(
            create_pr,
            title="Add wrapper",
            body_file=body,
            base="main",
            head="fork:topic",
            runner=runner,
        )

    assert rc == 0
    assert runner.calls == [[
        "gh", "pr", "create",
        "--title", "Add wrapper",
        "--body-file", str(body),
        "--base", "main",
        "--head", "fork:topic",
    ]]


def test_create_pr_refuses_detached_branch_without_head():
    with tempfile.TemporaryDirectory() as raw:
        body = Path(raw) / "body.md"
        body.write_text("Summary\n", encoding="utf-8")
        runner = FakeRunner([
            RunResult(["git", "branch", "--show-current"], 0, "", ""),
        ])

        rc, out = _capture(
            create_pr,
            title="Add wrapper",
            body_file=body,
            base="main",
            runner=runner,
        )

    assert rc == 1
    assert "could not determine current branch" in out
    assert runner.calls == [["git", "branch", "--show-current"]]


def test_create_pr_refuses_base_branch_without_head():
    with tempfile.TemporaryDirectory() as raw:
        body = Path(raw) / "body.md"
        body.write_text("Summary\n", encoding="utf-8")
        runner = FakeRunner([
            RunResult(["git", "branch", "--show-current"], 0, "main\n", ""),
        ])

        rc, out = _capture(
            create_pr,
            title="Add wrapper",
            body_file=body,
            base="main",
            runner=runner,
        )

    assert rc == 1
    assert "refusing to create a PR from base branch main" in out
    assert runner.calls == [["git", "branch", "--show-current"]]


def test_create_pr_reports_push_failure():
    with tempfile.TemporaryDirectory() as raw:
        body = Path(raw) / "body.md"
        body.write_text("Summary\n", encoding="utf-8")
        runner = FakeRunner([
            RunResult(["git", "branch", "--show-current"], 0, "topic\n", ""),
            RunResult(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], 128, "", "no upstream"),
            RunResult(["git", "push", "-u", "origin", "HEAD:refs/heads/topic"], 1, "", "denied"),
        ])

        rc, out = _capture(
            create_pr,
            title="Add wrapper",
            body_file=body,
            base="main",
            runner=runner,
        )

    assert rc == 1
    assert "git push -u origin HEAD:refs/heads/topic failed" in out
    assert "denied" in out
    assert runner.calls == [
        ["git", "branch", "--show-current"],
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        ["git", "push", "-u", "origin", "HEAD:refs/heads/topic"],
    ]


def test_create_pr_pushes_option_like_branch_as_refspec_data():
    with tempfile.TemporaryDirectory() as raw:
        body = Path(raw) / "body.md"
        body.write_text("Summary\n", encoding="utf-8")
        runner = FakeRunner([
            RunResult(["git", "branch", "--show-current"], 0, "--delete\n", ""),
            RunResult(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], 128, "", "no upstream"),
            RunResult(["git", "push", "-u", "origin", "HEAD:refs/heads/--delete"], 0, "", ""),
            RunResult(["gh", "pr", "create"], 0, "https://github.test/pull/5\n", ""),
        ])

        rc, out = _capture(
            create_pr,
            title="Add wrapper",
            body_file=body,
            base="main",
            runner=runner,
        )

    assert rc == 0
    assert runner.calls == [
        ["git", "branch", "--show-current"],
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        ["git", "push", "-u", "origin", "HEAD:refs/heads/--delete"],
        [
            "gh", "pr", "create",
            "--title", "Add wrapper",
            "--body-file", str(body),
            "--base", "main",
        ],
    ]
    assert "ok: git push -u origin HEAD:refs/heads/--delete" in out


def test_create_pr_rejects_option_like_remote_without_head():
    with tempfile.TemporaryDirectory() as raw:
        body = Path(raw) / "body.md"
        body.write_text("Summary\n", encoding="utf-8")
        runner = FakeRunner([
            RunResult(["git", "branch", "--show-current"], 0, "topic\n", ""),
        ])

        rc, out = _capture(
            create_pr,
            title="Add wrapper",
            body_file=body,
            base="main",
            remote="--mirror",
            runner=runner,
        )

    assert rc == 2
    assert "--remote must be a non-option remote name" in out
    assert runner.calls == [["git", "branch", "--show-current"]]


def test_create_pr_rejects_empty_remote_without_head():
    with tempfile.TemporaryDirectory() as raw:
        body = Path(raw) / "body.md"
        body.write_text("Summary\n", encoding="utf-8")
        runner = FakeRunner([
            RunResult(["git", "branch", "--show-current"], 0, "topic\n", ""),
        ])

        rc, out = _capture(
            create_pr,
            title="Add wrapper",
            body_file=body,
            base="main",
            remote=" ",
            runner=runner,
        )

    assert rc == 2
    assert "--remote must be a non-option remote name" in out
    assert runner.calls == [["git", "branch", "--show-current"]]


def test_create_pr_requires_body_file():
    rc, out = _capture(
        create_pr,
        title="Add wrapper",
        body_file=Path("/definitely/missing/pr-body.md"),
    )
    assert rc == 1
    assert "body file not found" in out


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


CHECKS_CMD = ["gh", "pr", "checks", "12", "--json", "name,state,link"]
CHECKS_PASSING = RunResult(CHECKS_CMD, 0, '[{"name":"ci","state":"SUCCESS"}]', "")


def test_merge_pr_polls_until_merged_then_cleans_up():
    runner = FakeRunner([
        RunResult(
            ["gh", "pr", "view", "12", "--json", "headRefName,state,url"],
            0,
            '{"headRefName":"topic","state":"OPEN"}',
            "",
        ),
        CHECKS_PASSING,
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
    assert CHECKS_CMD in runner.calls
    assert ["gh", "pr", "merge", "12", "--squash", "--delete-branch"] in runner.calls
    assert "ci: all 1 checks passed" in out
    assert "state: MERGED" in out
    assert "post-merge cleanup" in out
    assert ["git", "branch", "-D", "topic"] in runner.calls


def test_merge_pr_prints_local_validation_override_reason():
    runner = FakeRunner([
        RunResult(
            ["gh", "pr", "view", "12", "--json", "headRefName,state,url"],
            0,
            '{"headRefName":"topic","state":"OPEN"}',
            "",
        ),
        RunResult(["gh", "pr", "merge", "12", "--squash", "--delete-branch"], 0, "", ""),
        RunResult(["gh", "pr", "view", "12", "--json", "state,url"], 0, '{"state":"MERGED"}', ""),
    ])

    rc, out = _capture(
        merge_pr,
        "12",
        cleanup=False,
        skip_ci_reason="GHA budget exhausted; local suite passed.",
        runner=runner,
        sleeper=lambda _s: None,
        poll_s=0,
    )

    assert rc == 0
    assert "ci gate SKIPPED" in out
    assert "local validation override: GHA budget exhausted; local suite passed." in out
    assert "state: MERGED" in out
    assert CHECKS_CMD not in runner.calls


def test_merge_pr_retries_transient_initial_view_failure():
    runner = FakeRunner([
        RunResult(
            ["gh", "pr", "view", "12", "--json", "headRefName,state,url"],
            1,
            "",
            "HTTP 503 Service Unavailable",
        ),
        RunResult(
            ["gh", "pr", "view", "12", "--json", "headRefName,state,url"],
            0,
            '{"headRefName":"topic","state":"OPEN"}',
            "",
        ),
        CHECKS_PASSING,
        RunResult(["gh", "pr", "merge", "12", "--squash", "--delete-branch"], 0, "", ""),
        RunResult(["gh", "pr", "view", "12", "--json", "state,url"], 0, '{"state":"MERGED"}', ""),
        RunResult(["git", "branch", "--show-current"], 0, "topic\n", ""),
        RunResult(["git", "rev-parse", "--verify", "topic"], 0, "abc\n", ""),
        RunResult(["git", "checkout", "main"], 0, "", ""),
        RunResult(["git", "pull", "--ff-only", "origin", "main"], 0, "", ""),
        RunResult(["git", "branch", "-D", "topic"], 0, "", ""),
    ])

    rc, out = _capture(merge_pr, "12", runner=runner, sleeper=lambda _s: None, poll_s=0)

    assert rc == 0
    assert runner.calls.count(["gh", "pr", "view", "12", "--json", "headRefName,state,url"]) == 2
    assert "state: MERGED" in out


def test_merge_pr_rejects_malformed_initial_pr_view_without_crashing():
    runner = FakeRunner([
        RunResult(
            ["gh", "pr", "view", "12", "--json", "headRefName,state,url"],
            0,
            "[]",
            "",
        ),
    ])

    rc, out = _capture(merge_pr, "12", runner=runner, sleeper=lambda _s: None, poll_s=0)

    assert rc == 1
    assert "malformed PR data" in out


def test_merge_pr_rejects_non_string_head_ref_without_crashing():
    runner = FakeRunner([
        RunResult(
            ["gh", "pr", "view", "12", "--json", "headRefName,state,url"],
            0,
            '{"headRefName":["topic"],"state":"OPEN","url":"https://pr"}',
            "",
        ),
    ])

    rc, out = _capture(merge_pr, "12", runner=runner, sleeper=lambda _s: None, poll_s=0)

    assert rc == 1
    assert "malformed PR data" in out


def test_merge_pr_rejects_missing_head_ref_without_crashing():
    runner = FakeRunner([
        RunResult(
            ["gh", "pr", "view", "12", "--json", "headRefName,state,url"],
            0,
            '{"state":"OPEN","url":"https://pr"}',
            "",
        ),
    ])

    rc, out = _capture(merge_pr, "12", runner=runner, sleeper=lambda _s: None, poll_s=0)

    assert rc == 1
    assert "malformed PR data" in out


def test_merge_pr_rejects_missing_initial_state_without_crashing():
    runner = FakeRunner([
        RunResult(
            ["gh", "pr", "view", "12", "--json", "headRefName,state,url"],
            0,
            '{"headRefName":"topic","url":"https://pr"}',
            "",
        ),
    ])

    rc, out = _capture(merge_pr, "12", runner=runner, sleeper=lambda _s: None, poll_s=0)

    assert rc == 1
    assert "malformed PR data" in out


def test_merge_pr_recovers_when_merge_command_fails_after_pr_merged():
    runner = FakeRunner([
        RunResult(
            ["gh", "pr", "view", "12", "--json", "headRefName,state,url"],
            0,
            '{"headRefName":"topic","state":"OPEN"}',
            "",
        ),
        CHECKS_PASSING,
        RunResult(["gh", "pr", "merge", "12", "--squash", "--delete-branch"], 1, "", "HTTP 401 Unauthorized"),
        RunResult(["gh", "pr", "view", "12", "--json", "state,url"], 0, '{"state":"MERGED"}', ""),
        RunResult(["git", "branch", "--show-current"], 0, "topic\n", ""),
        RunResult(["git", "rev-parse", "--verify", "topic"], 0, "abc\n", ""),
        RunResult(["git", "checkout", "main"], 0, "", ""),
        RunResult(["git", "pull", "--ff-only", "origin", "main"], 0, "", ""),
        RunResult(["git", "branch", "-D", "topic"], 0, "", ""),
    ])

    rc, out = _capture(merge_pr, "12", runner=runner, sleeper=lambda _s: None, poll_s=0)

    assert rc == 0
    assert "merge command failed" in out
    assert "recovered: PR is already MERGED" in out
    assert "post-merge cleanup" in out
    assert runner.calls.count(["gh", "pr", "view", "12", "--json", "state,url"]) == 1


def test_merge_pr_rejects_malformed_recovery_pr_view_without_crashing():
    runner = FakeRunner([
        RunResult(
            ["gh", "pr", "view", "12", "--json", "headRefName,state,url"],
            0,
            '{"headRefName":"topic","state":"OPEN"}',
            "",
        ),
        CHECKS_PASSING,
        RunResult(["gh", "pr", "merge", "12", "--squash", "--delete-branch"], 1, "", "HTTP 401 Unauthorized"),
        RunResult(["gh", "pr", "view", "12", "--json", "state,url"], 0, "[]", ""),
    ])

    rc, out = _capture(merge_pr, "12", runner=runner, sleeper=lambda _s: None, poll_s=0)

    assert rc == 1
    assert "malformed PR data" in out


def test_merge_pr_fails_when_merge_command_error_did_not_merge_pr():
    runner = FakeRunner([
        RunResult(
            ["gh", "pr", "view", "12", "--json", "headRefName,state,url"],
            0,
            '{"headRefName":"topic","state":"OPEN"}',
            "",
        ),
        CHECKS_PASSING,
        RunResult(["gh", "pr", "merge", "12", "--squash", "--delete-branch"], 1, "", "HTTP 401 Unauthorized"),
        RunResult(["gh", "pr", "view", "12", "--json", "state,url"], 0, '{"state":"OPEN"}', ""),
    ])

    rc, out = _capture(merge_pr, "12", runner=runner, sleeper=lambda _s: None, poll_s=0)

    assert rc == 1
    assert "PR state after merge failure is OPEN" in out
    assert "post-merge cleanup" not in out


def test_merge_pr_retries_transient_poll_failure():
    runner = FakeRunner([
        RunResult(
            ["gh", "pr", "view", "12", "--json", "headRefName,state,url"],
            0,
            '{"headRefName":"topic","state":"OPEN"}',
            "",
        ),
        CHECKS_PASSING,
        RunResult(["gh", "pr", "merge", "12", "--squash", "--delete-branch"], 0, "", ""),
        RunResult(["gh", "pr", "view", "12", "--json", "state,url"], 1, "", "HTTP 502 Bad Gateway"),
        RunResult(["gh", "pr", "view", "12", "--json", "state,url"], 0, '{"state":"MERGED"}', ""),
        RunResult(["git", "branch", "--show-current"], 0, "topic\n", ""),
        RunResult(["git", "rev-parse", "--verify", "topic"], 0, "abc\n", ""),
        RunResult(["git", "checkout", "main"], 0, "", ""),
        RunResult(["git", "pull", "--ff-only", "origin", "main"], 0, "", ""),
        RunResult(["git", "branch", "-D", "topic"], 0, "", ""),
    ])

    rc, out = _capture(merge_pr, "12", runner=runner, sleeper=lambda _s: None, poll_s=0)

    assert rc == 0
    assert runner.calls.count(["gh", "pr", "view", "12", "--json", "state,url"]) == 2
    assert "state: MERGED" in out


def test_merge_pr_rejects_malformed_poll_pr_view_without_crashing():
    runner = FakeRunner([
        RunResult(
            ["gh", "pr", "view", "12", "--json", "headRefName,state,url"],
            0,
            '{"headRefName":"topic","state":"OPEN"}',
            "",
        ),
        CHECKS_PASSING,
        RunResult(["gh", "pr", "merge", "12", "--squash", "--delete-branch"], 0, "", ""),
        RunResult(["gh", "pr", "view", "12", "--json", "state,url"], 0, "[]", ""),
    ])

    rc, out = _capture(merge_pr, "12", runner=runner, sleeper=lambda _s: None, poll_s=0)

    assert rc == 1
    assert "malformed PR data" in out


def test_merge_pr_fails_when_post_merge_poll_never_confirms_state():
    runner = FakeRunner([
        RunResult(
            ["gh", "pr", "view", "12", "--json", "headRefName,state,url"],
            0,
            '{"headRefName":"topic","state":"OPEN"}',
            "",
        ),
        CHECKS_PASSING,
        RunResult(["gh", "pr", "merge", "12", "--squash", "--delete-branch"], 0, "", ""),
        RunResult(["gh", "pr", "view", "12", "--json", "state,url"], 1, "", "HTTP 502 Bad Gateway"),
        RunResult(["gh", "pr", "view", "12", "--json", "state,url"], 1, "", "HTTP 502 Bad Gateway"),
        RunResult(["gh", "pr", "view", "12", "--json", "state,url"], 1, "", "HTTP 502 Bad Gateway"),
    ])

    rc, out = _capture(merge_pr, "12", runner=runner, sleeper=lambda _s: None, poll_s=0)

    assert rc == 1
    assert runner.calls.count(["gh", "pr", "view", "12", "--json", "state,url"]) == 3
    assert "could not poll PR state" in out
    assert "post-merge cleanup" not in out


PR_VIEW_OPEN = RunResult(
    ["gh", "pr", "view", "12", "--json", "headRefName,state,url"],
    0,
    '{"headRefName":"topic","state":"OPEN"}',
    "",
)
MERGE_CMD = ["gh", "pr", "merge", "12", "--squash", "--delete-branch"]


def test_merge_pr_refuses_when_ci_checks_failing():
    runner = FakeRunner([
        PR_VIEW_OPEN,
        RunResult(
            CHECKS_CMD,
            0,
            '[{"name":"ci-required","state":"FAILURE","link":"https://gha/runs/1"},'
            '{"name":"lint","state":"SUCCESS"}]',
            "",
        ),
    ])

    rc, out = _capture(merge_pr, "12", runner=runner, sleeper=lambda _s: None, poll_s=0)

    assert rc == 1
    assert MERGE_CMD not in runner.calls
    assert "refusing to merge: CI checks are failing" in out
    assert "failed: ci-required [FAILURE] (https://gha/runs/1)" in out


def test_merge_pr_waits_for_pending_ci_then_merges():
    runner = FakeRunner([
        PR_VIEW_OPEN,
        RunResult(CHECKS_CMD, 0, '[{"name":"ci","state":"IN_PROGRESS"}]', ""),
        RunResult(CHECKS_CMD, 0, '[{"name":"ci","state":"SUCCESS"}]', ""),
        RunResult(MERGE_CMD, 0, "", ""),
        RunResult(["gh", "pr", "view", "12", "--json", "state,url"], 0, '{"state":"MERGED"}', ""),
    ])

    rc, out = _capture(
        merge_pr, "12", cleanup=False,
        runner=runner, sleeper=lambda _s: None, poll_s=0, ci_poll_s=0,
    )

    assert rc == 0
    assert runner.calls.count(CHECKS_CMD) == 2
    assert "ci: waiting for 1 pending check(s)" in out
    assert "ci: all 1 checks passed" in out
    assert "state: MERGED" in out


def test_merge_pr_refuses_when_ci_still_pending_at_deadline():
    runner = FakeRunner([
        PR_VIEW_OPEN,
        RunResult(CHECKS_CMD, 0, '[{"name":"ci","state":"IN_PROGRESS"}]', ""),
    ])

    rc, out = _capture(
        merge_pr, "12",
        runner=runner, sleeper=lambda _s: None, poll_s=0, ci_timeout_s=0, ci_poll_s=0,
    )

    assert rc == 1
    assert MERGE_CMD not in runner.calls
    assert "still pending" in out
    assert "pending: ci [IN_PROGRESS]" in out
    assert "refusing to merge: CI did not finish in time" in out


def test_merge_pr_refuses_when_no_ci_checks_reported():
    with tempfile.TemporaryDirectory() as raw:
        repo = Path(raw)
        workflows = repo / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text("name: ci\n", encoding="utf-8")
        old = os.getcwd()
        os.chdir(repo)
        try:
            runner = FakeRunner([
                PR_VIEW_OPEN,
                RunResult(CHECKS_CMD, 0, "[]", ""),
            ])

            rc, out = _capture(
                merge_pr, "12",
                runner=runner, sleeper=lambda _s: None, poll_s=0, ci_timeout_s=0, ci_poll_s=0,
            )
        finally:
            os.chdir(old)

    assert rc == 1
    assert MERGE_CMD not in runner.calls
    assert "refusing to merge: no CI checks reported" in out
    assert "--skip-ci-reason" in out


def test_merge_pr_refuses_no_ci_from_subdir_when_repo_has_workflows():
    with tempfile.TemporaryDirectory() as raw:
        repo = Path(raw)
        workflows = repo / ".github" / "workflows"
        workdir = repo / "pkg"
        workflows.mkdir(parents=True)
        workdir.mkdir()
        (repo / ".git").mkdir()
        (workflows / "ci.yml").write_text("name: ci\n", encoding="utf-8")
        old = os.getcwd()
        os.chdir(workdir)
        try:
            runner = FakeRunner([
                PR_VIEW_OPEN,
                RunResult(CHECKS_CMD, 0, "[]", ""),
            ])

            rc, out = _capture(
                merge_pr, "12",
                runner=runner, sleeper=lambda _s: None, poll_s=0, ci_timeout_s=0, ci_poll_s=0,
            )
        finally:
            os.chdir(old)

    assert rc == 1
    assert MERGE_CMD not in runner.calls
    assert "refusing to merge: no CI checks reported" in out


def test_merge_pr_auto_skips_no_ci_when_no_workflows_exist_locally():
    with tempfile.TemporaryDirectory() as raw:
        repo = Path(raw)
        (repo / ".git").mkdir()
        old = os.getcwd()
        os.chdir(repo)
        try:
            runner = FakeRunner([
                PR_VIEW_OPEN,
                RunResult(CHECKS_CMD, 0, "[]", ""),
                RunResult(MERGE_CMD, 0, "", ""),
                RunResult(["gh", "pr", "view", "12", "--json", "state,url"], 0, '{"state":"MERGED"}', ""),
            ])

            rc, out = _capture(
                merge_pr,
                "12",
                cleanup=False,
                runner=runner,
                sleeper=lambda _s: None,
                poll_s=0,
                ci_timeout_s=0,
                ci_poll_s=0,
            )
        finally:
            os.chdir(old)

    assert rc == 0
    assert MERGE_CMD in runner.calls
    assert "ci gate SKIPPED" in out
    assert "no GitHub Actions workflows found locally" in out
    assert "state: MERGED" in out


def test_merge_pr_refuses_no_ci_when_repo_root_cannot_be_proven():
    with tempfile.TemporaryDirectory() as raw:
        old = os.getcwd()
        os.chdir(raw)
        try:
            runner = FakeRunner([
                PR_VIEW_OPEN,
                RunResult(CHECKS_CMD, 0, "[]", ""),
            ])

            rc, out = _capture(
                merge_pr,
                "12",
                runner=runner,
                sleeper=lambda _s: None,
                poll_s=0,
                ci_timeout_s=0,
                ci_poll_s=0,
            )
        finally:
            os.chdir(old)

    assert rc == 1
    assert MERGE_CMD not in runner.calls
    assert "refusing to merge: no CI checks reported" in out


def test_merge_pr_treats_no_checks_reported_error_as_zero_checks():
    with tempfile.TemporaryDirectory() as raw:
        repo = Path(raw)
        workflows = repo / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text("name: ci\n", encoding="utf-8")
        old = os.getcwd()
        os.chdir(repo)
        try:
            runner = FakeRunner([
                PR_VIEW_OPEN,
                RunResult(
                    CHECKS_CMD, 1, "",
                    "no checks reported on the 'topic' branch",
                ),
            ])

            rc, out = _capture(
                merge_pr, "12",
                runner=runner, sleeper=lambda _s: None, poll_s=0, ci_timeout_s=0, ci_poll_s=0,
            )
        finally:
            os.chdir(old)

    assert rc == 1
    assert MERGE_CMD not in runner.calls
    assert "refusing to merge: no CI checks reported" in out


def test_merge_pr_refuses_when_checks_fetch_fails():
    runner = FakeRunner([
        PR_VIEW_OPEN,
        RunResult(CHECKS_CMD, 1, "", "HTTP 503 Service Unavailable"),
    ])

    rc, out = _capture(merge_pr, "12", runner=runner, sleeper=lambda _s: None, poll_s=0)

    assert rc == 1
    assert MERGE_CMD not in runner.calls
    assert "refusing to merge: CI state unknown" in out


def test_merge_pr_refuses_on_malformed_check_data():
    runner = FakeRunner([
        PR_VIEW_OPEN,
        RunResult(CHECKS_CMD, 0, '[{"name":"ci","state":42}]', ""),
    ])

    rc, out = _capture(merge_pr, "12", runner=runner, sleeper=lambda _s: None, poll_s=0)

    assert rc == 1
    assert MERGE_CMD not in runner.calls
    assert "malformed check data" in out
    assert "refusing to merge: CI state unknown" in out


def test_merge_pr_skipped_and_neutral_checks_do_not_count_as_pending():
    runner = FakeRunner([
        PR_VIEW_OPEN,
        RunResult(
            CHECKS_CMD,
            0,
            '[{"name":"ci","state":"SUCCESS"},{"name":"optional","state":"SKIPPED"},'
            '{"name":"advisory","state":"NEUTRAL"}]',
            "",
        ),
        RunResult(MERGE_CMD, 0, "", ""),
        RunResult(["gh", "pr", "view", "12", "--json", "state,url"], 0, '{"state":"MERGED"}', ""),
    ])

    rc, out = _capture(merge_pr, "12", cleanup=False, runner=runner, sleeper=lambda _s: None, poll_s=0)

    assert rc == 0
    assert "ci: all 3 checks passed" in out
    assert "state: MERGED" in out


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


def test_ci_status_rejects_malformed_check_items_without_crashing():
    runner = FakeRunner([
        RunResult(
            ["gh", "pr", "checks", "--json", "name,state,link"],
            0,
            "[null]",
            "",
        )
    ])

    rc, out = _capture(ci_status, runner=runner)

    assert rc == 1
    assert "malformed check data" in out


def test_ci_status_rejects_malformed_check_field_types_without_crashing():
    runner = FakeRunner([
        RunResult(
            ["gh", "pr", "checks", "--json", "name,state,link"],
            0,
            '[{"name":"test-python","state":1,"link":""}]',
            "",
        )
    ])

    rc, out = _capture(ci_status, runner=runner)

    assert rc == 1
    assert "malformed check data" in out


def test_ci_status_rejects_missing_required_check_fields_without_crashing():
    runner = FakeRunner([
        RunResult(
            ["gh", "pr", "checks", "--json", "name,state,link"],
            0,
            "[{}]",
            "",
        )
    ])

    rc, out = _capture(ci_status, runner=runner)

    assert rc == 1
    assert "malformed check data" in out


def test_ci_status_classifies_actions_budget_exhaustion_from_failed_run_log():
    runner = FakeRunner([
        RunResult(
            ["gh", "pr", "checks", "--json", "name,state,link"],
            0,
            '[{"name":"test-python","state":"FAILURE","link":"https://github.test/actions/runs/456/job/789"}]',
            "",
        ),
        RunResult(
            ["gh", "run", "view", "456", "--log-failed"],
            0,
            "GitHub Actions budget exhausted for this account.\n",
            "",
        ),
    ])

    rc, out = _capture(ci_status, runner=runner)

    assert rc == 2
    assert "blocked: actions_budget_exhausted" in out
    assert runner.calls[-1] == ["gh", "run", "view", "456", "--log-failed"]


def test_ci_status_scans_all_failed_action_runs_for_budget_exhaustion():
    checks = ",".join(
        f'{{"name":"test-{run_id}","state":"FAILURE","link":"https://github.test/actions/runs/{run_id}"}}'
        for run_id in (101, 102, 103, 104)
    )
    runner = FakeRunner([
        RunResult(
            ["gh", "pr", "checks", "--json", "name,state,link"],
            0,
            f"[{checks}]",
            "",
        ),
        RunResult(["gh", "run", "view", "101", "--log-failed"], 0, "FAILED tests/a.py::test_a\n", ""),
        RunResult(["gh", "run", "view", "102", "--log-failed"], 0, "FAILED tests/b.py::test_b\n", ""),
        RunResult(["gh", "run", "view", "103", "--log-failed"], 0, "FAILED tests/c.py::test_c\n", ""),
        RunResult(
            ["gh", "run", "view", "104", "--log-failed"],
            0,
            "GitHub Actions quota has been exhausted.\n",
            "",
        ),
    ])

    rc, out = _capture(ci_status, runner=runner)

    assert rc == 2
    assert "blocked: actions_budget_exhausted" in out
    assert runner.calls[-1] == ["gh", "run", "view", "104", "--log-failed"]


def test_ci_status_ignores_failed_log_fetch_when_classifying_budget_exhaustion():
    runner = FakeRunner([
        RunResult(
            ["gh", "pr", "checks", "--json", "name,state,link"],
            0,
            '[{"name":"test-python","state":"FAILURE","link":"https://github.test/actions/runs/456/job/789"}]',
            "",
        ),
        RunResult(["gh", "run", "view", "456", "--log-failed"], 1, "", "not found"),
    ])

    rc, out = _capture(ci_status, runner=runner)

    assert rc == 1
    assert "blocked: actions_budget_exhausted" not in out


def test_ci_status_does_not_classify_generic_budget_test_failure_as_actions_budget_exhaustion():
    runner = FakeRunner([
        RunResult(
            ["gh", "pr", "checks", "--json", "name,state,link"],
            0,
            '[{"name":"test-python","state":"FAILURE","link":"https://github.test/actions/runs/456/job/789"}]',
            "",
        ),
        RunResult(
            ["gh", "run", "view", "456", "--log-failed"],
            0,
            "FAILED tests/test_budget.py::test_retry_budget - AssertionError: retry budget exhausted\n",
            "",
        ),
    ])

    rc, out = _capture(ci_status, runner=runner)

    assert rc == 1
    assert "blocked: actions_budget_exhausted" not in out


def test_ci_status_does_not_classify_included_minutes_phrase_without_blocking_verb():
    runner = FakeRunner([
        RunResult(
            ["gh", "pr", "checks", "--json", "name,state,link"],
            0,
            '[{"name":"test-python","state":"FAILURE","link":"https://github.test/actions/runs/456/job/789"}]',
            "",
        ),
        RunResult(
            ["gh", "run", "view", "456", "--log-failed"],
            0,
            "FAILED tests/test_copy.py::test_text - AssertionError: included minutes for GitHub Actions docs changed\n",
            "",
        ),
    ])

    rc, out = _capture(ci_status, runner=runner)

    assert rc == 1
    assert "blocked: actions_budget_exhausted" not in out


def test_ci_status_does_not_classify_pytest_copy_failure_as_actions_budget_exhaustion():
    runner = FakeRunner([
        RunResult(
            ["gh", "pr", "checks", "--json", "name,state,link"],
            0,
            '[{"name":"test-python","state":"FAILURE","link":"https://github.test/actions/runs/456/job/789"}]',
            "",
        ),
        RunResult(
            ["gh", "run", "view", "456", "--log-failed"],
            0,
            "FAILED tests/test_docs.py::test_copy - AssertionError: GitHub Actions budget exhausted copy changed\n",
            "",
        ),
    ])

    rc, out = _capture(ci_status, runner=runner)

    assert rc == 1
    assert "blocked: actions_budget_exhausted" not in out


def test_ci_status_does_not_classify_retry_budget_limit_text_as_actions_budget_exhaustion():
    runner = FakeRunner([
        RunResult(
            ["gh", "pr", "checks", "--json", "name,state,link"],
            0,
            '[{"name":"test-python","state":"FAILURE","link":"https://github.test/actions/runs/456/job/789"}]',
            "",
        ),
        RunResult(
            ["gh", "run", "view", "456", "--log-failed"],
            0,
            "FAILED tests/test_policy.py::test_copy - AssertionError: retry budget limit for GitHub Actions changed\n",
            "",
        ),
    ])

    rc, out = _capture(ci_status, runner=runner)

    assert rc == 1
    assert "blocked: actions_budget_exhausted" not in out


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


def test_ci_failures_rejects_malformed_pr_view_without_crashing():
    runner = FakeRunner([
        RunResult(["gh", "pr", "view", "12", "--json", "headRefName"], 0, "[]", ""),
    ])

    rc, out = _capture(ci_failures, pr="12", runner=runner)

    assert rc == 1
    assert "malformed PR data" in out


def test_ci_failures_rejects_non_string_head_ref_without_crashing():
    runner = FakeRunner([
        RunResult(["gh", "pr", "view", "12", "--json", "headRefName"], 0, '{"headRefName":[]}', ""),
    ])

    rc, out = _capture(ci_failures, pr="12", runner=runner)

    assert rc == 1
    assert "malformed PR data" in out


def test_ci_failures_rejects_missing_head_ref_without_unscoped_run_lookup():
    runner = FakeRunner([
        RunResult(["gh", "pr", "view", "12", "--json", "headRefName"], 0, "{}", ""),
    ])

    rc, out = _capture(ci_failures, pr="12", runner=runner)

    assert rc == 1
    assert "malformed PR data" in out
    assert len(runner.calls) == 1


def test_ci_failures_rejects_empty_head_ref_without_unscoped_run_lookup():
    runner = FakeRunner([
        RunResult(["gh", "pr", "view", "12", "--json", "headRefName"], 0, '{"headRefName":""}', ""),
    ])

    rc, out = _capture(ci_failures, pr="12", runner=runner)

    assert rc == 1
    assert "malformed PR data" in out
    assert len(runner.calls) == 1


def test_ci_failures_rejects_malformed_run_list_without_crashing():
    runner = FakeRunner([
        RunResult(
            [
                "gh", "run", "list", "--limit", "1", "--json",
                "databaseId,conclusion,status,workflowName,url",
            ],
            0,
            "[null]",
            "",
        ),
    ])

    rc, out = _capture(ci_failures, runner=runner)

    assert rc == 1
    assert "malformed run data" in out


def test_ci_failures_rejects_missing_run_id_without_crashing():
    runner = FakeRunner([
        RunResult(
            [
                "gh", "run", "list", "--limit", "1", "--json",
                "databaseId,conclusion,status,workflowName,url",
            ],
            0,
            '[{"workflowName":"tests","url":"https://ci/run"}]',
            "",
        ),
    ])

    rc, out = _capture(ci_failures, runner=runner)

    assert rc == 1
    assert "malformed run data" in out


def test_ci_failures_rejects_non_numeric_run_id_without_crashing():
    runner = FakeRunner([
        RunResult(
            [
                "gh", "run", "list", "--limit", "1", "--json",
                "databaseId,conclusion,status,workflowName,url",
            ],
            0,
            '[{"databaseId":"not-a-run-id","workflowName":"tests","url":"https://ci/run"}]',
            "",
        ),
    ])

    rc, out = _capture(ci_failures, runner=runner)

    assert rc == 1
    assert "malformed run data" in out


def test_ci_failures_rejects_non_scalar_run_fields_without_crashing():
    runner = FakeRunner([
        RunResult(
            [
                "gh", "run", "list", "--limit", "1", "--json",
                "databaseId,conclusion,status,workflowName,url",
            ],
            0,
            '[{"databaseId":456,"workflowName":[],"url":"https://ci/run"}]',
            "",
        ),
    ])

    rc, out = _capture(ci_failures, runner=runner)

    assert rc == 1
    assert "malformed run data" in out


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


def test_ci_failures_extracts_pytest_failures_from_stderr():
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
            "",
            "FAILED tests/foo_test.py::test_bar - AssertionError: expected 1\n",
        ),
    ])
    rc, out = _capture(ci_failures, run_id="456", runner=runner)
    assert rc == 1
    assert "failing test: tests/foo_test.py::test_bar - AssertionError: expected 1" in out


def test_ci_failures_run_id_rejects_malformed_metadata_without_crashing():
    runner = FakeRunner([
        RunResult(
            ["gh", "run", "view", "456", "--json", "workflowName,url"],
            0,
            '{"workflowName":[],"url":[]}',
            "",
        ),
    ])

    rc, out = _capture(ci_failures, run_id="456", runner=runner)

    assert rc == 1
    assert "malformed run data" in out


def test_ci_failures_run_id_rejects_non_object_metadata_without_crashing():
    runner = FakeRunner([
        RunResult(
            ["gh", "run", "view", "456", "--json", "workflowName,url"],
            0,
            "[]",
            "",
        ),
    ])

    rc, out = _capture(ci_failures, run_id="456", runner=runner)

    assert rc == 1
    assert "malformed run data" in out


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


def test_summarize_pytest_log_strips_ansi_and_timestamp_prefixes():
    text = (
        "2026-06-05T00:00:00Z \x1b[31mFAILED\x1b[0m "
        "tests/foo_test.py::test_bar - AssertionError: expected 1\n"
        "2026-06-05T00:00:01Z \x1b[31m================ 1 failed in 0.12s ================\x1b[0m\n"
    )
    summary = summarize_pytest_log(text)
    assert summary.failures == ["tests/foo_test.py::test_bar - AssertionError: expected 1"]
    assert summary.final_line == "1 failed in 0.12s"


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
