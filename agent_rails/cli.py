"""`agent-rails` command-line entry point.

A thin operator-facing CLI over the same core the hooks use. Subcommands:

    agent-rails report [--reset]   tuning summary: what fired, would-block rates
    agent-rails status [DIR]       resolved config for DIR (default: cwd)
    agent-rails install [HARNESS]  install hooks; no arg = all detected harnesses
    agent-rails init [...]         compose a CLAUDE.md + AGENTS.md symlink from profiles
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
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

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


def _resolve_link_path(
    out_was_default: bool,
    out_path: Path,
    explicit_link: Optional[str],
    no_link: bool,
) -> Optional[Path]:
    """Decide where (if anywhere) the sibling symlink should go.

    Defaults: with `agent-rails init` (no flags) we write CLAUDE.md AND drop
    AGENTS.md as a relative symlink. A custom --out implies single-file
    intent unless --link is also given. --no-link suppresses the default.
    """
    if no_link:
        return None
    if explicit_link is not None:
        return Path(explicit_link)
    if out_was_default:
        return out_path.parent / "AGENTS.md"
    return None


def _rel_to_cwd(p: Path) -> Path:
    try:
        return p.relative_to(Path.cwd())
    except ValueError:
        return p


def _make_relative_symlink(link_path: Path, target_path: Path, force: bool) -> Optional[str]:
    """Create `link_path` as a relative symlink pointing at `target_path`.

    Returns an error message on failure (the OUT file write succeeded first,
    so the caller can decide how loud to be). Refuses to clobber an existing
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
    out_was_default = args.out is None
    out_path = Path(args.out) if args.out else Path.cwd() / "CLAUDE.md"
    link_path = _resolve_link_path(out_was_default, out_path, args.link, args.no_link)

    if link_path is not None:
        # Compare LITERAL paths, not symlink-followed paths: if a previous
        # run already created the symlink, link_path.resolve() would follow
        # it to out_path and falsely trip this guard. os.path.abspath
        # normalizes `..`/`.` without dereferencing the final component.
        if os.path.abspath(str(link_path)) == os.path.abspath(str(out_path)):
            print(
                f"error: --link path is the same as --out ({out_path})",
                file=sys.stderr,
            )
            return 2

    if args.dry_run:
        print(content, end="" if content.endswith("\n") else "\n")
        if link_path is not None:
            rel_target = os.path.relpath(str(out_path), start=str(link_path.parent))
            print(f"(would also create symlink: {_rel_to_cwd(link_path)} -> {rel_target})")
        return 0

    if out_path.exists() and not args.force:
        print(
            f"error: {out_path} already exists. Re-run with --force to overwrite.",
            file=sys.stderr,
        )
        return 1

    if link_path is not None and (link_path.is_symlink() or link_path.exists()) and not args.force:
        print(
            f"error: {link_path} already exists. Re-run with --force to overwrite.",
            file=sys.stderr,
        )
        return 1

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
    except OSError as e:
        print(f"error: could not write {out_path}: {e}", file=sys.stderr)
        return 1

    summary = (
        f"wrote {_rel_to_cwd(out_path)}  "
        f"({len(ordered)} profile(s): {', '.join(ordered)})"
    )

    if link_path is not None:
        err = _make_relative_symlink(link_path, out_path, args.force)
        if err is not None:
            # The output file was written; surface the symlink failure clearly
            # with a non-zero exit so the user notices.
            print(summary)
            print(f"warning: symlink not created: {err}", file=sys.stderr)
            return 1
        rel_target = os.path.relpath(str(out_path), start=str(link_path.parent))
        summary += f"\nlinked {_rel_to_cwd(link_path)} -> {rel_target}"

    print(summary)
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

    init = sub.add_parser(
        "init",
        help="compose a CLAUDE.md + AGENTS.md symlink from packaged workflow profiles",
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
