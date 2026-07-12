"""CLI tests — report/status/version over the same core the hooks use."""
import io
import os
import stat
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp(prefix="agent-rails-cli-")
os.environ["AGENT_RAILS_STATE_DIR"] = _TMP

from agent_rails.cli import build_parser, main  # noqa: E402
import agent_rails.cli as cli_module  # noqa: E402
from agent_rails.core.audit import clear_audit, log_verdict  # noqa: E402
from agent_rails.core.budget import read_state as budget_read_state  # noqa: E402
from agent_rails.core.budget import reset as budget_reset  # noqa: E402
from agent_rails.detectors.base import BLOCK, NUDGE, Verdict  # noqa: E402


def _run(argv) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    assert rc == 0
    return buf.getvalue()


def _run_err(argv) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stderr(buf):
        rc = main(argv)
    return rc, buf.getvalue()


def test_version():
    assert "agent-rails" in _run(["version"])


def test_status_emits_config():
    out = _run(["status", os.getcwd()])
    assert '"mode"' in out and '"detectors"' in out


def test_commands_lists_workflow_wrappers():
    out = _run(["commands"])
    assert "Available agent-rails wrappers" in out
    assert "agent-rails pr-create --title <title> --body-file <path>" in out
    assert "agent-rails pr-merge <pr>" in out
    assert "agent-rails ci-status [pr] --wait" in out
    assert "agent-rails ci-preflight [pr]" in out
    assert "agent-rails ci-failures --pr <pr>" in out
    assert "sandbox escalation" in out


def test_report_empty():
    clear_audit()
    assert "No verdicts recorded" in _run(["report"])


def test_report_summarizes_and_reset():
    clear_audit()
    log_verdict("s", "Bash", Verdict(NUDGE, "repetition", "r", would_block=True))
    log_verdict("s", "Edit", Verdict(BLOCK, "error_streak", "b"))
    out = _run(["report"])
    assert "repetition" in out and "error_streak" in out
    assert "would-block" in out
    # --reset clears, so a follow-up report is empty again
    _run(["report", "--reset"])
    assert "No verdicts recorded" in _run(["report"])


def test_report_json():
    clear_audit()
    log_verdict("s", "Bash", Verdict(NUDGE, "repetition", "r"))
    out = _run(["report", "--json"])
    assert '"by_detector"' in out and '"repetition"' in out


def test_workflow_subcommands_parse():
    parser = build_parser()
    cases = [
        ["pr-merge", "12", "--method", "squash", "--skip-ci-reason", "local only", "--command-timeout", "10"],
        ["pr-create", "--title", "T", "--body-file", "body.md", "--head", "topic", "--remote", "upstream"],
        ["pr-create", "--title", "T", "--body", "-", "--head", "topic"],
        ["post-merge-cleanup", "topic", "--force-delete", "--dry-run"],
        ["ci-status", "12", "--command-timeout", "10"],
        ["ci-status", "12", "--wait", "--timeout", "120", "--poll", "5"],
        ["ci-preflight", "12", "--command-timeout", "10"],
        ["ci-failures", "12", "--command-timeout", "10"],
        ["ci-failures", "--run", "456", "--command-timeout", "10"],
        ["test-summary", ".pytest_output.log"],
        ["preflight"],
        ["preflight", "--list"],
        ["preflight", "full-suite-readiness", "--", "--strict"],
        ["code-atlas", ".", "--glob", "*.py", "--min-lines", "100"],
        ["repo-health", ".", "--min-lines", "1000", "--max-suggestions", "3"],
        ["locate", "pick directory endpoint", "--glob", "*.py"],
        ["locate-symbol", "do_GET", "--max-results", "3"],
        ["locate-edit", "where should I add repo root field?", "--context-lines", "40"],
        ["ledger", "add", "--kind", "constraint", "--claim", "C", "--evidence", "E", "--scope", "x.py"],
        ["ledger", "check"],
        ["ledger", "relevant", "x.py"],
        ["ledger", "reverify", "record-slug", "--timeout", "5"],
        ["ledger", "retire", "record-slug"],
        ["budget", "session-123"],
        ["budget", "session-123", "add", "20"],
        ["budget", "session-123", "add", "3", "--self"],
        ["budget", "session-123", "reset", "20"],
        ["budget", "session-123", "subagent"],
        ["budget", "task-type", "list"],
        ["budget", "task-type", "set", "session-123", "debug"],
    ]
    for argv in cases:
        args = parser.parse_args(argv)
        assert args.command == argv[0]
        assert callable(args.func)


def test_commands_lists_repo_local_preflights():
    with tempfile.TemporaryDirectory() as tmp:
        repo = os.path.join(tmp, "repo")
        preflight_dir = os.path.join(repo, ".agent-rails", "preflight")
        os.makedirs(preflight_dir)
        script = os.path.join(preflight_dir, "full-suite-readiness")
        with open(script, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(script, os.stat(script).st_mode | stat.S_IXUSR)

        old_cwd = os.getcwd()
        try:
            os.chdir(repo)
            out = _run(["commands"])
        finally:
            os.chdir(old_cwd)

    assert "Preflight:" in out
    assert "agent-rails preflight full-suite-readiness" in out


def test_ledger_add_check_relevant_and_stale():
    with tempfile.TemporaryDirectory() as tmp:
        repo = os.path.join(tmp, "repo")
        os.makedirs(repo)
        src = os.path.join(repo, "x.py")
        with open(src, "w", encoding="utf-8") as f:
            f.write("VALUE = 1\n")

        out = _run([
            "ledger",
            "--dir",
            repo,
            "add",
            "--kind",
            "ruled-out",
            "--claim",
            "Changing VALUE fixes the failure",
            "--evidence",
            "The focused test still failed after VALUE changed.",
            "--falsifier",
            "false",
            "--scope",
            "x.py",
            "--cost",
            "~10min",
        ])
        assert "added changing-value-fixes-the-failure" in out
        record_path = os.path.join(repo, ".ledger", "changing-value-fixes-the-failure.md")
        assert os.path.exists(record_path)

        relevant = _run(["ledger", "--dir", repo, "relevant", "x.py"])
        assert "Changing VALUE fixes the failure" in relevant

        with open(src, "w", encoding="utf-8") as f:
            f.write("VALUE = 2\n")
        checked = _run(["ledger", "--dir", repo, "check"])
        assert "stale: 1" in checked
        with open(os.path.join(repo, ".ledger", "LEDGER.md"), encoding="utf-8") as f:
            assert "[STALE]" in f.read()


def test_ledger_reverify_repins_on_failing_falsifier_and_retires_on_passing_falsifier():
    with tempfile.TemporaryDirectory() as tmp:
        repo = os.path.join(tmp, "repo")
        os.makedirs(repo)
        with open(os.path.join(repo, "x.py"), "w", encoding="utf-8") as f:
            f.write("VALUE = 1\n")

        _run([
            "ledger",
            "--dir",
            repo,
            "add",
            "--kind",
            "ruled-out",
            "--claim",
            "Dead hypothesis",
            "--evidence",
            "A falsifier killed it.",
            "--falsifier",
            "false",
            "--scope",
            "x.py",
        ])
        out = _run(["ledger", "--dir", repo, "reverify", "dead-hypothesis"])
        assert "re-pinned dead-hypothesis" in out
        assert os.path.exists(os.path.join(repo, ".ledger", "dead-hypothesis.md"))

        with open(os.path.join(repo, ".ledger", "dead-hypothesis.md"), "r", encoding="utf-8") as f:
            text = f.read()
        with open(os.path.join(repo, ".ledger", "dead-hypothesis.md"), "w", encoding="utf-8") as f:
            f.write(text.replace("falsifier: |\n  false", "falsifier: |\n  true"))
        out = _run(["ledger", "--dir", repo, "reverify", "dead-hypothesis"])
        assert "retired dead-hypothesis" in out
        assert not os.path.exists(os.path.join(repo, ".ledger", "dead-hypothesis.md"))
        with open(os.path.join(repo, ".ledger", "LEDGER.md"), encoding="utf-8") as f:
            assert "Retired records:" in f.read()


def test_ledger_reverify_timeout_keeps_record():
    with tempfile.TemporaryDirectory() as tmp:
        repo = os.path.join(tmp, "repo")
        os.makedirs(repo)
        with open(os.path.join(repo, "x.py"), "w", encoding="utf-8") as f:
            f.write("VALUE = 1\n")
        _run([
            "ledger",
            "--dir",
            repo,
            "add",
            "--kind",
            "ruled-out",
            "--claim",
            "Slow falsifier",
            "--evidence",
            "A timeout should not retire the record.",
            "--falsifier",
            "python3 -c 'import time; time.sleep(2)'",
            "--scope",
            "x.py",
        ])

        rc, err = _run_err(["ledger", "--dir", repo, "reverify", "slow-falsifier", "--timeout", "1"])
        assert rc == 2
        assert "timed out" in err
        assert os.path.exists(os.path.join(repo, ".ledger", "slow-falsifier.md"))


def test_ledger_retire_and_malformed_records_fail_open():
    with tempfile.TemporaryDirectory() as tmp:
        repo = os.path.join(tmp, "repo")
        os.makedirs(repo)
        with open(os.path.join(repo, "x.py"), "w", encoding="utf-8") as f:
            f.write("VALUE = 1\n")
        os.makedirs(os.path.join(repo, ".ledger"))
        with open(os.path.join(repo, ".ledger", "bad.md"), "w", encoding="utf-8") as f:
            f.write("---\nkind: ruled-out\nclaim: missing evidence\n---\n")

        checked = _run(["ledger", "--dir", repo, "check"])
        assert "checked 0 ledger record(s)" in checked
        relevant = _run(["ledger", "--dir", repo, "relevant", "x.py"])
        assert "No relevant ledger records." in relevant

        _run([
            "ledger",
            "--dir",
            repo,
            "add",
            "--kind",
            "constraint",
            "--claim",
            "Do not infer from generated output",
            "--evidence",
            "The source test is the authority.",
            "--scope",
            "x.py",
        ])
        out = _run(["ledger", "--dir", repo, "retire", "do-not-infer-from-generated-output", "--reason", "graduated"])
        assert "retired do-not-infer-from-generated-output" in out
        with open(os.path.join(repo, ".ledger", "LEDGER.md"), encoding="utf-8") as f:
            index = f.read()
        assert "Retired records:" in index
        assert "graduated" in index


def test_ledger_record_without_valid_while_is_stale_loud():
    with tempfile.TemporaryDirectory() as tmp:
        repo = os.path.join(tmp, "repo")
        os.makedirs(os.path.join(repo, ".ledger"))
        with open(os.path.join(repo, "x.py"), "w", encoding="utf-8") as f:
            f.write("VALUE = 1\n")
        with open(os.path.join(repo, ".ledger", "pinless.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\n"
                "kind: constraint\n"
                "claim: >\n"
                "  Pinless records must not look live.\n"
                "evidence: >\n"
                "  There is no expiry pin.\n"
                "scope:\n"
                "  - x.py\n"
                "---\n"
            )

        checked = _run(["ledger", "--dir", repo, "check"])
        assert "stale: 1" in checked
        with open(os.path.join(repo, ".ledger", "LEDGER.md"), encoding="utf-8") as f:
            assert "pinless [STALE]" in f.read()


def test_ledger_rejects_scope_that_escapes_repo():
    with tempfile.TemporaryDirectory() as tmp:
        repo = os.path.join(tmp, "repo")
        os.makedirs(repo)
        rc, err = _run_err([
            "ledger",
            "--dir",
            repo,
            "add",
            "--kind",
            "constraint",
            "--claim",
            "Outside file matters",
            "--evidence",
            "This should not be accepted.",
            "--scope",
            "../outside.txt",
        ])
        assert rc == 2
        assert "at least one --scope path is required" in err
        assert not os.path.exists(os.path.join(repo, ".ledger"))


def test_preflight_lists_and_runs_repo_script():
    with tempfile.TemporaryDirectory() as tmp:
        repo = os.path.join(tmp, "repo")
        preflight_dir = os.path.join(repo, ".agent-rails", "preflight")
        os.makedirs(preflight_dir)
        script = os.path.join(preflight_dir, "echo-args")
        out_file = os.path.join(repo, "out.txt")
        with open(script, "w", encoding="utf-8") as f:
            f.write(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$AGENT_RAILS_REPO_ROOT\" \"$AGENT_RAILS_PREFLIGHT_NAME\" \"$@\" > out.txt\n"
            )
        os.chmod(script, os.stat(script).st_mode | stat.S_IXUSR)

        old_cwd = os.getcwd()
        try:
            os.chdir(repo)
            out = _run(["preflight", "--list"])
            assert "echo-args" in out
            rc = main(["preflight", "echo-args", "--", "--strict", "x"])
        finally:
            os.chdir(old_cwd)

        assert rc == 0
        with open(out_file, encoding="utf-8") as f:
            lines = f.read().splitlines()
        assert lines == [
            os.path.realpath(repo),
            "echo-args",
            "--strict",
            "x",
        ]


def test_preflight_rejects_unknown_and_non_executable():
    with tempfile.TemporaryDirectory() as tmp:
        repo = os.path.join(tmp, "repo")
        preflight_dir = os.path.join(repo, ".agent-rails", "preflight")
        os.makedirs(preflight_dir)
        with open(os.path.join(preflight_dir, "not-executable"), "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\nexit 0\n")

        old_cwd = os.getcwd()
        try:
            os.chdir(repo)
            err = io.StringIO()
            with redirect_stderr(err):
                rc = main(["preflight", "missing"])
            assert rc == 2
            assert "unknown repo-local preflight" in err.getvalue()

            err = io.StringIO()
            with redirect_stderr(err):
                rc = main(["preflight", "not-executable"])
            assert rc == 2
            assert "not executable" in err.getvalue()
        finally:
            os.chdir(old_cwd)


def test_preflight_rejects_symlink_escape():
    with tempfile.TemporaryDirectory() as tmp:
        repo = os.path.join(tmp, "repo")
        preflight_dir = os.path.join(repo, ".agent-rails", "preflight")
        os.makedirs(preflight_dir)
        outside = os.path.join(tmp, "outside-preflight")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(outside, os.stat(outside).st_mode | stat.S_IXUSR)
        os.symlink(outside, os.path.join(preflight_dir, "escape"))

        old_cwd = os.getcwd()
        try:
            os.chdir(repo)
            err = io.StringIO()
            with redirect_stderr(err):
                rc = main(["preflight", "escape"])
        finally:
            os.chdir(old_cwd)

    assert rc == 2
    assert "escapes the repo" in err.getvalue()


def test_budget_short_form_status_shows_next_steps():
    session = "cli-budget-status"
    budget_reset(session)

    out = _run(["budget", session])

    assert "no budget state found" in out
    assert f"agent-rails budget {session} add 20" in out
    assert f"agent-rails budget {session} reset" in out


def test_budget_short_form_add_reset_and_subagent():
    session = "cli-budget-short-form"
    budget_reset(session)

    out = _run(["budget", session, "add", "30"])
    assert "approved:" in out
    assert budget_read_state(session)["approved_tool_calls"] == 30

    out = _run(["budget", session, "subagent"])
    assert "subagent_approved:   True" in out
    assert budget_read_state(session)["subagent_approved"] is True

    out = _run(["budget", session, "reset", "20"])
    assert "pre-approved: 20" in out
    assert budget_read_state(session)["approved_tool_calls"] == 45


def test_budget_short_form_rejects_bad_add_value():
    err = io.StringIO()
    with redirect_stderr(err):
        rc = main(["budget", "cli-budget-bad-add", "add", "nope"])

    assert rc == 2
    assert "add requires a positive integer" in err.getvalue()
    assert "agent-rails budget cli-budget-bad-add add 20" in err.getvalue()


def test_budget_short_form_rejects_flag_without_session():
    err = io.StringIO()
    with redirect_stderr(err):
        rc = main(["budget", "--self"])

    assert rc == 2
    assert "missing budget session id" in err.getvalue()


def test_budget_self_approve_rejects_nonexistent_session():
    session = "cli-budget-mistyped-self"
    budget_reset(session)
    err = io.StringIO()

    with redirect_stderr(err):
        rc = main(["budget", session, "add", "3", "--self"])

    assert rc == 1
    assert "self-approve rejected" in err.getvalue()
    assert "no budget state found" in err.getvalue()
    assert budget_read_state(session) == {}


def test_recover_handoff_is_bounded_and_reset_preserves_audit(tmp_path, monkeypatch):
    from agent_rails.core.events import ToolEvent
    from agent_rails.core.state import append_event, read_recent

    monkeypatch.setenv("AGENT_RAILS_STATE_DIR", str(tmp_path))
    sid = "recover-cli"
    append_event(ToolEvent.record(sid, "Bash", {"command": "pytest"}, False))
    log_verdict(sid, "Bash", Verdict(
        BLOCK, "repetition", "blocked", response="tripwire",
        recovery={"detector": "repetition", "signature": "exact-abc"},
    ))

    out = _run(["recover", sid, "handoff"])
    assert "Recent mechanical signatures:" in out
    assert "Relevant ruled-out hypotheses:" in out
    assert "Minimal next action:" in out
    assert "detector: repetition" in out
    assert "exact signature: exact-abc" in out
    assert "git diff --stat" in out
    assert "Fresh session:" in out
    assert f"agent-rails recover {sid} reset" in out

    out = _run(["recover", sid, "reset"])
    assert "detector state cleared" in out
    assert "audit history preserved" in out
    assert read_recent(sid, 8) == []


def test_recover_canonicalizes_untrusted_session_in_output(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RAILS_STATE_DIR", str(tmp_path))
    raw = "bad`\ncommand"
    out = _run(["recover", raw, "handoff"])
    assert raw not in out
    assert "agent-rails recover bad__command reset" in out


def test_pr_create_body_dash_reads_stdin_and_writes_temp_body():
    calls = []

    def fake_create_pr(**kwargs):
        calls.append(kwargs)
        body = kwargs["body_file"].read_text(encoding="utf-8")
        assert body == "PR body from stdin"
        return 0

    original_create_pr = cli_module.create_pr
    original_stdin = sys.stdin
    cli_module.create_pr = fake_create_pr
    sys.stdin = io.StringIO("PR body from stdin")
    try:
        rc = main(["pr-create", "--title", "T", "--body", "-", "--head", "topic"])
    finally:
        cli_module.create_pr = original_create_pr
        sys.stdin = original_stdin


    assert rc == 0
    assert calls[0]["title"] == "T"
    assert calls[0]["head"] == "topic"
    assert not calls[0]["body_file"].exists()


def test_pr_create_body_dash_rejects_oversized_stdin():
    original_limit = cli_module.MAX_STDIN_BODY_BYTES
    original_stdin = sys.stdin
    cli_module.MAX_STDIN_BODY_BYTES = 10
    sys.stdin = io.StringIO("x" * 11)

    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = main(["pr-create", "--title", "T", "--body", "-"])
    finally:
        cli_module.MAX_STDIN_BODY_BYTES = original_limit
        sys.stdin = original_stdin

    assert rc == 2
    assert "stdin PR body exceeds 10 bytes" in buf.getvalue()


def test_pr_create_body_dash_rejects_invalid_stdin_text():
    original_stdin = sys.stdin
    sys.stdin = io.StringIO("\udcff")

    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = main(["pr-create", "--title", "T", "--body", "-"])
    finally:
        sys.stdin = original_stdin

    assert rc == 2
    assert "stdin PR body must be valid UTF-8 text" in buf.getvalue()


def test_ci_failures_rejects_positional_and_flag_pr_together():
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["ci-failures", "12", "--pr", "13"])

    assert rc == 2
    assert "pass exactly one of positional PR, --pr, or --run" in buf.getvalue()


def test_ci_failures_rejects_positional_pr_and_run_together():
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["ci-failures", "12", "--run", "456"])

    assert rc == 2
    assert "pass exactly one of positional PR, --pr, or --run" in buf.getvalue()


def test_ci_failures_rejects_empty_explicit_pr_or_run():
    for argv in (
        ["ci-failures", "--pr", ""],
        ["ci-failures", "--pr", "   "],
        ["ci-failures", "--run", ""],
        ["ci-failures", "--run", "   "],
        ["ci-failures", ""],
        ["ci-failures", "   "],
    ):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(argv)

        assert rc == 2
        assert "must not be empty" in buf.getvalue()


def test_pr_create_body_rejects_non_stdin_value():
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["pr-create", "--title", "T", "--body", "literal body"])

    assert rc == 2
    assert "--body only supports '-'" in buf.getvalue()


def test_pr_create_body_rejects_empty_value():
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["pr-create", "--title", "T", "--body", ""])

    assert rc == 2
    assert "--body only supports '-'" in buf.getvalue()


def test_ci_status_json_emits_only_versioned_lifecycle(monkeypatch):
    from agent_rails.workflows import LifecycleResult

    def fake_ci_status(pr, **kwargs):
        print("human text that JSON mode must suppress")
        kwargs["outcome"].append(LifecycleResult(
            1, "ci_status", "pending", 0, total=2, pending=1,
        ))
        return 0

    monkeypatch.setattr(cli_module, "ci_status", fake_ci_status)
    out = _run(["ci-status", "12", "--json"])
    payload = __import__("json").loads(out)
    assert payload["schema_version"] == 1
    assert payload["state"] == "pending"
    assert "human text" not in out


def test_pr_create_json_reports_malformed_input_as_failed():
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["pr-create", "--title", "T", "--body", "literal", "--json"])
    payload = __import__("json").loads(buf.getvalue())
    assert rc == 2
    assert payload["operation"] == "pr_create"
    assert payload["state"] == "failed"
    assert payload["exit_code"] == 2
    assert payload["resumable"] is True
    assert payload["next_action"]


def test_pr_merge_json_reports_merged(monkeypatch):
    monkeypatch.setattr(cli_module, "_cmd_pr_merge_text", lambda args: 0)
    out = _run(["pr-merge", "12", "--json"])
    payload = __import__("json").loads(out)
    assert payload["operation"] == "pr_merge"
    assert payload["state"] == "merged"


def test_pr_merge_json_interruption_is_explicit_and_resumable(monkeypatch):
    def interrupt(args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_module, "_cmd_pr_merge_text", interrupt)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["pr-merge", "12", "--json"])
    payload = __import__("json").loads(buf.getvalue())
    assert rc == 130
    assert payload["state"] == "interrupted"
    assert "safe to rerun" in payload["detail"]


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _run import run_module_tests
    sys.exit(run_module_tests(globals()))
