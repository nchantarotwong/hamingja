"""`agent-rails` command-line entry point.

A thin operator-facing CLI over the same core the hooks use. Subcommands:

    agent-rails report [--reset]   tuning summary: what fired, would-block rates
    agent-rails status [DIR]       resolved config for DIR (default: cwd)
    agent-rails commands           list workflow wrappers available here
    agent-rails preflight [NAME]   list or run repo-owned preflight scripts
    agent-rails code-atlas [DIR]   map large files to bounded line ranges
    agent-rails repo-health [DIR]  show large-file retrieval cost
    agent-rails locate QUERY       ranked code ranges to read next
    agent-rails locate-symbol NAME ranked definition-ish ranges
    agent-rails locate-edit QUERY  ranked ranges for a desired change
    agent-rails ledger ...          manage repo-local ruled-out records
    agent-rails budget ID [ACTION] inspect/add/reset session budget
    agent-rails install [HARNESS]  install hooks; no arg = all detected harnesses
    agent-rails init [...]         compose a CLAUDE.md + AGENTS.md symlink from profiles
    agent-rails pr-create ...      create a PR with a body file
    agent-rails pr-merge PR        wait for CI checks, merge + poll + local cleanup
    agent-rails ci-status          summarize PR checks
    agent-rails ci-preflight       classify CI quota/infrastructure blocks
    agent-rails ci-failures        summarize failed CI logs
    agent-rails test-summary LOG   summarize saved pytest output
    agent-rails version

`report` is the other half of `observe` mode: observe logs every non-ALLOW
verdict to the audit log; `report` reads it back so you can tune thresholds
against your real workflow before flipping to `enforce`.

`init` is the offline doc generator for the soft workflow layer. By default
it writes `./CLAUDE.md` from the bundled profiles and drops `./AGENTS.md` as
a relative symlink to it — Claude Code reads `CLAUDE.md` natively, Codex
reads `AGENTS.md`, both see the same content.

Nothing here can block a tool call — it's all read/aggregate plus wrappers
around install scripts and file generation.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from typing import Optional

from . import __version__
from .config import load_config
from .code_atlas import build_code_atlas, format_code_atlas, format_repo_health, repo_health
from .core.audit import clear_audit, read_audit, summarize
from .core.budget import approve as _budget_approve
from .core.budget import get_task_type as _budget_get_task_type
from .core.budget import known_task_types as _budget_known_task_types
from .core.budget import read_state as _budget_read_state
from .core.budget import reset as _budget_reset
from .core.budget import self_approve as _budget_self_approve
from .core.budget import set_task_type as _budget_set_task_type
from .core.state import read_recent as _read_recent_events
from .core.state import reset_session as _reset_detector_session
from .core.delegation import read_state as _delegation_read_state
from .core.delegation import reset_state as _delegation_reset_state
from .ledger import add_record as _ledger_add
from .ledger import check_records as _ledger_check
from .ledger import discover_root as _ledger_root
from .ledger import relevant_records as _ledger_relevant
from .ledger import retire as _ledger_retire
from .ledger import reverify as _ledger_reverify
from .locator import format_locations, locate
from .profiles import (
    ALL_PROFILES,
    DEFAULT_PROFILES,
    normalize as _normalize_profile,
    read_profile,
)
from .templates import ROOT_TEMPLATE, read_template
from .workflows import ci_failures, ci_preflight, ci_status, cleanup_after_merge, create_pr, merge_pr, test_summary, timed_runner


def _cmd_report(args: argparse.Namespace) -> int:
    if args.reset:
        clear_audit()
        print("audit log cleared.")
        return 0
    entries = read_audit()
    if args.json:
        print(json.dumps(summarize(entries), indent=2, sort_keys=True))
        return 0
    if not entries:
        print(
            "No verdicts recorded yet.\n"
            "Run some sessions in observe mode (the default), then re-run "
            "`agent-rails report`."
        )
        return 0
    s = summarize(entries)
    print(f"agent-rails report  ({s['total']} verdicts across {s['sessions']} session(s))")
    print()
    print(f"  nudges:        {s['nudges']}")
    print(f"  would-block:   {s['would_blocks']}   (become BLOCKS when the relevant mode is enforce)")
    print(f"  blocks:        {s['blocks']}   (already enforced)")
    print()
    print(f"  {'detector':<16} {'nudge':>7} {'would-block':>13} {'block':>7}")
    print(f"  {'-' * 16} {'-' * 7} {'-' * 13} {'-' * 7}")
    for det, c in sorted(s["by_detector"].items()):
        print(f"  {det:<16} {c['nudge']:>7} {c['would_block']:>13} {c['block']:>7}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    target = args.dir or os.getcwd()
    cfg = load_config(target)
    print(f"resolved config for: {target}")
    print(json.dumps(cfg, indent=2, sort_keys=True))
    env = os.environ.get("AGENT_RAILS_MODE")
    if env:
        print(f"\n(AGENT_RAILS_MODE={env} is set in your shell and overrides mode)")
    return 0


_GLOBAL_WRAPPERS = [
    ("PR", "agent-rails pr-create --title <title> --body-file <path>"),
    ("PR", "agent-rails pr-create --title <title> --body -"),
    ("PR", "agent-rails pr-merge <pr>"),
    ("PR", "agent-rails pr-merge <pr> --skip-ci-reason <reason>"),
    ("PR", "agent-rails post-merge-cleanup [branch]"),
    ("CI", "agent-rails ci-status [pr]"),
    ("CI", "agent-rails ci-status [pr] --wait"),
    ("CI", "agent-rails ci-preflight [pr]"),
    ("CI", "agent-rails ci-failures <pr>"),
    ("CI", "agent-rails ci-failures --pr <pr>"),
    ("CI", "agent-rails ci-failures --run <run-id>"),
    ("Tests", "agent-rails test-summary .pytest_output.log"),
]

_REPO_WRAPPER_NAMES = {
    "pr-create": "PR",
    "pr-merge": "PR",
    "post-merge-cleanup": "PR",
    "ci-status": "CI",
    "ci-preflight": "CI",
    "ci-failures": "CI",
    "test-summary": "Tests",
}

_PREFLIGHT_DIR = Path(".agent-rails") / "preflight"


def _repo_wrapper_commands(root: Path) -> list[tuple[str, str]]:
    """Return repo-local scripts/agent wrappers, if this repo provides any."""
    out: list[tuple[str, str]] = []
    scripts_dir = root / "scripts" / "agent"
    try:
        children = sorted(scripts_dir.iterdir())
    except OSError:
        return out
    for path in children:
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        category = _REPO_WRAPPER_NAMES.get(path.name)
        if category is None:
            continue
        out.append((category, f"scripts/agent/{path.name}"))
    return out


def _discover_repo_root(start: Path) -> Optional[Path]:
    """Return the nearest repo-ish root for repo-local wrappers."""
    path = start.resolve()
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists() or (candidate / ".agent-rails").exists():
            return candidate
    return None


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _repo_preflight_scripts(root: Path) -> list[Path]:
    """Return repo-local executable preflight scripts."""
    scripts_dir = root / _PREFLIGHT_DIR
    try:
        children = sorted(scripts_dir.iterdir())
    except OSError:
        return []

    scripts: list[Path] = []
    for path in children:
        try:
            if path.is_file() and os.access(path, os.X_OK) and _is_within(path, root):
                scripts.append(path)
        except OSError:
            continue
    return scripts


def _repo_preflight_commands(root: Path) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for path in _repo_preflight_scripts(root):
        out.append(("Preflight", f"agent-rails preflight {path.name}"))
    return out


def _cmd_commands(args: argparse.Namespace) -> int:
    root = _discover_repo_root(Path.cwd()) or Path.cwd()
    wrappers = list(_GLOBAL_WRAPPERS)
    wrappers.extend(_repo_wrapper_commands(root))
    wrappers.extend(_repo_preflight_commands(root))
    by_category: dict[str, list[str]] = {"PR": [], "CI": [], "Tests": [], "Preflight": []}
    for category, command in wrappers:
        by_category.setdefault(category, []).append(command)

    print("Available agent-rails wrappers for this repo:")
    for category in ("PR", "CI", "Tests", "Preflight"):
        commands = by_category.get(category) or []
        if not commands:
            continue
        print()
        print(f"{category}:")
        for command in commands:
            print(f"  {command}")
    print()
    print("Use these before raw gh/git polling, PR cleanup, CI log scraping, or manual test-log parsing.")
    print("Codex: if a wrapper fails because of sandboxed network or .git writes, rerun that wrapper with sandbox escalation.")
    return 0


def _print_preflight_list(root: Path) -> int:
    scripts = _repo_preflight_scripts(root)
    if not scripts:
        print(f"No repo-local preflights found under {_PREFLIGHT_DIR}/.")
        return 0

    print("Repo-local preflights:")
    for path in scripts:
        print(f"  {path.name}")
    return 0


def _cmd_preflight(args: argparse.Namespace) -> int:
    root = _discover_repo_root(Path.cwd())
    if root is None:
        print("error: could not find a repo root for repo-local preflights", file=sys.stderr)
        return 2

    if args.list or not args.name:
        return _print_preflight_list(root)

    name = args.name
    if Path(name).name != name or name in {"", ".", ".."}:
        print("error: preflight name must be a single script name", file=sys.stderr)
        return 2

    script = root / _PREFLIGHT_DIR / name
    try:
        if not script.is_file():
            print(f"error: unknown repo-local preflight: {name}", file=sys.stderr)
            return 2
        if not _is_within(script, root):
            print(f"error: repo-local preflight escapes the repo: {_PREFLIGHT_DIR / name}", file=sys.stderr)
            return 2
        if not os.access(script, os.X_OK):
            print(f"error: repo-local preflight is not executable: {_PREFLIGHT_DIR / name}", file=sys.stderr)
            return 2
    except OSError as e:
        print(f"error: could not inspect repo-local preflight {name}: {e}", file=sys.stderr)
        return 2

    forwarded = list(args.preflight_args or [])
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]

    env = dict(os.environ)
    env["AGENT_RAILS_REPO_ROOT"] = str(root)
    env["AGENT_RAILS_PREFLIGHT_NAME"] = name
    try:
        completed = subprocess.run(
            [str(script), *forwarded],
            cwd=str(root),
            env=env,
            check=False,
        )
    except OSError as e:
        print(f"error: could not run repo-local preflight {name}: {e}", file=sys.stderr)
        return 2
    return completed.returncode


def _cmd_code_atlas(args: argparse.Namespace) -> int:
    try:
        root = Path(args.dir) if args.dir else Path.cwd()
        atlas = build_code_atlas(
            root=root,
            glob=args.glob,
            min_lines=args.min_lines,
            max_files=args.max_files,
            max_entries_per_file=args.max_entries,
        )
        if args.json:
            resolved = root.resolve()
            print(json.dumps({
                "schema_version": 1,
                "kind": "code_atlas",
                "incomplete": len(atlas) >= args.max_files,
                "files": [{
                    "path": str(item.path.relative_to(resolved)),
                    "line_count": item.line_count,
                    "generated": item.generated,
                    "entries": [vars(entry) for entry in item.entries],
                } for item in atlas],
            }, sort_keys=True))
        else:
            print(format_code_atlas(atlas, root=root))
    except Exception:
        print("No code atlas entries found.")
    return 0


def _cmd_repo_health(args: argparse.Namespace) -> int:
    try:
        root = Path(args.dir) if args.dir else Path.cwd()
        health = repo_health(
            root=root,
            glob=args.glob,
            min_lines=args.min_lines,
            max_files=args.max_files,
            max_suggestions=args.max_suggestions,
        )
        if args.json:
            resolved = root.resolve()
            print(json.dumps({
                "schema_version": 1,
                "kind": "repo_health",
                "incomplete": len(health) >= args.max_files,
                "files": [{
                    "path": str(item.path.relative_to(resolved)),
                    "line_count": item.line_count,
                    "estimated_tokens": item.estimated_tokens,
                    "generated": item.generated,
                    "suggestions": item.suggestions,
                } for item in health],
            }, sort_keys=True))
        else:
            print(format_repo_health(health, root=root))
    except Exception:
        print("No large source files found.")
    return 0


def _cmd_locate(args: argparse.Namespace) -> int:
    try:
        root = Path(args.dir) if args.dir else Path.cwd()
        results = locate(
            args.query,
            root=root,
            glob=args.glob,
            max_results=args.max_results,
            context_lines=args.context_lines,
            symbol=getattr(args, "symbol", False),
        )
        print(format_locations(results, root=root))
    except Exception:
        print("No likely targets found.")
    return 0


def _cmd_ledger(args: argparse.Namespace) -> int:
    root = _ledger_root(Path(args.dir) if getattr(args, "dir", None) else Path.cwd())
    action = getattr(args, "ledger_action", None)
    if action == "add":
        result = _ledger_add(
            root,
            kind=args.kind,
            claim=args.claim,
            evidence=args.evidence,
            falsifier=args.falsifier or "",
            scope=args.scope or [],
            cost=args.cost or "",
            body=args.body or "",
            slug=args.slug or "",
        )
        if not result.ok:
            print(f"error: {result.message}", file=sys.stderr)
            return 2
        print(result.message)
        if result.record is not None:
            print(f"{result.record.path.relative_to(root)}")
        return 0
    if action == "check":
        recs, stale = _ledger_check(root)
        print(f"checked {len(recs)} ledger record(s); stale: {stale}")
        for rec in recs:
            if rec.stale:
                print(f"STALE {rec.slug}: {rec.claim}")
        return 0
    if action == "relevant":
        recs = _ledger_relevant(root, args.paths or [])
        if not recs:
            print("No relevant ledger records.")
            return 0
        for rec in recs:
            state = " STALE" if rec.stale else ""
            print(f"{rec.slug}{state} ({rec.kind}): {rec.claim}")
            print(f"  scope: {', '.join(rec.scope)}")
        return 0
    if action == "reverify":
        result = _ledger_reverify(root, args.slug, timeout=args.timeout)
        if not result.ok:
            print(f"error: {result.message}", file=sys.stderr)
            return 2
        print(result.message)
        return 0
    if action == "retire":
        result = _ledger_retire(root, args.slug, reason=args.reason or "")
        if not result.ok:
            print(f"error: {result.message}", file=sys.stderr)
            return 2
        print(result.message)
        return 0
    print("error: missing ledger action", file=sys.stderr)
    return 2


_HARNESS_ALIASES = {"claude": "claude_code"}
MAX_STDIN_BODY_BYTES = 1_000_000
STDIN_BODY_CHUNK_CHARS = 64_000
# All harnesses agent-rails knows how to install for. Order matters: when
# installing multiple, we run them in this order so output is predictable.
_KNOWN_HARNESSES = ["claude_code", "codex"]
# Config-dir -> harness slug. Used by the detection path under `install`.
_HARNESS_HOMES = {".claude": "claude_code", ".codex": "codex"}


def _detect_harnesses(home: Path) -> list[str]:
    """Return harnesses whose config dir exists under `home`, in stable order."""
    out: list[str] = []
    for sub, slug in _HARNESS_HOMES.items():
        try:
            if (home / sub).is_dir():
                out.append(slug)
        except OSError:
            continue
    return out


def _run_single_install(harness: str) -> int:
    script = (
        Path(__file__).resolve().parent
        / "adapters"
        / harness
        / "install.sh"
    )
    if not script.exists():
        print(f"error: no installer for harness {harness!r}", file=sys.stderr)
        return 1
    try:
        return subprocess.call(["bash", str(script)])
    except FileNotFoundError:
        print("error: bash not found on PATH", file=sys.stderr)
        return 1


def _run_installs(harnesses: list[str]) -> int:
    rc = 0
    for i, h in enumerate(harnesses):
        if len(harnesses) > 1:
            if i:
                print()
            print(f"--- installing for {h} ---")
        r = _run_single_install(h)
        if r != 0:
            rc = r
    return rc


def _cmd_install(args: argparse.Namespace) -> int:
    harness = args.harness
    if harness is None:
        detected = _detect_harnesses(Path.home())
        if not detected:
            print(
                "error: no harness detected at ~/.claude/ or ~/.codex/.\n"
                "Pass an explicit harness name: claude | codex | all",
                file=sys.stderr,
            )
            return 1
        return _run_installs(detected)
    if harness == "all":
        return _run_installs(list(_KNOWN_HARNESSES))
    actual = _HARNESS_ALIASES.get(harness, harness)
    return _run_installs([actual])


def _cmd_pr_create_text(args: argparse.Namespace) -> int:
    runner = timed_runner(args.command_timeout)
    if args.body is not None and args.body != "-":
        print("pr create")
        print("- error: --body only supports '-' for stdin; use --body-file for saved content")
        return 2
    if args.body == "-":
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as body_file:
            read_error = _copy_stdin_to_body_file(body_file)
            body_path = Path(body_file.name)
        if read_error is not None:
            try:
                body_path.unlink()
            except OSError:
                pass
            print("pr create")
            print(read_error)
            return 2
        try:
            return create_pr(
                title=args.title,
                body_file=body_path,
                base=args.base,
                head=args.head,
                remote=args.remote,
                draft=args.draft,
                runner=runner,
                outcome=getattr(args, "_outcome", None),
            )
        finally:
            try:
                body_path.unlink()
            except OSError:
                pass
    return create_pr(
        title=args.title,
        body_file=Path(args.body_file),
        base=args.base,
        head=args.head,
        remote=args.remote,
        draft=args.draft,
        runner=runner,
        outcome=getattr(args, "_outcome", None),
    )


def _workflow_json(operation: str, state: str, rc: int, detail: str = "") -> None:
    print(json.dumps({
        "schema_version": 1,
        "operation": operation,
        "state": state,
        "exit_code": rc,
        "detail": detail[:1000],
    }, sort_keys=True))


def _cmd_pr_create(args: argparse.Namespace) -> int:
    if not args.json:
        return _cmd_pr_create_text(args)
    captured = io.StringIO()
    outcome = []
    args._outcome = outcome
    try:
        with redirect_stdout(captured):
            rc = _cmd_pr_create_text(args)
    except KeyboardInterrupt:
        _workflow_json("pr_create", "interrupted", 130, "safe to rerun")
        return 130
    state = outcome[-1].state if outcome else ("created" if rc == 0 else "failed")
    detail = outcome[-1].detail if outcome else captured.getvalue()
    _workflow_json("pr_create", state, rc, detail)
    return rc


def _cmd_pr_merge_text(args: argparse.Namespace) -> int:
    runner = timed_runner(args.command_timeout)
    return merge_pr(
        args.pr,
        method=args.method,
        cleanup=not args.no_cleanup,
        main_branch=args.main,
        remote=args.remote,
        timeout_s=args.timeout,
        poll_s=args.poll,
        ci_timeout_s=args.ci_timeout,
        ci_poll_s=args.ci_poll,
        skip_ci_reason=args.skip_ci_reason,
        runner=runner,
        outcome=getattr(args, "_outcome", None),
    )


def _cmd_pr_merge(args: argparse.Namespace) -> int:
    if not args.json:
        return _cmd_pr_merge_text(args)
    captured = io.StringIO()
    outcome = []
    args._outcome = outcome
    try:
        with redirect_stdout(captured):
            rc = _cmd_pr_merge_text(args)
    except KeyboardInterrupt:
        _workflow_json("pr_merge", "interrupted", 130, "safe to rerun; wrapper rechecks PR state")
        return 130
    state = outcome[-1].state if outcome else (
        "merged" if rc == 0 else ("blocked" if rc == 2 else "failed")
    )
    detail = outcome[-1].detail if outcome else captured.getvalue()
    _workflow_json("pr_merge", state, rc, detail)
    return rc


def _cmd_post_merge_cleanup(args: argparse.Namespace) -> int:
    runner = timed_runner(args.command_timeout)
    return cleanup_after_merge(
        args.branch,
        main_branch=args.main,
        remote=args.remote,
        dry_run=args.dry_run,
        force_delete=args.force_delete,
        runner=runner,
    )


def _cmd_ci_status(args: argparse.Namespace) -> int:
    kwargs = {
        "runner": timed_runner(args.command_timeout),
        "wait_timeout_s": args.timeout if args.wait else 0,
        "poll_s": args.poll,
    }
    if not args.json:
        return ci_status(args.pr, **kwargs)
    outcome = []
    captured = io.StringIO()
    with redirect_stdout(captured):
        rc = ci_status(args.pr, outcome=outcome, **kwargs)
    if outcome:
        print(json.dumps(outcome[-1].__dict__, sort_keys=True))
    else:
        print(json.dumps({
            "schema_version": 1, "operation": "ci_status",
            "state": "unknown", "exit_code": rc,
        }, sort_keys=True))
    return rc


def _cmd_ci_preflight(args: argparse.Namespace) -> int:
    return ci_preflight(args.pr, runner=timed_runner(args.command_timeout))


def _cmd_ci_failures(args: argparse.Namespace) -> int:
    if _blank(args.pr) or _blank(args.pr_arg) or _blank(args.run):
        print("ci failures")
        print("- error: PR and run identifiers must not be empty")
        return 2
    source_count = len([value for value in (args.pr, args.pr_arg, args.run) if value is not None])
    if source_count > 1:
        print("ci failures")
        print("- error: pass exactly one of positional PR, --pr, or --run")
        return 2
    return ci_failures(pr=args.pr or args.pr_arg, run_id=args.run, runner=timed_runner(args.command_timeout))


def _blank(value) -> bool:
    return isinstance(value, str) and not value.strip()


def _copy_stdin_to_body_file(body_file) -> str | None:
    total = 0
    while True:
        chunk = sys.stdin.read(STDIN_BODY_CHUNK_CHARS)
        if chunk == "":
            return None
        try:
            encoded = chunk.encode("utf-8")
        except UnicodeEncodeError:
            return "- error: stdin PR body must be valid UTF-8 text; use --body-file for binary or invalid text"
        total += len(encoded)
        if total > MAX_STDIN_BODY_BYTES:
            return f"- error: stdin PR body exceeds {MAX_STDIN_BODY_BYTES} bytes; use --body-file for larger content"
        body_file.write(chunk)


def _cmd_recover(args: argparse.Namespace) -> int:
    """Show a bounded recovery/handoff packet or explicitly reset detectors."""
    try:
        session_id = str(args.session_id).strip()
        if not session_id:
            print("error: session id must not be empty", file=sys.stderr)
            return 2
        if args.action == "reset":
            changed = _reset_detector_session(session_id)
            print(
                f"reset: detector state {'cleared' if changed else 'already empty'} "
                f"for session: {session_id}"
            )
            print("audit history preserved")
            return 0

        state = _budget_read_state(session_id)
        events = _read_recent_events(session_id, 8)
        print(f"# agent-rails recovery handoff: {session_id}")
        print("")
        progress = state.get("last_progress") if isinstance(state, dict) else None
        print("Last observed progress:")
        print(json.dumps(progress, sort_keys=True) if isinstance(progress, dict) else "none")
        print("")
        print("Recent mechanical signatures:")
        if not events:
            print("none")
        for event in events:
            print(
                f"- {event.status} {event.tool} {event.arg_kind or 'unclassified'} "
                f"signature={event.arg_hash}"
            )
        paths = [event.read_path for event in events if event.read_path]
        ruled_out = []
        try:
            if paths:
                ruled_out = _ledger_relevant(_ledger_root(Path.cwd()), paths)[:5]
        except Exception:
            ruled_out = []
        print("")
        print("Relevant ruled-out hypotheses:")
        if not ruled_out:
            print("none")
        for record in ruled_out:
            print(f"- {record.slug}: {record.claim}")
        print("")
        print("Minimal next action:")
        print("- Run one materially different, read-only diagnostic.")
        print("")
        print("Recovery commands:")
        print(f"- agent-rails recover {session_id} reset")
        print(f"- agent-rails budget {session_id} reset")
        return 0
    except Exception:
        print("Recovery packet unavailable; no state was changed.")
        return 0


def _cmd_delegation(args: argparse.Namespace) -> int:
    try:
        if args.action == "reset":
            changed = _delegation_reset_state(str(args.session_id))
            print(f"delegation state {'cleared' if changed else 'already empty'}")
            return 0
        state = _delegation_read_state(str(args.session_id))
        print(json.dumps(state, indent=2, sort_keys=True))
    except Exception:
        print("{}")
    return 0


def _cmd_budget(args: argparse.Namespace) -> int:
    parts = list(getattr(args, "budget_args", []) or [])
    self_approve = bool(getattr(args, "self_approve", False))
    approve_subagent = bool(getattr(args, "approve_subagent", False))
    if not parts and not self_approve and not approve_subagent:
        args._parser.print_help()  # type: ignore[attr-defined]
        return 0
    if any(p in {"-h", "--help"} for p in parts):
        args._parser.print_help()  # type: ignore[attr-defined]
        return 0

    filtered: list[str] = []
    for part in parts:
        if part == "--self":
            self_approve = True
        elif part == "--subagent":
            approve_subagent = True
        else:
            filtered.append(part)
    parts = filtered
    if not parts:
        print("error: missing budget session id", file=sys.stderr)
        _print_budget_examples(file=sys.stderr)
        return 2

    if parts and parts[0] == "task-type":
        return _cmd_budget_task_type(args, parts[1:])

    session_id = parts[0]
    action = parts[1] if len(parts) > 1 else "status"
    value = parts[2] if len(parts) > 2 else None

    if len(parts) > 3:
        print("error: too many budget arguments", file=sys.stderr)
        _print_budget_examples(file=sys.stderr)
        return 2

    if self_approve and action != "add":
        print("error: --self is only valid with `add`", file=sys.stderr)
        print(f"  Use: agent-rails budget {session_id} add 3 --self", file=sys.stderr)
        return 2
    if approve_subagent and action not in {"add", "subagent"}:
        print("error: --subagent is only valid with `add`; otherwise use the `subagent` action", file=sys.stderr)
        print(f"  Use: agent-rails budget {session_id} subagent", file=sys.stderr)
        return 2

    if action == "status":
        if value is not None:
            print("error: status does not take a value", file=sys.stderr)
            print(f"  Use: agent-rails budget {session_id}", file=sys.stderr)
            return 2
        state = _budget_read_state(session_id)
        if not state:
            print(f"no budget state found for session: {session_id}")
            _print_budget_next_steps(session_id)
            return 0
        print(f"budget state for session: {session_id}")
        for k, v in sorted(state.items()):
            print(f"  {k}: {v}")
        _print_budget_next_steps(session_id)
        return 0

    if action == "add":
        add = _parse_budget_int(value, default=8, floor=1)
        if add is None:
            print("error: add requires a positive integer", file=sys.stderr)
            print(f"  Use: agent-rails budget {session_id} add 20", file=sys.stderr)
            return 2
        if self_approve:
            cfg = load_config(os.getcwd())
            # Pass the wrapping budget cfg so self_approve sees both the
            # self_approve sub-dict AND checkpoint_at (used for replenishment math).
            result = _budget_self_approve(
                session_id, add_tools=add, cfg=cfg.get("budget", {})
            )
            if not result.get("ok"):
                print(
                    f"error: self-approve rejected: {result.get('reason', 'unknown')}",
                    file=sys.stderr,
                )
                print(
                    f"  Human approval: ! agent-rails budget {session_id} add {add}",
                    file=sys.stderr,
                )
                return 1
            state = result.get("state", {})
            print(f"self-approved: session={session_id}")
            print(f"  approved_tool_calls: {state.get('approved_tool_calls')}")
            print(f"  self_approve_times:  {state.get('self_approve_times')}")
            return 0
        state = _budget_approve(session_id, add_tools=add, approve_subagent=approve_subagent)
        if not state:
            print(f"error: could not update budget state for session: {session_id}", file=sys.stderr)
            print(f"  To clear bad state: agent-rails budget {session_id} reset", file=sys.stderr)
            return 1
        print(f"approved: session={session_id}")
        print(f"  approved_tool_calls: {state.get('approved_tool_calls')}")
        print(f"  subagent_approved:   {state.get('subagent_approved')}")
        return 0

    if action == "subagent":
        if value is not None:
            print("error: subagent does not take a value", file=sys.stderr)
            print(f"  Use: agent-rails budget {session_id} subagent", file=sys.stderr)
            return 2
        state = _budget_approve(session_id, add_tools=1, approve_subagent=True)
        if not state:
            print(f"error: could not update budget state for session: {session_id}", file=sys.stderr)
            print(f"  To clear bad state: agent-rails budget {session_id} reset", file=sys.stderr)
            return 1
        print(f"approved: session={session_id}")
        print(f"  approved_tool_calls: {state.get('approved_tool_calls')}")
        print(f"  subagent_approved:   {state.get('subagent_approved')}")
        return 0

    if action == "reset":
        add = _parse_budget_int(value, default=0, floor=0)
        if add is None:
            print("error: reset runway must be a non-negative integer", file=sys.stderr)
            print(f"  Use: agent-rails budget {session_id} reset 20", file=sys.stderr)
            return 2
        deleted = _budget_reset(session_id, add_tools=add)
        if deleted:
            print(f"reset: budget state cleared for session: {session_id}")
        else:
            print(f"reset: no budget state found for session: {session_id}")
        if add > 0:
            print(f"  pre-approved: {add} tool calls added above checkpoint_at for next session")
        return 0

    print(f"error: unknown budget action: {action}", file=sys.stderr)
    _print_budget_examples(file=sys.stderr)
    return 2


def _parse_budget_int(value: Optional[str], *, default: int, floor: int) -> Optional[int]:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < floor:
        return None
    return parsed


def _print_budget_next_steps(session_id: str) -> None:
    print("next:")
    print(f"  agent-rails budget {session_id} add 20")
    print(f"  agent-rails budget {session_id} reset")
    print(f"  agent-rails budget {session_id} reset 20")


def _print_budget_examples(*, file=None) -> None:
    if file is None:
        file = sys.stdout
    print("examples:", file=file)
    print("  agent-rails budget <session-id>", file=file)
    print("  agent-rails budget <session-id> add 20", file=file)
    print("  agent-rails budget <session-id> reset", file=file)
    print("  agent-rails budget <session-id> reset 20", file=file)
    print("  agent-rails budget <session-id> subagent", file=file)
    print("  agent-rails budget <session-id> add 3 --self", file=file)


def _cmd_budget_task_type(args: argparse.Namespace, parts: list[str]) -> int:
    cfg = load_config(os.getcwd())
    budget_cfg = cfg.get("budget", {}) if isinstance(cfg, dict) else {}
    action = parts[0] if parts else None
    if action == "list" and len(parts) == 1:
        for name in _budget_known_task_types(budget_cfg):
            print(name)
        return 0
    if action == "get" and len(parts) == 2:
        current = _budget_get_task_type(parts[1])
        print(current if current else "(not set)")
        return 0
    if action == "set" and len(parts) == 3:
        result = _budget_set_task_type(parts[1], parts[2], budget_cfg)
        if not result.get("ok"):
            print(
                f"error: task-type set rejected: {result.get('reason', 'unknown')}",
                file=sys.stderr,
            )
            return 1
        state = result.get("state", {})
        print(f"task-type: session={parts[1]}")
        print(f"  task_type:           {state.get('task_type')}")
        print(f"  approved_tool_calls: {state.get('approved_tool_calls')}")
        return 0
    print("budget task-type usage:", file=sys.stderr)
    print("  agent-rails budget task-type list", file=sys.stderr)
    print("  agent-rails budget task-type get <session-id>", file=sys.stderr)
    print("  agent-rails budget task-type set <session-id> <type>", file=sys.stderr)
    return 2


def _cmd_test_summary(args: argparse.Namespace) -> int:
    return test_summary(Path(args.log))


def _split_profile_args(values) -> list[str]:
    """Flatten `--profile a --profile b,c` into ['a','b','c']."""
    out: list[str] = []
    for v in values or []:
        for part in str(v).split(","):
            p = part.strip()
            if p:
                out.append(p)
    return out


def _render_profiles_md(profile_names: list[str]) -> str:
    """Render the inner content: template header + profile sections, no markers."""
    parts = [read_template(ROOT_TEMPLATE).rstrip()]
    for name in profile_names:
        parts.append("")  # blank line between sections
        parts.append(read_profile(name).rstrip())
    return "\n".join(parts) + "\n"


# HTML-comment markers — invisible in rendered markdown — that delimit the
# agent-rails-managed block within an existing CLAUDE.md / AGENTS.md. The
# upsert path uses these to detect and replace the prior managed block in
# place, so re-running `init` is idempotent and never duplicates.
_BEGIN_MARKER = "<!-- BEGIN agent-rails workflow profiles -->"
_END_MARKER = "<!-- END agent-rails workflow profiles -->"
_MANAGED_BLOCK_RE = re.compile(
    re.escape(_BEGIN_MARKER) + r".*?" + re.escape(_END_MARKER),
    re.DOTALL,
)
_MANAGED_NOTICE = (
    "<!-- managed by `agent-rails init`; edits between these markers will be "
    "overwritten on re-run -->"
)
_PROFILE_HEADING_RE = re.compile(r"^#\s+([A-Za-z0-9_-]+)\s*$", re.MULTILINE)


def _make_managed_block(profile_names: list[str]) -> str:
    """Wrap the rendered profile content in the BEGIN/END marker block."""
    inner = _render_profiles_md(profile_names).rstrip()
    return (
        f"{_BEGIN_MARKER}\n"
        f"{_MANAGED_NOTICE}\n"
        f"\n"
        f"{inner}\n"
        f"\n"
        f"{_END_MARKER}"
    )


def _upsert_managed_block(existing: str, block: str) -> str:
    """Insert or replace the managed block in `existing`, preserving everything else.

    - markers present: replace the existing block in place.
    - markers absent + non-empty file: append the block at the end with a
      blank line of separation, preserving the existing content untouched.
    - empty/whitespace-only file: write just the block.
    """
    block = block.rstrip()
    if _BEGIN_MARKER in existing and _END_MARKER in existing:
        result = _MANAGED_BLOCK_RE.sub(block, existing, count=1)
        return result if result.endswith("\n") else result + "\n"
    if not existing.strip():
        return block + "\n"
    cleaned = existing.rstrip() + "\n"
    return cleaned + "\n" + block + "\n"


def _profiles_from_existing_text(existing: str) -> list[str]:
    """Return known profile slugs found in an existing managed block."""
    match = _MANAGED_BLOCK_RE.search(existing)
    if match is None:
        return []

    seen: set[str] = set()
    out: list[str] = []
    for raw in _PROFILE_HEADING_RE.findall(match.group(0)):
        name = _normalize_profile(raw)
        if name in ALL_PROFILES and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _profiles_from_existing_paths(paths: list[Path]) -> list[str]:
    """Infer the previous managed profile set from existing files, if present."""
    seen: set[str] = set()
    out: list[str] = []
    for path in paths:
        try:
            if not path.exists() or not path.is_file():
                continue
            found = _profiles_from_existing_text(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        for name in found:
            if name not in seen:
                seen.add(name)
                out.append(name)
    return out


def _default_or_existing_profiles(args: argparse.Namespace) -> list[str]:
    """Use defaults for first init; on re-run, preserve the existing profile set."""
    paths: list[Path]
    if args.out is not None:
        paths = [Path(args.out)]
    else:
        paths = [Path.cwd() / "CLAUDE.md", Path.cwd() / "AGENTS.md"]
    return _profiles_from_existing_paths(paths) or list(DEFAULT_PROFILES)


def _rel_to_cwd(p: Path) -> Path:
    try:
        return p.relative_to(Path.cwd())
    except ValueError:
        return p


def _make_relative_symlink(link_path: Path, target_path: Path, force: bool) -> Optional[str]:
    """Create `link_path` as a relative symlink pointing at `target_path`.

    Returns an error message on failure. Refuses to clobber an existing
    file/symlink unless `force` is set.
    """
    if link_path.is_symlink() or link_path.exists():
        if not force:
            return f"{link_path} already exists (re-run with --force to overwrite)"
        try:
            link_path.unlink()
        except OSError as e:
            return f"could not remove existing {link_path}: {e}"
    rel_target = os.path.relpath(str(target_path), start=str(link_path.parent))
    try:
        link_path.symlink_to(rel_target)
    except OSError as e:
        return f"could not create symlink {link_path} -> {rel_target}: {e}"
    return None


# State of one file in the canonical CLAUDE.md / AGENTS.md pair.
# 'missing'            -- nothing there
# 'real'               -- regular file
# 'symlink_to_sibling' -- a symlink whose target resolves to the OTHER file
# 'weird'              -- anything else: directory, broken symlink, symlink
#                         to an unrelated path, special file
_FileState = str


def _classify(path: Path, sibling: Path) -> _FileState:
    """Classify one half of the canonical pair against the other."""
    if path.is_symlink():
        try:
            tgt = os.readlink(path)
        except OSError:
            return "weird"
        try:
            resolved = (path.parent / tgt).resolve(strict=False)
            sib_resolved = sibling.resolve(strict=False)
        except OSError:
            return "weird"
        return "symlink_to_sibling" if resolved == sib_resolved else "weird"
    if path.exists():
        return "real" if path.is_file() else "weird"
    return "missing"


def _decide_canonical_actions(
    claude_state: _FileState,
    agents_state: _FileState,
) -> Optional[tuple[list[str], bool]]:
    """Map (CLAUDE.md state, AGENTS.md state) -> (targets-to-modify, create_symlink).

    Returns None for "weird" combinations the caller must refuse.

    Decision table (agreed in design discussion):
      (missing, missing)               -> write CLAUDE.md fresh + symlink AGENTS.md
      (real,    missing)               -> append/upsert CLAUDE.md only
      (missing, real)                  -> append/upsert AGENTS.md only
      (real,    real)                  -> append/upsert BOTH (they may differ)
      (real,    symlink_to_sibling)    -> append/upsert CLAUDE.md (symlink follows)
      (symlink_to_sibling, real)       -> append/upsert AGENTS.md (symlink follows)
      anything else                    -> refuse (weird / cyclic / broken)
    """
    pair = (claude_state, agents_state)
    if pair == ("missing", "missing"):
        return ["CLAUDE.md"], True
    if pair == ("real", "missing"):
        return ["CLAUDE.md"], False
    if pair == ("missing", "real"):
        return ["AGENTS.md"], False
    if pair == ("real", "real"):
        return ["CLAUDE.md", "AGENTS.md"], False
    if pair == ("real", "symlink_to_sibling"):
        return ["CLAUDE.md"], False
    if pair == ("symlink_to_sibling", "real"):
        return ["AGENTS.md"], False
    return None


def _describe_state(name: str, state: _FileState, path: Path) -> str:
    if state == "missing":
        return f"  {name}: missing"
    if state == "real":
        return f"  {name}: regular file"
    if state == "symlink_to_sibling":
        try:
            tgt = os.readlink(path)
        except OSError:
            tgt = "?"
        return f"  {name}: symlink -> {tgt}"
    # weird
    if path.is_symlink():
        try:
            tgt = os.readlink(path)
        except OSError:
            tgt = "?"
        return f"  {name}: symlink -> {tgt}  (not pointing at the sibling)"
    if path.exists():
        return f"  {name}: not a regular file (directory or special)"
    return f"  {name}: weird state"


def _apply_to_file(path: Path, block: str, force: bool) -> tuple[str, str]:
    """Write the block into `path` per upsert rules. Returns (verb, filename).

    verb is one of: 'wrote', 'updated', 'appended to', 'clobbered'.
    """
    name = path.name
    existed = path.exists()
    if force or not existed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(block + "\n", encoding="utf-8")
        return ("clobbered" if force and existed else "wrote", name)
    existing = path.read_text(encoding="utf-8")
    had_block = _BEGIN_MARKER in existing and _END_MARKER in existing
    new_content = _upsert_managed_block(existing, block)
    path.write_text(new_content, encoding="utf-8")
    return ("updated" if had_block else "appended to", name)


def _cmd_init(args: argparse.Namespace) -> int:
    if args.list:
        for name in ALL_PROFILES:
            tag = "  (default)" if name in DEFAULT_PROFILES else ""
            print(f"{name}{tag}")
        return 0

    if args.no_link and args.link is not None:
        print("error: --link and --no-link are mutually exclusive", file=sys.stderr)
        return 2

    raw = _split_profile_args(args.profile)
    selected = [_normalize_profile(p) for p in raw] if raw else _default_or_existing_profiles(args)

    unknown = [n for n in selected if n not in ALL_PROFILES]
    if unknown:
        print(
            f"error: unknown profile(s): {', '.join(unknown)}\n"
            f"available: {', '.join(ALL_PROFILES)}",
            file=sys.stderr,
        )
        return 2

    # de-dup while preserving first-seen order so repeated --profile flags don't double up
    seen: set[str] = set()
    ordered: list[str] = []
    for n in selected:
        if n not in seen:
            seen.add(n)
            ordered.append(n)

    block = _make_managed_block(ordered)

    if args.out is not None:
        return _init_explicit_out(args, block, ordered)
    return _init_canonical_pair(args, block, ordered)


def _init_explicit_out(args: argparse.Namespace, block: str, ordered: list[str]) -> int:
    """Single-file mode: write/upsert at --out, optionally create --link symlink."""
    out_path = Path(args.out)
    link_path: Optional[Path] = Path(args.link) if args.link else None

    if link_path is not None:
        if os.path.abspath(str(link_path)) == os.path.abspath(str(out_path)):
            print(
                f"error: --link path is the same as --out ({out_path})",
                file=sys.stderr,
            )
            return 2

    if args.dry_run:
        verb = _preview_action_for(out_path, force=args.force)
        print(block)
        print()
        print(f"(would {verb} {_rel_to_cwd(out_path)})")
        if link_path is not None:
            rel_target = os.path.relpath(str(out_path), start=str(link_path.parent))
            print(f"(would also create symlink: {_rel_to_cwd(link_path)} -> {rel_target})")
        return 0

    if link_path is not None and (link_path.is_symlink() or link_path.exists()) and not args.force:
        print(
            f"error: {link_path} already exists. Re-run with --force to overwrite.",
            file=sys.stderr,
        )
        return 1

    try:
        verb, _ = _apply_to_file(out_path, block, args.force)
    except OSError as e:
        print(f"error: could not write {out_path}: {e}", file=sys.stderr)
        return 1

    lines = [f"{verb} {_rel_to_cwd(out_path)}  ({len(ordered)} profile(s): {', '.join(ordered)})"]

    if link_path is not None:
        err = _make_relative_symlink(link_path, out_path, args.force)
        if err is not None:
            print("\n".join(lines))
            print(f"warning: symlink not created: {err}", file=sys.stderr)
            return 1
        rel_target = os.path.relpath(str(out_path), start=str(link_path.parent))
        lines.append(f"linked {_rel_to_cwd(link_path)} -> {rel_target}")

    print("\n".join(lines))
    return 0


def _init_canonical_pair(args: argparse.Namespace, block: str, ordered: list[str]) -> int:
    """Default canonical-pair mode: act on CLAUDE.md / AGENTS.md per the decision table."""
    claude_path = Path.cwd() / "CLAUDE.md"
    agents_path = Path.cwd() / "AGENTS.md"

    # --force short-circuits the decision table: clobber CLAUDE.md fresh and
    # create the AGENTS.md symlink (or whatever --link/--no-link say). Matches
    # the "I want a clean slate" semantics of the prior --force.
    if args.force:
        return _init_force_canonical(args, block, ordered, claude_path, agents_path)

    c_state = _classify(claude_path, agents_path)
    a_state = _classify(agents_path, claude_path)
    decision = _decide_canonical_actions(c_state, a_state)

    if decision is None:
        # Weird state — refuse loudly, describe what we saw, suggest --force
        print("error: CLAUDE.md / AGENTS.md are in a state `init` won't guess at:", file=sys.stderr)
        print(_describe_state("CLAUDE.md", c_state, claude_path), file=sys.stderr)
        print(_describe_state("AGENTS.md", a_state, agents_path), file=sys.stderr)
        print(
            "\nFix the offending file manually (or remove it), or re-run with "
            "--force to clobber CLAUDE.md and rewrite the canonical pair.",
            file=sys.stderr,
        )
        return 1

    targets, want_symlink = decision

    if args.dry_run:
        for name in targets:
            path = Path.cwd() / name
            verb = _preview_action_for(path, force=False)
            print(f"# {verb} {name}:")
            print(block)
            print()
        if want_symlink and not args.no_link:
            link_path = Path(args.link) if args.link else agents_path
            rel_target = os.path.relpath(str(claude_path), start=str(link_path.parent))
            print(f"(would also create symlink: {_rel_to_cwd(link_path)} -> {rel_target})")
        return 0

    lines: list[str] = []
    for name in targets:
        path = Path.cwd() / name
        try:
            verb, fname = _apply_to_file(path, block, force=False)
        except OSError as e:
            print(f"error: could not write {path}: {e}", file=sys.stderr)
            return 1
        lines.append(f"{verb} {fname}")

    if want_symlink and not args.no_link:
        link_path = Path(args.link) if args.link else agents_path
        if os.path.abspath(str(link_path)) == os.path.abspath(str(claude_path)):
            print("\n".join(lines))
            print(
                f"error: --link path is the same as the canonical CLAUDE.md ({claude_path})",
                file=sys.stderr,
            )
            return 2
        err = _make_relative_symlink(link_path, claude_path, force=False)
        if err is not None:
            print("\n".join(lines))
            print(f"warning: symlink not created: {err}", file=sys.stderr)
            return 1
        rel_target = os.path.relpath(str(claude_path), start=str(link_path.parent))
        lines.append(f"linked {_rel_to_cwd(link_path)} -> {rel_target}")

    lines.append(f"({len(ordered)} profile(s): {', '.join(ordered)})")
    print("\n".join(lines))
    return 0


def _init_force_canonical(
    args: argparse.Namespace,
    block: str,
    ordered: list[str],
    claude_path: Path,
    agents_path: Path,
) -> int:
    """--force in canonical-pair mode: rewrite CLAUDE.md from scratch + recreate symlink."""
    # If --link points at CLAUDE.md itself, that's a config error.
    link_path: Optional[Path]
    if args.no_link:
        link_path = None
    elif args.link is not None:
        link_path = Path(args.link)
        if os.path.abspath(str(link_path)) == os.path.abspath(str(claude_path)):
            print(
                f"error: --link path is the same as the canonical CLAUDE.md ({claude_path})",
                file=sys.stderr,
            )
            return 2
    else:
        link_path = agents_path

    if args.dry_run:
        print(f"# would clobber {claude_path.name}:")
        print(block)
        if link_path is not None:
            rel_target = os.path.relpath(str(claude_path), start=str(link_path.parent))
            print()
            print(f"(would also (re)create symlink: {_rel_to_cwd(link_path)} -> {rel_target})")
        return 0

    try:
        verb, _ = _apply_to_file(claude_path, block, force=True)
    except OSError as e:
        print(f"error: could not write {claude_path}: {e}", file=sys.stderr)
        return 1

    lines = [f"{verb} {claude_path.name}  ({len(ordered)} profile(s): {', '.join(ordered)})"]
    if link_path is not None:
        err = _make_relative_symlink(link_path, claude_path, force=True)
        if err is not None:
            print("\n".join(lines))
            print(f"warning: symlink not created: {err}", file=sys.stderr)
            return 1
        rel_target = os.path.relpath(str(claude_path), start=str(link_path.parent))
        lines.append(f"linked {_rel_to_cwd(link_path)} -> {rel_target}")
    print("\n".join(lines))
    return 0


def _preview_action_for(path: Path, force: bool) -> str:
    """Word for dry-run output: what would happen to `path` if we ran for real."""
    if force and path.exists():
        return "clobber"
    if not path.exists():
        return "write"
    try:
        existing = path.read_text(encoding="utf-8")
    except OSError:
        return "(re)write"
    if _BEGIN_MARKER in existing and _END_MARKER in existing:
        return "update managed block in"
    return "append managed block to"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agent-rails",
        description="Harness-neutral guardrails for LLM coding agents.",
    )
    p.add_argument("--version", action="version", version=f"agent-rails {__version__}")
    sub = p.add_subparsers(dest="command")

    rep = sub.add_parser("report", help="tuning summary of recorded verdicts")
    rep.add_argument("--reset", action="store_true", help="clear the audit log")
    rep.add_argument("--json", action="store_true", help="emit the raw summary as JSON")
    rep.set_defaults(func=_cmd_report)

    st = sub.add_parser("status", help="show resolved config for a directory")
    st.add_argument("dir", nargs="?", help="directory to resolve (default: cwd)")
    st.set_defaults(func=_cmd_status)

    cmds = sub.add_parser("commands", help="list workflow wrappers available in this repo")
    cmds.set_defaults(func=_cmd_commands)

    pfl = sub.add_parser("preflight", help="list or run repo-local preflight scripts")
    pfl.add_argument("name", nargs="?", help="preflight script name under .agent-rails/preflight/")
    pfl.add_argument("preflight_args", nargs=argparse.REMAINDER, metavar="ARG")
    pfl.add_argument("--list", action="store_true", help="list repo-local preflights and exit")
    pfl.set_defaults(func=_cmd_preflight)

    atlas = sub.add_parser("code-atlas", help="map large files to symbol/section line ranges")
    atlas.add_argument("dir", nargs="?", help="repo/directory to map (default: cwd)")
    atlas.add_argument("--glob", help="optional file glob, for example '*.py' or 'src/**'")
    atlas.add_argument("--min-lines", type=int, default=200, help="minimum file size to include (default: 200)")
    atlas.add_argument("--max-files", type=int, default=50, help="maximum files to print (default: 50)")
    atlas.add_argument("--max-entries", type=int, default=80, help="maximum entries per file (default: 80)")
    atlas.add_argument("--json", action="store_true", help="emit versioned structured output")
    atlas.set_defaults(func=_cmd_code_atlas)

    health = sub.add_parser("repo-health", help="show large-file retrieval cost and split hints")
    health.add_argument("dir", nargs="?", help="repo/directory to inspect (default: cwd)")
    health.add_argument("--glob", help="optional file glob, for example '*.py' or 'src/**'")
    health.add_argument("--min-lines", type=int, default=1000, help="minimum file size to include (default: 1000)")
    health.add_argument("--max-files", type=int, default=50, help="maximum files to print (default: 50)")
    health.add_argument("--max-suggestions", type=int, default=8, help="maximum split hints per file (default: 8)")
    health.add_argument("--json", action="store_true", help="emit versioned structured output")
    health.set_defaults(func=_cmd_repo_health)

    def add_locate_args(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("query", help="symbol, text, or desired change to locate")
        parser.add_argument("--dir", help="repo/directory to search (default: cwd)")
        parser.add_argument("--glob", help="optional file glob, for example '*.py' or 'src/**'")
        parser.add_argument("--max-results", type=int, default=8, help="maximum ranked ranges to print (default: 8)")
        parser.add_argument("--context-lines", type=int, default=80, help="maximum lines per suggested range (default: 80)")

    loc = sub.add_parser("locate", help="rank code ranges matching a query without printing file contents")
    add_locate_args(loc)
    loc.set_defaults(func=_cmd_locate, symbol=False)

    loc_sym = sub.add_parser("locate-symbol", help="rank definition-ish ranges for a symbol")
    add_locate_args(loc_sym)
    loc_sym.set_defaults(func=_cmd_locate, symbol=True)

    loc_edit = sub.add_parser("locate-edit", help="rank ranges likely relevant to a desired code change")
    add_locate_args(loc_edit)
    loc_edit.set_defaults(func=_cmd_locate, symbol=False)

    led = sub.add_parser(
        "ledger",
        help="manage repo-local ruled-out records under .ledger/",
    )
    led.add_argument("--dir", help="repo/directory containing .ledger/ (default: nearest repo root)")
    led_sub = led.add_subparsers(dest="ledger_action")
    led_add = led_sub.add_parser("add", help="deposit an evidence-backed ruled-out/dead-end/constraint record")
    led_add.add_argument("--kind", required=True, choices=["ruled-out", "dead-end", "constraint"])
    led_add.add_argument("--claim", required=True, help="the belief or path future agents should not re-walk")
    led_add.add_argument("--evidence", required=True, help="observation that killed the claim")
    led_add.add_argument("--falsifier", help="command or precise observation to re-run")
    led_add.add_argument("--scope", action="append", required=True, help="repo-relative path this record applies to; repeatable")
    led_add.add_argument("--cost", help="rough session cost, for example '~30min'")
    led_add.add_argument("--body", help="optional free-text elaboration")
    led_add.add_argument("--slug", help="optional kebab-case-ish record slug")
    led_add.set_defaults(func=_cmd_ledger)
    led_check = led_sub.add_parser("check", help="refresh blob pins and flag stale records in LEDGER.md")
    led_check.set_defaults(func=_cmd_ledger)
    led_rel = led_sub.add_parser("relevant", help="show records whose scope intersects paths")
    led_rel.add_argument("paths", nargs="+", help="repo-relative paths")
    led_rel.set_defaults(func=_cmd_ledger)
    led_rev = led_sub.add_parser("reverify", help="run a record falsifier; re-pin on failure, retire on success")
    led_rev.add_argument("slug", help="record slug")
    led_rev.add_argument("--timeout", type=float, default=60.0, help="seconds before the falsifier times out (default: 60)")
    led_rev.set_defaults(func=_cmd_ledger)
    led_ret = led_sub.add_parser("retire", help="delete a live record and leave a tombstone in LEDGER.md")
    led_ret.add_argument("slug", help="record slug")
    led_ret.add_argument("--reason", help="optional retirement reason")
    led_ret.set_defaults(func=_cmd_ledger)
    led.set_defaults(func=_cmd_ledger)

    ins = sub.add_parser(
        "install",
        help="install hooks for a harness (no arg = all detected; 'all' = both known)",
    )
    ins.add_argument(
        "harness",
        nargs="?",
        choices=["claude", "claude_code", "codex", "all"],
        help="harness to install for; omit to auto-detect ~/.claude and ~/.codex",
    )
    ins.set_defaults(func=_cmd_install)

    prc = sub.add_parser(
        "pr-create",
        help="create a GitHub PR using --body-file to avoid shell quoting hazards",
    )
    prc.add_argument("--title", required=True, help="PR title")
    prc.add_argument("--json", action="store_true", help="emit a versioned lifecycle state")
    pr_body = prc.add_mutually_exclusive_group(required=True)
    pr_body.add_argument("--body-file", help="path to the PR body markdown file")
    pr_body.add_argument("--body", help="PR body source; pass '-' to read from stdin")
    prc.add_argument("--base", default="main", help="base branch name (default: main)")
    prc.add_argument("--head", help="head branch name (default: current branch per gh)")
    prc.add_argument("--remote", default="origin", help="remote to push the current branch when no upstream exists (default: origin)")
    prc.add_argument("--draft", action="store_true", help="create the PR as a draft")
    prc.add_argument(
        "--command-timeout",
        type=float,
        default=30,
        help="seconds before one gh call times out (default: 30)",
    )
    prc.set_defaults(func=_cmd_pr_create)

    prm = sub.add_parser(
        "pr-merge",
        help="wait for CI checks to pass, merge a GitHub PR, wait for MERGED, then clean up the local branch",
    )
    prm.add_argument("pr", help="PR number, URL, or branch accepted by `gh pr merge`")
    prm.add_argument(
        "--method",
        choices=["squash", "merge", "rebase"],
        default="squash",
        help="merge method (default: squash)",
    )
    prm.add_argument("--no-cleanup", action="store_true", help="skip local post-merge cleanup")
    prm.add_argument("--json", action="store_true", help="emit a versioned lifecycle state")
    prm.add_argument(
        "--skip-ci-reason",
        help="bypass the CI gate; auditable reason for a local-validation-only merge",
    )
    prm.add_argument("--main", default="main", help="main branch name (default: main)")
    prm.add_argument("--remote", default="origin", help="remote for fast-forward pull (default: origin)")
    prm.add_argument("--timeout", type=int, default=120, help="seconds to wait for MERGED (default: 120)")
    prm.add_argument("--poll", type=float, default=5, help="seconds between PR state polls (default: 5)")
    prm.add_argument(
        "--ci-timeout",
        type=int,
        default=1800,
        help="seconds to wait for CI checks to pass before refusing to merge (default: 1800)",
    )
    prm.add_argument(
        "--ci-poll",
        type=float,
        default=30,
        help="seconds between CI check polls (default: 30)",
    )
    prm.add_argument(
        "--command-timeout",
        type=float,
        default=30,
        help="seconds before one gh/git call times out (default: 30)",
    )
    prm.set_defaults(func=_cmd_pr_merge)

    pmc = sub.add_parser(
        "post-merge-cleanup",
        help="checkout main, fast-forward pull, and delete a merged local branch",
    )
    pmc.add_argument("branch", nargs="?", help="merged branch to delete (default: current branch)")
    pmc.add_argument("--main", default="main", help="main branch name (default: main)")
    pmc.add_argument("--remote", default="origin", help="remote for fast-forward pull (default: origin)")
    pmc.add_argument("--dry-run", action="store_true", help="print commands without running them")
    pmc.add_argument(
        "--force-delete",
        action="store_true",
        help="use git branch -D for squash/rebase-merged branches",
    )
    pmc.add_argument(
        "--command-timeout",
        type=float,
        default=30,
        help="seconds before one git call times out (default: 30)",
    )
    pmc.set_defaults(func=_cmd_post_merge_cleanup)

    cis = sub.add_parser("ci-status", help="summarize GitHub PR checks")
    cis.add_argument("pr", nargs="?", help="PR number, URL, or branch for `gh pr checks`")
    cis.add_argument("--wait", action="store_true", help="poll with backoff until checks finish or timeout")
    cis.add_argument("--timeout", type=int, default=600, help="seconds to wait with --wait (default: 600)")
    cis.add_argument("--poll", type=float, default=10, help="initial seconds between --wait polls (default: 10; backs off up to 60)")
    cis.add_argument("--json", action="store_true", help="emit a versioned lifecycle state")
    cis.add_argument(
        "--command-timeout",
        type=float,
        default=30,
        help="seconds before one gh call times out (default: 30)",
    )
    cis.set_defaults(func=_cmd_ci_status)

    cip = sub.add_parser("ci-preflight", help="classify GitHub CI quota/infrastructure readiness")
    cip.add_argument("pr", nargs="?", help="PR number, URL, or branch for `gh pr checks`")
    cip.add_argument(
        "--command-timeout",
        type=float,
        default=30,
        help="seconds before one gh call times out (default: 30)",
    )
    cip.set_defaults(func=_cmd_ci_preflight)

    cif = sub.add_parser("ci-failures", help="extract pytest-style failures from a failed GitHub run")
    cif.add_argument("pr_arg", nargs="?", help="PR number, URL, or branch shorthand for --pr")
    cif.add_argument("--run", help="GitHub Actions run id (default: latest run)")
    cif.add_argument("--pr", help="find the latest run for this PR's head branch")
    cif.add_argument(
        "--command-timeout",
        type=float,
        default=30,
        help="seconds before one gh call times out (default: 30)",
    )
    cif.set_defaults(func=_cmd_ci_failures)

    ts = sub.add_parser("test-summary", help="summarize pytest failures from a saved log")
    ts.add_argument("log", help="path to a pytest output log")
    ts.set_defaults(func=_cmd_test_summary)

    init = sub.add_parser(
        "init",
        help="compose a CLAUDE.md + AGENTS.md symlink from packaged workflow profiles",
    )
    init.add_argument(
        "--profile",
        action="append",
        metavar="NAME",
        help="add a profile (repeatable; comma-separated also accepted). "
        "Defaults to the existing managed profile set on re-run, otherwise: "
        + ", ".join(DEFAULT_PROFILES),
    )
    init.add_argument("--list", action="store_true", help="list available profiles and exit")
    init.add_argument("--dry-run", action="store_true", help="print rendered content; no file writes")
    init.add_argument("--force", action="store_true", help="overwrite an existing output file/symlink")
    init.add_argument(
        "--out",
        metavar="PATH",
        help="output path (default: ./CLAUDE.md)",
    )
    init.add_argument(
        "--link",
        metavar="PATH",
        help="also create a relative symlink at PATH pointing at the output "
        "(default: ./AGENTS.md when --out is the default)",
    )
    init.add_argument(
        "--no-link",
        action="store_true",
        help="suppress the default AGENTS.md symlink",
    )
    init.set_defaults(func=_cmd_init)

    sub.add_parser("version", help="print version").set_defaults(
        func=lambda _a: (print(f"agent-rails {__version__}"), 0)[1]
    )

    bgt = sub.add_parser(
        "budget",
        help="inspect, add, or reset a session budget",
        epilog=(
            "examples:\n"
            "  agent-rails budget <session-id>\n"
            "  agent-rails budget <session-id> add 20\n"
            "  agent-rails budget <session-id> reset\n"
            "  agent-rails budget <session-id> reset 20\n"
            "  agent-rails budget <session-id> subagent\n"
            "  agent-rails budget <session-id> add 3 --self\n"
            "  agent-rails budget task-type list\n"
            "  agent-rails budget task-type set <session-id> <type>"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    bgt.add_argument(
        "--self",
        action="store_true",
        dest="self_approve",
        help=argparse.SUPPRESS,
    )
    bgt.add_argument(
        "--subagent",
        action="store_true",
        dest="approve_subagent",
        help=argparse.SUPPRESS,
    )
    bgt.add_argument("budget_args", nargs=argparse.REMAINDER, metavar="...")
    bgt.set_defaults(func=_cmd_budget, _parser=bgt)

    recover = sub.add_parser(
        "recover",
        help="show a bounded session handoff or reset detector state",
    )
    recover.add_argument("session_id", help="session identifier from a tripwire")
    recover.add_argument(
        "action", nargs="?", choices=["handoff", "reset"], default="handoff",
        help="handoff (default) or explicit detector-state reset",
    )
    recover.set_defaults(func=_cmd_recover)

    delegation = sub.add_parser(
        "delegation", help="show observed subagent lifecycle state for a session",
    )
    delegation.add_argument("session_id", help="parent session identifier")
    delegation.add_argument("action", nargs="?", choices=["show", "reset"], default="show")
    delegation.set_defaults(func=_cmd_delegation)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
