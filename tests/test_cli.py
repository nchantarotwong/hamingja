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
        ["pr-merge", "12", "--method", "squash", "--command-timeout", "10"],
        ["pr-create", "--title", "T", "--body-file", "body.md", "--head", "topic"],
        ["post-merge-cleanup", "topic", "--force-delete", "--dry-run"],
        ["ci-status", "12", "--command-timeout", "10"],
        ["ci-failures", "--run", "456", "--command-timeout", "10"],
        ["test-summary", ".pytest_output.log"],
    ]
    for argv in cases:
        args = parser.parse_args(argv)
        assert args.command == argv[0]
        assert callable(args.func)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _run import run_module_tests
    sys.exit(run_module_tests(globals()))
