"""`agent-rails` command-line entry point.

A thin operator-facing CLI over the same core the hooks use. Subcommands:

    agent-rails report [--reset]   tuning summary: what fired, would-block rates
    agent-rails status [DIR]       resolved config for DIR (default: cwd)
    agent-rails install HARNESS    run the bundled installer (claude | codex)
    agent-rails version

`report` is the other half of `observe` mode: observe logs every non-ALLOW
verdict to the audit log; `report` reads it back so you can tune thresholds
against your real workflow before flipping to `enforce`.

Nothing here can block a tool call — it's all read/aggregate plus a wrapper
around the install scripts.
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
