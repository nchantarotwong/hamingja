"""Detector unit tests — synthetic event sequences only.

NEVER replace these with captured real sessions: a real transcript can drag
private repo internals into git history. Detectors are pure functions over
ToolEvents, so synthetic sequences exercise them fully.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_rails.core.events import ERROR, OK, PENDING, ToolEvent  # noqa: E402
from agent_rails.detectors.base import ALLOW, BLOCK, NUDGE  # noqa: E402
from agent_rails.detectors.error_streak import ErrorStreakDetector  # noqa: E402
from agent_rails.detectors.leverage_fallback import LeverageFallbackDetector  # noqa: E402
from agent_rails.detectors.repetition import RepetitionDetector  # noqa: E402
from agent_rails.detectors.workflow_wrapper import WorkflowWrapperDetector  # noqa: E402

CFG = {
    "detectors": {
        "repetition": {"enabled": True, "nudge_at": 3, "block_at": 4},
        "error_streak": {"enabled": True, "nudge_at": 3, "block_at": 6},
        "leverage_fallback": {
            "enabled": True,
            "nudge_at": 1,
            "block_at": 2,
            "lookback": 4,
            "required_patterns": ["semantic-nav"],
            "fallback_patterns": ["grep ", "rg ", "sed ", "awk "],
            "protected_targets": ["src/compiler/main.lang"],
        },
        "workflow_wrapper": {"enabled": True, "nudge_at": 1, "block_at": 2},
    }
}


def ev(tool="Bash", arg="x", status=OK, sid="s"):
    return ToolEvent(sid, tool, arg, status, 0.0)


# --- repetition ---------------------------------------------------------

def test_repetition_blocks_the_fourth_identical_failed_call():
    hist = [ev(arg="a", status=ERROR) for _ in range(3)]
    cand = ev(arg="a", status=PENDING)
    v = RepetitionDetector().evaluate(hist, cand, CFG)
    assert v is not None and v.action == BLOCK


def test_repetition_nudges_on_the_third():
    hist = [ev(arg="a") for _ in range(2)]
    cand = ev(arg="a", status=PENDING)
    v = RepetitionDetector().evaluate(hist, cand, CFG)
    assert v is not None and v.action == NUDGE
    assert "3rd" in v.reason


def test_repetition_ignores_varied_calls():
    hist = [ev(arg=str(i)) for i in range(6)]
    cand = ev(arg="brand-new", status=PENDING)
    assert RepetitionDetector().evaluate(hist, cand, CFG) is None


def test_repetition_distinguishes_by_tool():
    hist = [ev(tool="Bash", arg="a") for _ in range(3)]
    cand = ev(tool="Read", arg="a", status=PENDING)
    assert RepetitionDetector().evaluate(hist, cand, CFG) is None


def test_repetition_exempts_read_only_tools():
    # repeating a read-only tool with identical args is normal, not flailing
    cfg = {"detectors": {"repetition": {
        "enabled": True, "nudge_at": 3, "block_at": 4, "exempt_tools": ["Read"]}}}
    hist = [ev(tool="Read", arg="a") for _ in range(5)]
    cand = ev(tool="Read", arg="a", status=PENDING)
    assert RepetitionDetector().evaluate(hist, cand, cfg) is None
    # a non-exempt tool with the same failed pattern still trips
    hist2 = [ev(tool="Bash", arg="a", status=ERROR) for _ in range(5)]
    cand2 = ev(tool="Bash", arg="a", status=PENDING)
    assert RepetitionDetector().evaluate(hist2, cand2, cfg).action == BLOCK


def test_repetition_blocks_identical_output():
    hist = [ToolEvent("s", "Bash", "a", OK, 0.0, output_hash="same") for _ in range(3)]
    cand = ev(arg="a", status=PENDING)
    v = RepetitionDetector().evaluate(hist, cand, CFG)
    assert v is not None and v.action == BLOCK


def test_repetition_requires_multiple_output_hashes_to_block():
    hist = [
        ToolEvent("s", "Bash", "a", OK, 0.0, output_hash="same"),
        ToolEvent("s", "Bash", "a", OK, 0.0),
        ToolEvent("s", "Bash", "a", OK, 0.0),
    ]
    cand = ev(arg="a", status=PENDING)
    v = RepetitionDetector().evaluate(hist, cand, CFG)
    assert v is not None and v.action == NUDGE


def test_repetition_does_not_block_success_without_output_evidence():
    hist = [ev(arg="a", status=OK) for _ in range(3)]
    cand = ev(arg="a", status=PENDING)
    v = RepetitionDetector().evaluate(hist, cand, CFG)
    assert v is not None and v.action == NUDGE


def test_repetition_ignores_incomplete_payloads():
    hist = [ToolEvent("s", "Bash", "a", OK, 0.0, args_complete=False) for _ in range(5)]
    cand = ToolEvent("s", "Bash", "a", PENDING, 0.0, args_complete=False)
    assert RepetitionDetector().evaluate(hist, cand, CFG) is None


def test_repetition_low_noise_shell_repeat_is_quiet_before_block_threshold():
    hist = [ToolEvent("s", "Bash", "a", OK, 0.0, arg_kind="shell:test", arg_preview="python3 -m pytest") for _ in range(2)]
    cand = ToolEvent("s", "Bash", "a", PENDING, 0.0, arg_kind="shell:test", arg_preview="python3 -m pytest")
    assert RepetitionDetector().evaluate(hist, cand, CFG) is None


def test_repetition_build_repeat_is_quiet_before_block_threshold():
    hist = [
        ToolEvent(
            "s", "Bash", "a", OK, 0.0,
            arg_kind="shell:build",
            arg_preview="bash scripts/rebuild.sh",
        )
        for _ in range(2)
    ]
    cand = ToolEvent(
        "s", "Bash", "a", PENDING, 0.0,
        arg_kind="shell:build",
        arg_preview="bash scripts/rebuild.sh",
    )
    assert RepetitionDetector().evaluate(hist, cand, CFG) is None


def test_repetition_read_only_shell_repeat_nudges_not_blocks_without_output_evidence():
    hist = [ToolEvent("s", "Bash", "a", OK, 0.0, arg_kind="shell:read-only", arg_preview="rg foo") for _ in range(3)]
    cand = ToolEvent("s", "Bash", "a", PENDING, 0.0, arg_kind="shell:read-only", arg_preview="rg foo")
    v = RepetitionDetector().evaluate(hist, cand, CFG)
    assert v is not None and v.action == NUDGE


def test_repetition_respects_disabled():
    cfg = {"detectors": {"repetition": {"enabled": False}}}
    hist = [ev(arg="a") for _ in range(5)]
    cand = ev(arg="a", status=PENDING)
    assert RepetitionDetector().evaluate(hist, cand, cfg) is None


# --- error streak -------------------------------------------------------

def test_error_streak_resets_on_success():
    hist = [ev(status=ERROR) for _ in range(5)] + [ev(status=OK), ev(status=ERROR)]
    assert ErrorStreakDetector().evaluate(hist, None, CFG) is None


def test_error_streak_nudges_at_three():
    hist = [ev(status=ERROR) for _ in range(3)]
    v = ErrorStreakDetector().evaluate(hist, None, CFG)
    assert v is not None and v.action == NUDGE


def test_error_streak_blocks_at_six():
    hist = [ev(status=ERROR) for _ in range(6)]
    v = ErrorStreakDetector().evaluate(hist, None, CFG)
    assert v is not None and v.action == BLOCK


def test_error_streak_clean_history_is_quiet():
    hist = [ev(status=OK) for _ in range(8)]
    assert ErrorStreakDetector().evaluate(hist, None, CFG) is None


# --- leverage fallback --------------------------------------------------

def test_leverage_fallback_blocks_required_tool_failure_to_protected_grep():
    hist = [
        ToolEvent(
            "s", "Bash", "refs", ERROR, 0.0,
            arg_preview="semantic-nav --def parse_expr",
        )
    ]
    cand = ToolEvent(
        "s", "Bash", "grep", PENDING, 0.0,
        arg_preview="grep -n \"parse_expr\" src/compiler/main.lang",
    )
    v = LeverageFallbackDetector().evaluate(hist, cand, CFG)
    assert v is not None and v.action == BLOCK


def test_leverage_fallback_ignores_successful_required_tool_before_grep():
    hist = [
        ToolEvent(
            "s", "Bash", "refs", OK, 0.0,
            arg_preview="semantic-nav --def parse_expr",
        )
    ]
    cand = ToolEvent(
        "s", "Bash", "grep", PENDING, 0.0,
        arg_preview="grep -n \"parse_expr\" src/compiler/main.lang",
    )
    assert LeverageFallbackDetector().evaluate(hist, cand, CFG) is None


def test_leverage_fallback_blocks_inline_or_grep_bypass():
    cand = ToolEvent(
        "s", "Bash", "combo", PENDING, 0.0,
        arg_preview=(
            "semantic-nav parse_expr || "
            "grep -n \"parse_expr\" src/compiler/main.lang"
        ),
    )
    v = LeverageFallbackDetector().evaluate([], cand, CFG)
    assert v is not None and v.action == BLOCK


# --- workflow wrapper ---------------------------------------------------

def test_workflow_wrapper_blocks_raw_gh_pr_checks():
    cand = ToolEvent(
        "s", "Bash", "gh", PENDING, 0.0,
        arg_preview="gh pr checks 193 --json name,state,link",
    )
    v = WorkflowWrapperDetector().evaluate([], cand, CFG)
    assert v is not None and v.action == BLOCK
    assert "agent-rails ci-status" in v.reason


def test_workflow_wrapper_blocks_timeout_prefixed_raw_gh_pr_checks():
    cand = ToolEvent(
        "s", "Bash", "gh", PENDING, 0.0,
        arg_preview="timeout 30s gh pr checks 193 --json name,state,link",
    )
    v = WorkflowWrapperDetector().evaluate([], cand, CFG)
    assert v is not None and v.action == BLOCK
    assert "agent-rails ci-status" in v.reason


def test_workflow_wrapper_allows_quoted_mentions_in_non_gh_command():
    cand = ToolEvent(
        "s", "Bash", "printf", PENDING, 0.0,
        arg_preview=(
            "printf %s "
            "'Validation: use gh pr checks through agent-rails ci-status'"
        ),
    )
    assert WorkflowWrapperDetector().evaluate([], cand, CFG) is None


def test_workflow_wrapper_blocks_raw_gh_pr_create():
    cand = ToolEvent(
        "s", "Bash", "gh", PENDING, 0.0,
        arg_preview="gh pr create --title T --body \"Summary\"",
    )
    v = WorkflowWrapperDetector().evaluate([], cand, CFG)
    assert v is not None and v.action == BLOCK
    assert "agent-rails pr-create" in v.reason


def test_workflow_wrapper_blocks_codex_exec_command_raw_gh_pr_create():
    cand = ToolEvent.candidate(
        "s",
        "functions.exec_command",
        {"cmd": "gh pr create --title T --body-file /tmp/body.md"},
    )
    v = WorkflowWrapperDetector().evaluate([], cand, CFG)
    assert v is not None and v.action == BLOCK
    assert "agent-rails pr-create" in v.reason


def test_workflow_wrapper_blocks_raw_gh_pr_merge():
    cand = ToolEvent(
        "s", "Bash", "gh", PENDING, 0.0,
        arg_preview="gh pr merge 193 --squash",
    )
    v = WorkflowWrapperDetector().evaluate([], cand, CFG)
    assert v is not None and v.action == BLOCK
    assert "agent-rails pr-merge" in v.reason


def test_workflow_wrapper_blocks_raw_gh_run_watch():
    cand = ToolEvent(
        "s", "Bash", "gh", PENDING, 0.0,
        arg_preview="gh run watch 456",
    )
    v = WorkflowWrapperDetector().evaluate([], cand, CFG)
    assert v is not None and v.action == BLOCK
    assert "agent-rails ci-failures" in v.reason


def test_workflow_wrapper_blocks_manual_post_merge_cleanup():
    cand = ToolEvent(
        "s", "Bash", "git", PENDING, 0.0,
        arg_preview="git checkout main && git pull --ff-only && git branch -d topic",
    )
    v = WorkflowWrapperDetector().evaluate([], cand, CFG)
    assert v is not None and v.action == BLOCK
    assert "agent-rails post-merge-cleanup" in v.reason


def test_workflow_wrapper_allows_standalone_checkout_main():
    cand = ToolEvent(
        "s", "Bash", "git", PENDING, 0.0,
        arg_preview="git checkout main",
    )
    assert WorkflowWrapperDetector().evaluate([], cand, CFG) is None


def test_workflow_wrapper_blocks_branch_delete_cleanup():
    cand = ToolEvent(
        "s", "Bash", "git", PENDING, 0.0,
        arg_preview="git branch -d topic",
    )
    v = WorkflowWrapperDetector().evaluate([], cand, CFG)
    assert v is not None and v.action == BLOCK
    assert "agent-rails post-merge-cleanup" in v.reason


def test_workflow_wrapper_allows_explicit_raw_fallback_prefix():
    cand = ToolEvent(
        "s", "Bash", "gh", PENDING, 0.0,
        arg_preview="AGENT_RAILS_ALLOW_RAW=1 gh pr checks 193 --json name,state,link",
    )
    assert WorkflowWrapperDetector().evaluate([], cand, CFG) is None


def test_workflow_wrapper_allows_agent_rails_wrapper():
    cand = ToolEvent(
        "s", "Bash", "rails", PENDING, 0.0,
        arg_preview="agent-rails ci-status 193",
    )
    assert WorkflowWrapperDetector().evaluate([], cand, CFG) is None


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _run import run_module_tests
    sys.exit(run_module_tests(globals()))
