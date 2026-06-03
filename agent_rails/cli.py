"""`agent-rails` command-line entry point.

A thin operator-facing CLI over the same core the hooks use. Subcommands:

    agent-rails report [--reset]   tuning summary: what fired, would-block rates
    agent-rails status [DIR]       resolved config for DIR (default: cwd)
    agent-rails install HARNESS    run the bundled installer (claude | codex)
    agent-rails init [...]         compose an AGENTS.md from soft workflow profiles
    agent-rails version

`report` is the other half of `observe` mode: observe logs every non-ALLOW
verdict to the audit log; `report` reads it back so you can tune thresholds
against your real workflow before flipping to `enforce`.

`init` is the offline doc generator for the soft workflow layer; it composes
an AGENTS.md from packaged profiles and writes it into the project.

Nothing here can block a tool call — it's all read/aggregate plus wrappers
around install scripts and file generation.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from . import __version__
from .config import load_config
from .core.audit import clear_audit, read_audit, summarize
from .profiles import (
    ALL_PROFILES,
    DEFAULT_PROFILES,
    normalize as _normalize_profile,
    read_profile,
)
from .templates import ROOT_TEMPLATE, read_template


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
    print(f"  would-block:   {s['would_blocks']}   (these become BLOCKS under enforce)")
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


_HARNESS_ALIASES = {"claude": "claude_code"}


def _cmd_install(args: argparse.Namespace) -> int:
    harness = _HARNESS_ALIASES.get(args.harness, args.harness)
    script = (
        Path(__file__).resolve().parent
        / "adapters"
        / harness
        / "install.sh"
    )
    if not script.exists():
        print(f"error: no installer for harness {args.harness!r}", file=sys.stderr)
        return 1
    try:
        return subprocess.call(["bash", str(script)])
    except FileNotFoundError:
        print("error: bash not found on PATH", file=sys.stderr)
        return 1


def _split_profile_args(values) -> list[str]:
    """Flatten `--profile a --profile b,c` into ['a','b','c']."""
    out: list[str] = []
    for v in values or []:
        for part in str(v).split(","):
            p = part.strip()
            if p:
                out.append(p)
    return out


def _render_agents_md(profile_names: list[str]) -> str:
    parts = [read_template(ROOT_TEMPLATE).rstrip()]
    for name in profile_names:
        parts.append("")  # blank line between sections
        parts.append(read_profile(name).rstrip())
    return "\n".join(parts) + "\n"


def _cmd_init(args: argparse.Namespace) -> int:
    if args.list:
        for name in ALL_PROFILES:
            tag = "  (default)" if name in DEFAULT_PROFILES else ""
            print(f"{name}{tag}")
        return 0

    raw = _split_profile_args(args.profile)
    selected = [_normalize_profile(p) for p in raw] if raw else list(DEFAULT_PROFILES)

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

    content = _render_agents_md(ordered)
    out_path = Path(args.out) if args.out else Path.cwd() / "AGENTS.md"

    if args.dry_run:
        print(content, end="" if content.endswith("\n") else "\n")
        return 0

    if out_path.exists() and not args.force:
        print(
            f"error: {out_path} already exists. Re-run with --force to overwrite.",
            file=sys.stderr,
        )
        return 1

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
    except OSError as e:
        print(f"error: could not write {out_path}: {e}", file=sys.stderr)
        return 1

    rel = out_path
    try:
        rel = out_path.relative_to(Path.cwd())
    except ValueError:
        pass
    print(f"wrote {rel}  ({len(ordered)} profile(s): {', '.join(ordered)})")
    return 0


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

    ins = sub.add_parser("install", help="install hooks for a harness")
    ins.add_argument("harness", choices=["claude", "claude_code", "codex"])
    ins.set_defaults(func=_cmd_install)

    init = sub.add_parser(
        "init",
        help="compose an AGENTS.md from packaged soft workflow profiles",
    )
    init.add_argument(
        "--profile",
        action="append",
        metavar="NAME",
        help="add a profile (repeatable; comma-separated also accepted). "
        "Defaults to: " + ", ".join(DEFAULT_PROFILES),
    )
    init.add_argument("--list", action="store_true", help="list available profiles and exit")
    init.add_argument("--dry-run", action="store_true", help="print rendered content; no file writes")
    init.add_argument("--force", action="store_true", help="overwrite an existing output file")
    init.add_argument(
        "--out",
        metavar="PATH",
        help="output path (default: ./AGENTS.md)",
    )
    init.set_defaults(func=_cmd_init)

    sub.add_parser("version", help="print version").set_defaults(
        func=lambda _a: (print(f"agent-rails {__version__}"), 0)[1]
    )
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
