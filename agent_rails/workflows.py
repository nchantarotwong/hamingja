"""Deterministic workflow wrappers for local agent work.

These helpers keep poll loops, branch cleanup, CI status collection, and test
log extraction out of the model's reasoning loop. They are deliberately small
and dependency-free: external tools may fail, but failure produces a concise
summary instead of another round of manual probing.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
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


def _err(result: RunResult) -> str:
    msg = (result.stderr or result.stdout or "").strip()
    cmd = " ".join(result.args)
    return f"{cmd} failed ({result.returncode})" + (f": {msg}" if msg else "")


def _print_lines(lines: list[str]) -> None:
    print("\n".join(lines))


def _git_current_branch(runner: Runner) -> Optional[str]:
    res = runner(["git", "branch", "--show-current"])
    if res.returncode != 0:
        return None
    branch = res.stdout.strip()
    return branch or None


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
    runner: Runner = default_runner,
    sleeper: Callable[[float], None] = time.sleep,
) -> int:
    """Merge a PR via gh, wait for MERGED, then optionally clean local state."""
    lines = [f"pr merge {pr}"]
    view = runner(["gh", "pr", "view", pr, "--json", "headRefName,state,url"])
    if view.returncode != 0:
        lines.append(f"- error: {_err(view)}")
        _print_lines(lines)
        return view.returncode or 1
    info = _json_from(view) or {}
    branch = info.get("headRefName")

    flag = {"squash": "--squash", "merge": "--merge", "rebase": "--rebase"}.get(method)
    if flag is None:
        lines.append(f"- error: unknown merge method {method!r}")
        _print_lines(lines)
        return 2

    merge = runner(["gh", "pr", "merge", pr, flag, "--delete-branch"])
    if merge.returncode != 0:
        lines.append(f"- error: {_err(merge)}")
        _print_lines(lines)
        return merge.returncode or 1
    lines.append("- merge command accepted")

    deadline = time.monotonic() + max(0, timeout_s)
    state = None
    while True:
        poll = runner(["gh", "pr", "view", pr, "--json", "state,url"])
        if poll.returncode != 0:
            lines.append(f"- warning: could not poll PR state: {_err(poll)}")
            break
        state = (_json_from(poll) or {}).get("state")
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


def ci_status(pr: Optional[str] = None, *, runner: Runner = default_runner) -> int:
    cmd = ["gh", "pr", "checks"]
    if pr:
        cmd.append(pr)
    cmd.extend(["--json", "name,state,link"])
    res = runner(cmd)
    if res.returncode != 0:
        print("ci status")
        print(f"- error: {_err(res)}")
        return res.returncode or 1
    checks = _json_from(res)
    if not isinstance(checks, list):
        print("ci status")
        print("- error: gh returned unparseable check data")
        return 1
    failing_states = {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED"}
    terminal_states = {"SUCCESS", "FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "SKIPPED"}
    failing = [
        c for c in checks
        if ((c.get("conclusion") or c.get("state") or "").upper() in failing_states)
    ]
    pending = [
        c for c in checks
        if c not in failing and (c.get("state") or "").upper() not in terminal_states
    ]
    print("ci status")
    print(f"- checks: {len(checks)} total, {len(failing)} failing, {len(pending)} pending")
    for c in failing[:20]:
        link = c.get("link") or ""
        suffix = f" ({link})" if link else ""
        status = c.get("conclusion") or c.get("state") or "?"
        print(f"- failed: {c.get('name', '?')} [{status}]" + suffix)
    return 1 if failing else 0


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
            branch = (_json_from(view) or {}).get("headRefName")
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
        run_id = str(runs[0].get("databaseId") or "")
        title = runs[0].get("workflowName") or "workflow"
        url = runs[0].get("url") or ""
        if branch:
            title = f"{title} ({branch})"
    else:
        meta = runner(["gh", "run", "view", run_id, "--json", "workflowName,url"])
        info = _json_from(meta) if meta.returncode == 0 else {}
        title = (info or {}).get("workflowName") or "workflow"
        url = (info or {}).get("url") or ""

    log = runner(["gh", "run", "view", run_id, "--log-failed"])
    print("ci failures")
    print(f"- run: {title} #{run_id}" + (f" ({url})" if url else ""))
    if log.returncode != 0:
        print(f"- error: {_err(log)}")
        return log.returncode or 1
    summary = summarize_pytest_log(log.stdout)
    if not summary.failures and not summary.errors:
        print("- failed log fetched, but no pytest-style failures were extracted")
        return 1
    _emit_test_summary(summary)
    return 1


@dataclass
class TestSummary:
    failures: list[str]
    errors: list[str]
    trace_lines: list[str]
    final_line: Optional[str]


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
    if "\t" not in line:
        return line
    parts = line.split("\t")
    return parts[-1] if parts else line


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
