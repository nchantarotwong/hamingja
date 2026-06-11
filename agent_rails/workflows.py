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
    skip_ci_reason: Optional[str] = None,
    runner: Runner = default_runner,
    sleeper: Callable[[float], None] = time.sleep,
) -> int:
    """Merge a PR via gh, wait for MERGED, then optionally clean local state."""
    lines = [f"pr merge {pr}"]
    if skip_ci_reason:
        lines.append(f"- local validation override: {skip_ci_reason}")
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
    if not all(_valid_check_shape(check) for check in checks):
        print("ci status")
        print("- error: gh returned malformed check data")
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
    budget_blocked = _ci_budget_blocked(failing, runner=runner)
    print("ci status")
    print(f"- checks: {len(checks)} total, {len(failing)} failing, {len(pending)} pending")
    if budget_blocked:
        print("- blocked: actions_budget_exhausted")
    for c in failing[:20]:
        link = c.get("link") or ""
        suffix = f" ({link})" if link else ""
        status = c.get("conclusion") or c.get("state") or "?"
        print(f"- failed: {c.get('name', '?')} [{status}]" + suffix)
    if budget_blocked:
        return 2
    return 1 if failing else 0


def _ci_budget_blocked(failing_checks: list[dict], *, runner: Runner) -> bool:
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
        if log.returncode != 0:
            continue
        lines = f"{log.stdout}\n{log.stderr}".splitlines()
        if any(_ci_budget_line_blocked(line) for line in lines):
            return True
    return False


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
