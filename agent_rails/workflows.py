"""Deterministic workflow wrappers for local agent work.

These helpers keep poll loops, branch cleanup, CI status collection, and test
log extraction out of the model's reasoning loop. They are deliberately small
and dependency-free: external tools may fail, but failure produces a concise
summary instead of another round of manual probing.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Sequence


@dataclass
class RunResult:
    args: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""


Runner = Callable[[Sequence[str]], RunResult]
DEFAULT_COMMAND_TIMEOUT_S = 30.0
TRANSIENT_GH_FAILURE_MARKERS = (
    "http 401",
    "http 502",
    "http 503",
    "http 504",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "timed out",
    "timeout",
    "connection reset",
    "connection refused",
    "tls handshake timeout",
)
CI_BUDGET_EXHAUSTED_PATTERNS = (
    re.compile(r"github actions (budget|quota|minutes|billing) (is |has been |was )?(exhausted|exceeded)", re.I),
    re.compile(r"github actions .{0,80}(spending limit|included minutes) .{0,80}(reached|exhausted|exceeded)", re.I),
    re.compile(r"(actions|workflow) minutes (are |have been |were )?(exhausted|exceeded)", re.I),
    re.compile(r"(spending limit|included minutes) .{0,80}(reached|exhausted|exceeded) .{0,80}(github actions|actions)", re.I),
)
NO_STEP_FAILURE_MAX_SECONDS = 10.0


def default_runner(args: Sequence[str], *, timeout_s: float = DEFAULT_COMMAND_TIMEOUT_S) -> RunResult:
    try:
        cp = subprocess.run(
            list(args),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout_s,
        )
        return RunResult(list(args), cp.returncode, cp.stdout, cp.stderr)
    except FileNotFoundError as e:
        return RunResult(list(args), 127, "", str(e))
    except subprocess.TimeoutExpired as e:
        return RunResult(
            list(args),
            124,
            (e.stdout or "") if isinstance(e.stdout, str) else "",
            f"timed out after {timeout_s:g}s",
        )
    except Exception as e:
        return RunResult(list(args), 1, "", f"{type(e).__name__}: {e}")


def timed_runner(timeout_s: float) -> Runner:
    def _run(args: Sequence[str]) -> RunResult:
        return default_runner(args, timeout_s=timeout_s)

    return _run


def _json_from(result: RunResult):
    try:
        return json.loads(result.stdout or "null")
    except Exception:
        return None


def _json_object_from(result: RunResult) -> Optional[dict]:
    value = _json_from(result)
    return value if isinstance(value, dict) else None


def _err(result: RunResult) -> str:
    msg = (result.stderr or result.stdout or "").strip()
    cmd = " ".join(result.args)
    return f"{cmd} failed ({result.returncode})" + (f": {msg}" if msg else "")


def _is_transient_gh_failure(result: RunResult) -> bool:
    if result.returncode == 124:
        return True
    text = f"{result.stderr}\n{result.stdout}".lower()
    return any(marker in text for marker in TRANSIENT_GH_FAILURE_MARKERS)


def _gh_pr_view(
    pr: str,
    fields: str,
    *,
    runner: Runner,
    sleeper: Callable[[float], None],
    poll_s: float,
    attempts: int = 3,
) -> RunResult:
    attempts = max(1, attempts)
    result = RunResult(["gh", "pr", "view", pr, "--json", fields], 1, "", "not run")
    for index in range(attempts):
        result = runner(["gh", "pr", "view", pr, "--json", fields])
        if result.returncode == 0:
            return result
        if index == attempts - 1 or not _is_transient_gh_failure(result):
            return result
        sleeper(min(max(poll_s, 0), 1.0))
    return result


def _print_lines(lines: list[str]) -> None:
    print("\n".join(lines))


def create_pr(
    *,
    title: str,
    body_file: Path,
    base: str = "main",
    head: Optional[str] = None,
    remote: str = "origin",
    draft: bool = False,
    runner: Runner = default_runner,
) -> int:
    """Create a PR using --body-file so shell quoting cannot mangle the body."""
    lines = ["pr create"]
    if not title.strip():
        lines.append("- error: --title cannot be empty")
        _print_lines(lines)
        return 2
    try:
        if not body_file.is_file():
            lines.append(f"- error: body file not found: {body_file}")
            _print_lines(lines)
            return 1
    except OSError as e:
        lines.append(f"- error: could not inspect body file {body_file}: {e}")
        _print_lines(lines)
        return 1

    if not head:
        branch = _git_current_branch(runner)
        if not branch:
            lines.append("- error: could not determine current branch; pass --head")
            _print_lines(lines)
            return 1
        if branch == base:
            lines.append(f"- error: refusing to create a PR from base branch {base}; checkout a topic branch or pass --head")
            _print_lines(lines)
            return 1
        if not _valid_remote_name(remote):
            lines.append("- error: --remote must be a non-option remote name")
            _print_lines(lines)
            return 2
        upstream = runner(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
        if upstream.returncode != 0:
            refspec = f"HEAD:refs/heads/{branch}"
            pushed = runner(["git", "push", "-u", remote, refspec])
            if pushed.returncode != 0:
                lines.append(f"- error: {_err(pushed)}")
                _print_lines(lines)
                return pushed.returncode or 1
            lines.append(f"- ok: git push -u {remote} {refspec}")
        else:
            upstream_name = upstream.stdout.strip() or "@{u}"
            counts = runner(["git", "rev-list", "--left-right", "--count", "@{u}...HEAD"])
            if counts.returncode != 0:
                lines.append(f"- error: {_err(counts)}")
                _print_lines(lines)
                return counts.returncode or 1
            parsed = _parse_ahead_behind(counts.stdout)
            if parsed is None:
                lines.append(f"- error: could not parse upstream ahead/behind counts: {counts.stdout.strip()!r}")
                _print_lines(lines)
                return 1
            behind, ahead = parsed
            if behind:
                lines.append(f"- error: branch is behind or diverged from {upstream_name}; pull/rebase before creating a PR")
                _print_lines(lines)
                return 1
            if ahead:
                upstream_remote = runner(["git", "config", "--get", f"branch.{branch}.remote"])
                upstream_merge = runner(["git", "config", "--get", f"branch.{branch}.merge"])
                push_target = _push_target(upstream_remote.stdout, upstream_merge.stdout)
                if upstream_remote.returncode != 0 or upstream_merge.returncode != 0 or push_target is None:
                    lines.append("- error: could not determine configured upstream push target")
                    _print_lines(lines)
                    return 1
                push_remote, push_ref = push_target
                pushed = runner(["git", "push", push_remote, f"HEAD:{push_ref}"])
                if pushed.returncode != 0:
                    lines.append(f"- error: {_err(pushed)}")
                    _print_lines(lines)
                    return pushed.returncode or 1
                lines.append(f"- ok: git push {push_remote} HEAD:{push_ref}")

    cmd = [
        "gh",
        "pr",
        "create",
        "--title",
        title,
        "--body-file",
        str(body_file),
        "--base",
        base,
    ]
    if head:
        cmd.extend(["--head", head])
    if draft:
        cmd.append("--draft")
    res = runner(cmd)
    if res.returncode != 0:
        lines.append(f"- error: {_err(res)}")
        _print_lines(lines)
        return res.returncode or 1
    url = (res.stdout or "").strip()
    lines.append("- ok: gh pr create --body-file")
    if url:
        lines.append(f"- url: {url}")
    _print_lines(lines)
    return 0


def _git_current_branch(runner: Runner) -> Optional[str]:
    res = runner(["git", "branch", "--show-current"])
    if res.returncode != 0:
        return None
    branch = res.stdout.strip()
    return branch or None


def _valid_remote_name(remote: str) -> bool:
    return bool(remote.strip()) and not remote.startswith("-")


def _parse_ahead_behind(text: str) -> Optional[tuple[int, int]]:
    parts = text.split()
    if len(parts) != 2:
        return None
    try:
        behind = int(parts[0])
        ahead = int(parts[1])
    except ValueError:
        return None
    if behind < 0 or ahead < 0:
        return None
    return behind, ahead


def _push_target(remote_text: str, merge_text: str) -> Optional[tuple[str, str]]:
    remote = remote_text.strip()
    merge_ref = merge_text.strip()
    if not _valid_remote_name(remote):
        return None
    if not merge_ref.startswith("refs/heads/") or merge_ref == "refs/heads/":
        return None
    return remote, merge_ref


def _git_branch_exists(branch: str, runner: Runner) -> bool:
    res = runner(["git", "rev-parse", "--verify", branch])
    return res.returncode == 0


def cleanup_after_merge(
    branch: Optional[str] = None,
    *,
    main_branch: str = "main",
    remote: str = "origin",
    runner: Runner = default_runner,
    dry_run: bool = False,
    force_delete: bool = False,
) -> int:
    """Switch to main, fast-forward it, and delete the merged local branch."""
    rc, lines = _cleanup_after_merge(
        branch,
        main_branch=main_branch,
        remote=remote,
        runner=runner,
        dry_run=dry_run,
        force_delete=force_delete,
    )
    _print_lines(lines)
    return rc


def _cleanup_after_merge(
    branch: Optional[str],
    *,
    main_branch: str,
    remote: str,
    runner: Runner,
    dry_run: bool,
    force_delete: bool,
) -> tuple[int, list[str]]:
    original = branch or _git_current_branch(runner)
    target = branch or original
    lines = ["post-merge cleanup"]

    if not target:
        lines.append("- error: could not determine branch; pass --branch")
        return 1, lines
    if target == main_branch:
        lines.append(f"- error: refusing to delete {main_branch}; pass --branch for the merged topic branch")
        return 1, lines

    planned = [
        ["git", "checkout", main_branch],
        ["git", "pull", "--ff-only", remote, main_branch],
    ]
    delete_flag = "-D" if force_delete else "-d"
    if dry_run:
        planned.append(["git", "branch", delete_flag, target])
        lines.extend("- would run: " + " ".join(cmd) for cmd in planned)
        return 0, lines

    if _git_branch_exists(target, runner):
        planned.append(["git", "branch", delete_flag, target])
    else:
        lines.append(f"- local branch already absent: {target}")

    for cmd in planned:
        res = runner(cmd)
        if res.returncode != 0:
            lines.append(f"- error: {_err(res)}")
            return res.returncode or 1, lines
        lines.append("- ok: " + " ".join(cmd))
    return 0, lines


def merge_pr(
    pr: str,
    *,
    method: str = "squash",
    cleanup: bool = True,
    main_branch: str = "main",
    remote: str = "origin",
    timeout_s: int = 120,
    poll_s: float = 5,
    ci_timeout_s: int = 1800,
    ci_poll_s: float = 30,
    skip_ci_reason: Optional[str] = None,
    runner: Runner = default_runner,
    sleeper: Callable[[float], None] = time.sleep,
) -> int:
    """Gate on CI, merge a PR via gh, wait for MERGED, then clean local state."""
    lines = [f"pr merge {pr}"]
    view = _gh_pr_view(pr, "headRefName,state,url", runner=runner, sleeper=sleeper, poll_s=poll_s)
    if view.returncode != 0:
        lines.append(f"- error: {_err(view)}")
        _print_lines(lines)
        return view.returncode or 1
    info = _json_object_from(view)
    if not _valid_pr_view(info, require_state=True, require_head=True):
        lines.append("- error: gh returned malformed PR data")
        _print_lines(lines)
        return 1
    branch = info.get("headRefName")

    flag = {"squash": "--squash", "merge": "--merge", "rebase": "--rebase"}.get(method)
    if flag is None:
        lines.append(f"- error: unknown merge method {method!r}")
        _print_lines(lines)
        return 2

    if skip_ci_reason:
        lines.append("- ci gate SKIPPED")
        lines.append(f"- local validation override: {skip_ci_reason}")
    else:
        gate_rc = _ci_gate(
            pr,
            lines,
            ci_timeout_s=ci_timeout_s,
            ci_poll_s=ci_poll_s,
            runner=runner,
            sleeper=sleeper,
        )
        if gate_rc != 0:
            _print_lines(lines)
            return gate_rc

    state = None
    merge = runner(["gh", "pr", "merge", pr, flag, "--delete-branch"])
    if merge.returncode != 0:
        lines.append(f"- warning: merge command failed: {_err(merge)}")
        recovered = _gh_pr_view(pr, "state,url", runner=runner, sleeper=sleeper, poll_s=poll_s)
        if recovered.returncode == 0:
            recovered_info = _json_object_from(recovered)
            if not _valid_pr_view(recovered_info, require_state=True):
                lines.append("- error: gh returned malformed PR data")
                _print_lines(lines)
                return 1
            state = recovered_info.get("state")
        else:
            state = None
        if state != "MERGED":
            lines.append(f"- error: PR state after merge failure is {state or 'unknown'}")
            _print_lines(lines)
            return merge.returncode or 1
        lines.append("- recovered: PR is already MERGED")
    else:
        lines.append("- merge command accepted")

    deadline = time.monotonic() + max(0, timeout_s)
    while True:
        if state == "MERGED":
            lines.append("- state: MERGED")
            break
        poll = _gh_pr_view(pr, "state,url", runner=runner, sleeper=sleeper, poll_s=poll_s)
        if poll.returncode != 0:
            lines.append(f"- error: could not poll PR state: {_err(poll)}")
            _print_lines(lines)
            return poll.returncode or 1
        poll_info = _json_object_from(poll)
        if not _valid_pr_view(poll_info, require_state=True):
            lines.append("- error: gh returned malformed PR data")
            _print_lines(lines)
            return 1
        state = poll_info.get("state")
        if state == "MERGED":
            lines.append("- state: MERGED")
            break
        if time.monotonic() >= deadline:
            lines.append(f"- timeout: PR state is {state or 'unknown'}")
            _print_lines(lines)
            return 1
        sleeper(poll_s)

    if cleanup and branch and state == "MERGED":
        cleanup_rc, cleanup_lines = _cleanup_after_merge(
            branch,
            main_branch=main_branch,
            remote=remote,
            runner=runner,
            dry_run=False,
            force_delete=method in {"squash", "rebase"},
        )
        lines.extend(cleanup_lines)
        _print_lines(lines)
        return cleanup_rc
    _print_lines(lines)
    return 0


FAILING_CHECK_STATES = {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED"}
# NEUTRAL is terminal and counts as passing, matching GitHub's own
# required-check semantics (neutral/skipped do not block a merge).
TERMINAL_CHECK_STATES = {"SUCCESS", "FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "SKIPPED", "NEUTRAL"}
WORKFLOW_FILE_SUFFIXES = {".yml", ".yaml"}
NO_CHECKS_GRACE_SECONDS = 60


def _fetch_checks(pr: Optional[str], *, runner: Runner):
    """Fetch PR checks via gh. Returns (checks, error) — exactly one is None.

    gh exits 0 with --json even when checks are failing/pending, so a
    nonzero exit here means gh itself failed, not that checks failed.
    """
    cmd = ["gh", "pr", "checks"]
    if pr:
        cmd.append(pr)
    cmd.extend(["--json", "name,state,link"])
    res = runner(cmd)
    if res.returncode != 0:
        return None, _err(res)
    checks = _json_from(res)
    if not isinstance(checks, list):
        return None, "gh returned unparseable check data"
    if not all(_valid_check_shape(check) for check in checks):
        return None, "gh returned malformed check data"
    return checks, None


def _classify_checks(checks: list[dict]):
    """Split checks into (failing, pending); the rest are terminal successes/skips."""
    failing = [
        c for c in checks
        if ((c.get("conclusion") or c.get("state") or "").upper() in FAILING_CHECK_STATES)
    ]
    pending = [
        c for c in checks
        if c not in failing and (c.get("state") or "").upper() not in TERMINAL_CHECK_STATES
    ]
    return failing, pending


def _checks_fingerprint(checks: list[dict]) -> tuple[tuple[str, str], ...]:
    """Stable identity for the reported check set, excluding volatile URLs."""
    return tuple(sorted(
        (
            str(c.get("name") or ""),
            str(c.get("conclusion") or c.get("state") or "").upper(),
        )
        for c in checks
    ))


def _check_line(prefix: str, check: dict) -> str:
    link = check.get("link") or ""
    suffix = f" ({link})" if link else ""
    status = check.get("conclusion") or check.get("state") or "?"
    return f"- {prefix}: {check.get('name', '?')} [{status}]" + suffix


def _pr_conflict_reason(pr: str, *, runner: Runner) -> Optional[str]:
    try:
        res = runner(["gh", "pr", "view", pr, "--json", "mergeStateStatus,mergeable"])
    except Exception:
        return None
    if res.returncode != 0:
        return None
    info = _json_object_from(res)
    if not isinstance(info, dict):
        return None
    merge_state = info.get("mergeStateStatus")
    mergeable = info.get("mergeable")
    if merge_state == "DIRTY" or mergeable == "CONFLICTING":
        return "This branch has conflicts that must be resolved before merging"
    return None


def ci_status(
    pr: Optional[str] = None,
    *,
    runner: Runner = default_runner,
    wait_timeout_s: int = 0,
    poll_s: float = 10,
    sleeper: Callable[[float], None] = time.sleep,
) -> int:
    deadline = time.monotonic() + max(0, wait_timeout_s)
    no_checks_deadline = time.monotonic() + min(max(0, wait_timeout_s), NO_CHECKS_GRACE_SECONDS)
    backoff = max(0.1, poll_s)
    printed_header = False
    timed_out_wait = False
    repo_has_no_ci_workflows: Optional[bool] = None
    if wait_timeout_s > 0:
        print("ci status", flush=True)
        printed_header = True
    while True:
        checks, error = _fetch_checks(pr, runner=runner)
        if error is not None and wait_timeout_s > 0 and _no_checks_reported(error):
            now = time.monotonic()
            if now >= no_checks_deadline:
                if repo_has_no_ci_workflows is None:
                    repo_has_no_ci_workflows = _repo_has_no_ci_workflows()
                if repo_has_no_ci_workflows:
                    if not printed_header:
                        print("ci status")
                    print("- checks: 0 total, 0 failing, 0 pending")
                    print("- no GitHub Actions workflows found locally; treating missing checks as no CI")
                    return 0
            if now < deadline:
                sleep_s = min(backoff, max(0, deadline - now))
                print(
                    f"- waiting: no checks reported yet; timeout {wait_timeout_s}s; next poll in {sleep_s:g}s",
                    flush=True,
                )
                sleeper(sleep_s)
                backoff = min(backoff * 2, 60.0)
                continue
        if error is not None:
            if not printed_header:
                print("ci status")
            print(f"- error: {error}")
            return 1
        failing, pending = _classify_checks(checks)
        if wait_timeout_s <= 0 or failing or not pending or time.monotonic() >= deadline:
            timed_out_wait = wait_timeout_s > 0 and bool(pending) and not failing and time.monotonic() >= deadline
            break
        sleep_s = min(backoff, max(0, deadline - time.monotonic()))
        print(
            f"- waiting: {len(pending)}/{len(checks)} checks still running; timeout {wait_timeout_s}s; next poll in {sleep_s:g}s",
            flush=True,
        )
        sleeper(sleep_s)
        backoff = min(backoff * 2, 60.0)
    failing, pending = _classify_checks(checks)
    ci_block = _ci_blocked(failing, runner=runner)
    if not printed_header:
        print("ci status")
    print(f"- checks: {len(checks)} total, {len(failing)} failing, {len(pending)} pending")
    if timed_out_wait:
        print(f"- timeout: {len(pending)} check(s) still pending after {wait_timeout_s}s")
    if ci_block:
        print(f"- blocked: {ci_block}")
    for c in failing[:20]:
        print(_check_line("failed", c))
    if ci_block:
        return 2
    if timed_out_wait:
        return 1
    return 1 if failing else 0


def _no_checks_reported(error: str) -> bool:
    return "no checks reported" in error


def ci_preflight(pr: Optional[str] = None, *, runner: Runner = default_runner) -> int:
    """Cheap CI readiness check that classifies quota/infrastructure blocks."""
    checks, error = _fetch_checks(pr, runner=runner)
    print("ci preflight")
    if error is not None:
        print(f"- error: {error}")
        return 1
    failing, pending = _classify_checks(checks)
    ci_block = _ci_blocked(failing, runner=runner)
    print(f"- checks: {len(checks)} total, {len(failing)} failing, {len(pending)} pending")
    if ci_block:
        print(f"- blocked: {ci_block}")
        print("- next: confirm GitHub Actions budget/availability before rerunning CI")
        return 2
    if failing:
        print("- result: failing checks look like ordinary CI failures")
        return 1
    if pending:
        print("- result: checks are pending")
        return 1
    print("- result: CI is ready")
    return 0


def _ci_gate(
    pr: str,
    lines: list[str],
    *,
    ci_timeout_s: int,
    ci_poll_s: float,
    runner: Runner,
    sleeper: Callable[[float], None],
) -> int:
    """Refuse to merge until every CI check on the PR has passed.

    Returns 0 to proceed with the merge, nonzero to refuse. Fail-closed:
    unknown CI state (gh failure, malformed data, no checks reported,
    still pending at the deadline) refuses rather than merges.
    """
    deadline = time.monotonic() + max(0, ci_timeout_s)
    # A PR whose checks haven't been reported yet looks identical to a repo
    # with no CI at all; give the former a short window to appear, then
    # refuse so the latter gets a fast, explicit answer instead of a
    # ci_timeout_s-long hang.
    no_checks_deadline = time.monotonic() + min(max(0, ci_timeout_s), NO_CHECKS_GRACE_SECONDS)
    waiting_logged = False
    passed_fingerprint: Optional[tuple[tuple[str, str], ...]] = None
    while True:
        checks, error = _fetch_checks(pr, runner=runner)
        if error is not None:
            # gh exits 1 with this message instead of returning [] when a PR
            # has no checks — same situation as an empty list, so give it the
            # same grace window (checks may simply not be reported yet).
            if "no checks reported" in error:
                checks = []
            else:
                lines.append(f"- error: {error}")
                lines.append("- refusing to merge: CI state unknown; retry, or pass --skip-ci-reason with local validation")
                return 1
        failing, pending = _classify_checks(checks)
        if failing:
            lines.append(f"- ci: {len(checks)} checks, {len(failing)} failing, {len(pending)} pending")
            ci_block = _ci_blocked(failing, runner=runner)
            if ci_block:
                lines.append(f"- blocked: {ci_block}")
                lines.append("- refusing to merge: CI appears unavailable; confirm GitHub Actions budget/availability before rerunning")
                return 2
            for c in failing[:20]:
                lines.append(_check_line("failed", c))
            lines.append("- refusing to merge: CI checks are failing; fix CI, or pass --skip-ci-reason with local validation")
            return 1
        if checks and not pending:
            fingerprint = _checks_fingerprint(checks)
            if passed_fingerprint == fingerprint:
                lines.append(f"- ci: all {len(checks)} checks passed")
                return 0
            passed_fingerprint = fingerprint
            lines.append(f"- ci: all {len(checks)} reported checks passed; verifying stable check set")
            sleeper(min(max(ci_poll_s, 0), 5))
            continue
        passed_fingerprint = None
        now = time.monotonic()
        if not checks and now >= no_checks_deadline:
            conflict_reason = _pr_conflict_reason(pr, runner=runner)
            if conflict_reason:
                lines.append(f"- mergeability: {conflict_reason}")
                lines.append("- refusing to merge: resolve branch conflicts, then rerun the wrapper")
                return 1
            if _repo_has_no_ci_workflows():
                lines.append("- ci gate SKIPPED")
                lines.append("- no GitHub Actions workflows found locally; treating missing checks as no CI")
                return 0
            lines.append("- refusing to merge: no CI checks reported for this PR; if this repo has no CI, pass --skip-ci-reason")
            return 1
        if now >= deadline:
            lines.append(f"- ci: {len(pending)} check(s) still pending after {ci_timeout_s}s")
            for c in pending[:20]:
                lines.append(_check_line("pending", c))
            lines.append("- refusing to merge: CI did not finish in time; raise --ci-timeout or rerun later")
            return 1
        if not waiting_logged and pending:
            lines.append(f"- ci: waiting for {len(pending)} pending check(s)")
            waiting_logged = True
        sleeper(ci_poll_s)


def _repo_has_no_ci_workflows(root: Optional[Path] = None) -> bool:
    """True only when local repo inspection proves no Actions workflows exist."""
    try:
        base = root or Path(os.getcwd())
        found_repo_marker = False
        for cur in (base, *base.parents):
            if (cur / ".git").exists() or (cur / ".github").exists():
                base = cur
                found_repo_marker = True
                break
        if not found_repo_marker:
            return False
        workflows = base / ".github" / "workflows"
        if not workflows.exists():
            return True
        if not workflows.is_dir():
            return False
        for child in workflows.iterdir():
            if child.is_file() and child.suffix.lower() in WORKFLOW_FILE_SUFFIXES:
                return False
        return True
    except Exception:
        return False


def _ci_blocked(failing_checks: list[dict], *, runner: Runner) -> Optional[str]:
    run_ids: list[str] = []
    for check in failing_checks:
        link = check.get("link")
        if not isinstance(link, str):
            continue
        match = re.search(r"/actions/runs/(\d+)", link)
        if match and match.group(1) not in run_ids:
            run_ids.append(match.group(1))
    for run_id in run_ids:
        log = runner(["gh", "run", "view", run_id, "--log-failed"])
        if log.returncode == 0:
            lines = f"{log.stdout}\n{log.stderr}".splitlines()
            if any(_ci_budget_line_blocked(line) for line in lines):
                return "actions_budget_exhausted"
        else:
            metadata_block = _ci_metadata_blocked(run_id, runner=runner)
            if metadata_block:
                return metadata_block
    return None


def _ci_metadata_blocked(run_id: str, *, runner: Runner) -> Optional[str]:
    meta = runner(["gh", "run", "view", run_id, "--json", "status,conclusion,jobs,url,workflowName"])
    if meta.returncode != 0:
        return None
    info = _json_object_from(meta)
    if not isinstance(info, dict):
        return None
    jobs = info.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        return None
    failed_jobs = [job for job in jobs if isinstance(job, dict) and job.get("conclusion") == "failure"]
    if not failed_jobs or len(failed_jobs) != len(jobs):
        return None
    if all(_job_failed_before_steps(job) for job in failed_jobs):
        return "ci_infrastructure_or_budget_failure"
    return None


def _job_failed_before_steps(job: dict) -> bool:
    if job.get("steps") != []:
        return False
    started = _parse_gh_time(job.get("startedAt"))
    completed = _parse_gh_time(job.get("completedAt"))
    if started is None or completed is None:
        return False
    duration = (completed - started).total_seconds()
    return 0 <= duration <= NO_STEP_FAILURE_MAX_SECONDS


def _parse_gh_time(value) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _ci_budget_line_blocked(line: str) -> bool:
    text = _log_message(line).strip()
    if not text or text.startswith(("FAILED ", "ERROR ", "E   ")):
        return False
    return any(pattern.search(text) for pattern in CI_BUDGET_EXHAUSTED_PATTERNS)


def _valid_check_shape(value) -> bool:
    if not isinstance(value, dict):
        return False
    if not _non_empty_string(value.get("name")):
        return False
    if not _non_empty_string(value.get("state")):
        return False
    for field in ("name", "state", "conclusion", "link"):
        item = value.get(field)
        if item is not None and not isinstance(item, str):
            return False
    return True


def _string_or_none(value) -> bool:
    return value is None or isinstance(value, str)


def _non_empty_string(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_run_id(value) -> bool:
    if isinstance(value, int) and not isinstance(value, bool):
        return value > 0
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip()) > 0
    return False


def _valid_pr_view(
    value: Optional[dict],
    *,
    require_state: bool = False,
    require_head: bool = False,
) -> bool:
    if value is None:
        return False
    head = value.get("headRefName")
    if require_head:
        if not _non_empty_string(head):
            return False
    elif not _string_or_none(head):
        return False
    state = value.get("state")
    if require_state:
        if not _non_empty_string(state):
            return False
    elif not _string_or_none(state):
        return False
    if not _string_or_none(value.get("url")):
        return False
    return True


def ci_failures(
    *,
    pr: Optional[str] = None,
    run_id: Optional[str] = None,
    runner: Runner = default_runner,
) -> int:
    if run_id is None:
        branch = None
        if pr:
            view = runner(["gh", "pr", "view", pr, "--json", "headRefName"])
            if view.returncode != 0:
                print("ci failures")
                print(f"- error: {_err(view)}")
                return view.returncode or 1
            view_info = _json_object_from(view)
            if view_info is None:
                print("ci failures")
                print("- error: gh returned malformed PR data")
                return 1
            branch = view_info.get("headRefName")
            if not _non_empty_string(branch):
                print("ci failures")
                print("- error: gh returned malformed PR data")
                return 1
        cmd = ["gh", "run", "list", "--limit", "1", "--json", "databaseId,conclusion,status,workflowName,url"]
        if branch:
            cmd.extend(["--branch", branch])
        res = runner(cmd)
        if res.returncode != 0:
            print("ci failures")
            print(f"- error: {_err(res)}")
            return res.returncode or 1
        runs = _json_from(res)
        if not isinstance(runs, list) or not runs:
            print("ci failures")
            print("- no workflow runs found")
            return 0
        run = runs[0]
        if not isinstance(run, dict) or not _valid_run_id(run.get("databaseId")):
            print("ci failures")
            print("- error: gh returned malformed run data")
            return 1
        if not _string_or_none(run.get("workflowName")) or not _string_or_none(run.get("url")):
            print("ci failures")
            print("- error: gh returned malformed run data")
            return 1
        run_id = str(run.get("databaseId") or "")
        title = run.get("workflowName") or "workflow"
        url = run.get("url") or ""
        if branch:
            title = f"{title} ({branch})"
    else:
        meta = runner(["gh", "run", "view", run_id, "--json", "workflowName,url"])
        if meta.returncode == 0:
            info = _json_object_from(meta)
            if info is None:
                print("ci failures")
                print("- error: gh returned malformed run data")
                return 1
        else:
            info = {}
        if not _string_or_none(info.get("workflowName")) or not _string_or_none(info.get("url")):
            print("ci failures")
            print("- error: gh returned malformed run data")
            return 1
        title = (info or {}).get("workflowName") or "workflow"
        url = (info or {}).get("url") or ""

    log = runner(["gh", "run", "view", run_id, "--log-failed"])
    print("ci failures")
    print(f"- run: {title} #{run_id}" + (f" ({url})" if url else ""))
    if log.returncode != 0:
        metadata_block = _ci_metadata_blocked(run_id, runner=runner)
        if metadata_block:
            print(f"- blocked: {metadata_block}")
            print("- failed logs were unavailable, but run metadata shows jobs failed before any steps ran")
            return 2
        print(f"- error: {_err(log)}")
        return log.returncode or 1
    summary = summarize_pytest_log(f"{log.stdout}\n{log.stderr}")
    if not _test_summary_has_signal(summary):
        fallback = _failed_job_log_summary(run_id, runner=runner)
        if fallback is not None and _test_summary_has_signal(fallback):
            print("- fallback: scanned failed job logs")
            _emit_test_summary(fallback)
            return 1
        print("- failed log fetched, but no pytest-style failures were extracted")
        return 1
    _emit_test_summary(summary)
    return 1


def _test_summary_has_signal(summary: TestSummary) -> bool:
    return bool(summary.failures or summary.errors or summary.trace_lines)


def _failed_job_log_summary(run_id: str, *, runner: Runner) -> Optional[TestSummary]:
    """Fetch raw logs for failed jobs in one run and summarize pytest failures.

    This is deliberately scoped to the run id `ci_failures` already selected,
    then narrowed again to jobs whose metadata says `conclusion == failure`.
    It is a fallback for cases where `gh run view --log-failed` returns wrapper
    metadata but omits the raw test output we need.
    """
    meta = runner(["gh", "run", "view", run_id, "--json", "jobs"])
    if meta.returncode != 0:
        return None
    info = _json_object_from(meta)
    if info is None:
        return None
    jobs = info.get("jobs")
    if not isinstance(jobs, list):
        return None

    chunks: list[str] = []
    for job in jobs:
        if not isinstance(job, dict) or job.get("conclusion") != "failure":
            continue
        job_id = job.get("databaseId")
        if not _valid_run_id(job_id):
            continue
        job_log = runner(["gh", "run", "view", run_id, "--job", str(job_id), "--log"])
        if job_log.returncode != 0:
            continue
        chunks.append(f"{job_log.stdout}\n{job_log.stderr}")
    if not chunks:
        return None
    return summarize_pytest_log("\n".join(chunks))


@dataclass
class TestSummary:
    failures: list[str]
    errors: list[str]
    trace_lines: list[str]
    final_line: Optional[str]


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_GHA_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\s+")
_FAILED_RE = re.compile(r"^(FAILED|ERROR)\s+(\S+)(?:\s+-\s+(.*))?$")
_TRACE_RE = re.compile(r"^\s*(E\s+.+|AssertionError\b.*|[A-Za-z_][\w.]*Error: .+)$")
_FINAL_RE = re.compile(r"=+\s+.*(?:failed|error|passed|skipped|xfailed|xpassed).*\s+in\s+[\d.]+s\s+=+", re.I)


def summarize_pytest_log(text: str, *, max_items: int = 20) -> TestSummary:
    failures: list[str] = []
    errors: list[str] = []
    trace_lines: list[str] = []
    final_line: Optional[str] = None
    for raw in text.splitlines():
        line = _log_message(raw.rstrip())
        m = _FAILED_RE.match(line)
        if m:
            bucket = failures if m.group(1) == "FAILED" else errors
            item = m.group(2) + (f" - {m.group(3)}" if m.group(3) else "")
            if item not in bucket and len(bucket) < max_items:
                bucket.append(item)
            continue
        if _FINAL_RE.match(line):
            final_line = line.strip("= ").strip()
            continue
        if len(trace_lines) < max_items and _TRACE_RE.match(line):
            cleaned = line.strip()
            if cleaned not in trace_lines:
                trace_lines.append(cleaned)
    return TestSummary(failures, errors, trace_lines, final_line)


def _log_message(line: str) -> str:
    """Return the likely human log message from plain pytest or gh log output."""
    line = _ANSI_RE.sub("", line).strip()
    if "\t" not in line:
        return _GHA_TIMESTAMP_RE.sub("", line)
    parts = line.split("\t")
    return _GHA_TIMESTAMP_RE.sub("", parts[-1].strip()) if parts else line


def test_summary(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print("test summary")
        print(f"- error: could not read {path}: {e}")
        return 1
    summary = summarize_pytest_log(text)
    print("test summary")
    _emit_test_summary(summary)
    return 1 if summary.failures or summary.errors else 0


def _emit_test_summary(summary: TestSummary) -> None:
    if summary.final_line:
        print(f"- result: {summary.final_line}")
    if summary.failures:
        for item in summary.failures:
            print(f"- failing test: {item}")
    if summary.errors:
        for item in summary.errors:
            print(f"- collection/runtime error: {item}")
    if summary.trace_lines:
        for item in summary.trace_lines[:10]:
            print(f"- error line: {item}")
    if not summary.failures and not summary.errors and not summary.trace_lines:
        print("- no pytest failures detected")
