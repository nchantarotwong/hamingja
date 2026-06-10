"""CLI tests — report/status/version over the same core the hooks use."""
import io
import os
import sys
import tempfile
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp(prefix="agent-rails-cli-")
os.environ["AGENT_RAILS_STATE_DIR"] = _TMP

from agent_rails.cli import build_parser, main  # noqa: E402
import agent_rails.cli as cli_module  # noqa: E402
from agent_rails.core.audit import clear_audit, log_verdict  # noqa: E402
from agent_rails.detectors.base import BLOCK, NUDGE, Verdict  # noqa: E402


def _run(argv) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    assert rc == 0
    return buf.getvalue()


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
        ["pr-create", "--title", "T", "--body-file", "body.md", "--head", "topic"],
        ["pr-create", "--title", "T", "--body", "-", "--head", "topic"],
        ["post-merge-cleanup", "topic", "--force-delete", "--dry-run"],
        ["ci-status", "12", "--command-timeout", "10"],
        ["ci-failures", "12", "--command-timeout", "10"],
        ["ci-failures", "--run", "456", "--command-timeout", "10"],
        ["test-summary", ".pytest_output.log"],
    ]
    for argv in cases:
        args = parser.parse_args(argv)
        assert args.command == argv[0]
        assert callable(args.func)


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


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _run import run_module_tests
    sys.exit(run_module_tests(globals()))
