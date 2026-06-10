"""Normalized event schema — the harness-neutral lingua franca.

Every adapter translates its harness's native tool-call payload into a
ToolEvent before handing it to the core. The core (state, engine, detectors)
knows nothing about Claude Code, Codex, or any other harness — it only ever
sees ToolEvents.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from typing import Any

# status values
OK = "ok"
ERROR = "error"
PENDING = "pending"  # a candidate call (PreToolUse) whose outcome isn't known yet
BLOCKED = "blocked"  # an enforced block we recorded: the call was DENIED, not run


@dataclass
class ToolEvent:
    session_id: str
    tool: str
    arg_hash: str
    status: str  # OK | ERROR | PENDING | BLOCKED
    ts: float
    args_complete: bool = True
    arg_kind: str = ""
    arg_preview: str = ""
    output_hash: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @staticmethod
    def from_json(line: str) -> "ToolEvent":
        d = json.loads(line)
        return ToolEvent(
            session_id=str(d["session_id"]),
            tool=str(d["tool"]),
            arg_hash=str(d["arg_hash"]),
            status=str(d["status"]),
            ts=float(d["ts"]),
            args_complete=bool(d.get("args_complete", True)),
            arg_kind=str(d.get("arg_kind", "")),
            arg_preview=str(d.get("arg_preview", "")),
            output_hash=str(d.get("output_hash", "")),
        )

    # --- factories: the ONE place a ToolEvent is built from raw args -------
    # Adapters call these instead of hand-wiring hash_args/time.time(), so a
    # schema change (new field, different hashing) touches exactly one site.

    @classmethod
    def candidate(cls, session_id: str, tool: str, args: Any) -> "ToolEvent":
        """A call about to run (PreToolUse / check); outcome unknown."""
        norm = normalize_tool_args(tool, args)
        return cls(
            session_id, tool, hash_args(norm.value), PENDING, time.time(),
            args_complete=norm.complete, arg_kind=norm.kind, arg_preview=norm.preview,
        )

    @classmethod
    def record(cls, session_id: str, tool: str, args: Any, ok: bool, output: Any = None) -> "ToolEvent":
        """A completed call with a known outcome (PostToolUse / observe)."""
        norm = normalize_tool_args(tool, args)
        return cls(
            session_id, tool, hash_args(norm.value), OK if ok else ERROR, time.time(),
            args_complete=norm.complete, arg_kind=norm.kind, arg_preview=norm.preview,
            output_hash=hash_output(output),
        )

    @classmethod
    def blocked(cls, session_id: str, tool: str, args: Any) -> "ToolEvent":
        """A call we DENIED in enforce mode. Recorded so the history reflects the
        intervention: a blocked call never runs, so no PostToolUse follows it.
        Without this marker, a candidate-independent detector (error_streak)
        would keep blocking every subsequent call — the denied calls produce no
        success to reset the streak — and wedge the agent permanently. The
        marker is not an ERROR, so it breaks the streak and lets the agent run
        the diagnostic the block asked for; it carries the candidate's hash, so
        an identical *retry* still matches and stays blocked under repetition."""
        norm = normalize_tool_args(tool, args)
        return cls(
            session_id, tool, hash_args(norm.value), BLOCKED, time.time(),
            args_complete=norm.complete, arg_kind=norm.kind, arg_preview=norm.preview,
        )


@dataclass
class NormalizedArgs:
    value: Any
    complete: bool
    kind: str
    preview: str


def _tool_lower(tool: str) -> str:
    return str(tool or "").strip().lower()


def _is_shell_tool(tool: str) -> bool:
    tl = _tool_lower(tool)
    return tl in {"bash", "functions.exec_command", "exec_command"}


def _dig_path(obj: Any, path: tuple[str, ...]) -> Any:
    cur = obj
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur.get(key)
    return cur


def _first_str(obj: Any, paths: tuple[tuple[str, ...], ...]) -> str:
    for path in paths:
        val = _dig_path(obj, path)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def _redact_preview(s: str) -> str:
    out = str(s or "")
    out = re.sub(r"(?i)(api[_-]?key|token|secret|password|passwd|pwd)=\S+", r"\1=<redacted>", out)
    out = re.sub(r"(?i)(bearer)\s+[A-Za-z0-9._~+/-]+=*", r"\1 <redacted>", out)
    return out


def _preview(s: str, limit: int = 320) -> str:
    compact = re.sub(r"\s+", " ", _redact_preview(str(s or ""))).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def normalize_tool_args(tool: str, args: Any) -> NormalizedArgs:
    """Return the repeat-detection identity for a raw tool payload.

    Adapter payloads are not security boundaries: harnesses can rename fields or
    omit nested input. A missing command must fail open, not collapse every Bash
    call to the same `{}` hash and become enforceable repetition.
    """
    tl = _tool_lower(tool)
    if _is_shell_tool(tool):
        command = _first_str(args, (
            ("command",), ("cmd",),
            ("parameters", "command"), ("parameters", "cmd"),
            ("arguments", "command"), ("arguments", "cmd"),
            ("args", "command"), ("args", "cmd"),
            ("tool_input", "command"), ("tool_input", "cmd"),
        ))
        if not command:
            return NormalizedArgs(
                value={"tool": "Bash", "missing_command": True},
                complete=False,
                kind="shell:missing-command",
                preview="<missing Bash command>",
            )
        return NormalizedArgs(
            value={"tool": "Bash", "command": command},
            complete=True,
            kind=_classify_shell_command(command),
            preview=_preview(command),
        )
    return NormalizedArgs(value=args, complete=True, kind=tl or "tool", preview=_preview(args))


def _classify_shell_command(command: str) -> str:
    s = str(command or "").strip()
    first = s.split(None, 1)[0] if s else ""
    base = first.rsplit("/", 1)[-1]
    read_only = {
        "awk", "cat", "find", "grep", "head", "jq", "ls", "nl", "pwd", "rg",
        "sed", "tail", "wc", "git",
    }
    tests = {"pytest", "tox"}
    builds = {"make", "cmake", "ninja", "cargo", "go", "mvn", "gradle"}
    mutating = {"cp", "mv", "rm", "mkdir", "touch", "git"}
    if base == "git":
        parts = s.split()
        sub = parts[1] if len(parts) > 1 else ""
        if sub in {"status", "diff", "show", "log", "rev-parse", "branch"}:
            return "shell:read-only"
        if sub in {"add", "commit", "checkout", "reset", "merge", "rebase", "push", "pull"}:
            return "shell:mutating"
    if base in read_only:
        return "shell:read-only"
    if base in tests or "test" in s or "check" in s:
        return "shell:test"
    if _is_build_shell_command(base, s, builds):
        return "shell:build"
    if base in mutating or any(op in s for op in (" >", ">>", " 2>", " | tee ")):
        return "shell:mutating"
    if base in {"python", "python3", "node", "ruby", "perl"} and "<<" in s:
        return "shell:diagnostic-script"
    return "shell"


def _is_build_shell_command(base: str, command: str, build_bins: set[str]) -> bool:
    if base in build_bins:
        return True
    parts = command.split()
    if base in {"bash", "sh", "zsh"} and len(parts) > 1:
        script = parts[1].rsplit("/", 1)[-1].lower()
        if "build" in script or "rebuild" in script:
            return True
    if base in {"npm", "pnpm", "yarn"} and any(part == "build" for part in parts[1:]):
        return True
    return False


def hash_output(output: Any) -> str:
    """Stable short hash of a completed tool's result text. Empty on no output."""
    if output is None:
        return ""
    try:
        if isinstance(output, dict):
            parts: list[str] = []
            for key in ("stdout", "stderr", "output", "text", "error"):
                val = output.get(key)
                if isinstance(val, str) and val:
                    parts.append(f"{key}:{val}")
            if not parts:
                return ""
            for key in ("exit_code", "exitCode", "returncode", "return_code", "status"):
                if key in output:
                    parts.append(f"{key}:{output.get(key)}")
            raw = "\n".join(parts)
        else:
            raw = str(output)
    except Exception:
        raw = repr(output)
    if not raw:
        return ""
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]

def _nonjson(o: Any):
    # Tag non-serializable values with their type so e.g. the set {1,2,3} does
    # NOT collide with the plain string "{1, 2, 3}" (which a bare repr would).
    return {"__nonjson__": type(o).__name__, "repr": repr(o)}


def hash_args(args: Any) -> str:
    """Stable short hash of tool arguments, for repetition detection.

    Identical (tool, arg_hash) recurring across calls is the strongest
    flailing signal and the one with the lowest false-positive rate: an agent
    making progress varies its calls; an agent in a doom loop repeats. We hash
    a canonical JSON form so key ordering doesn't matter, and tag any
    non-serializable value with its type so distinct values can't collapse to
    the same string. Never raises.
    """
    try:
        canonical = json.dumps(args, sort_keys=True, default=_nonjson)
    except Exception:
        canonical = repr(args)
    return hashlib.sha256(canonical.encode("utf-8", "replace")).hexdigest()[:16]
