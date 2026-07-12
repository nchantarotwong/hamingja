#!/usr/bin/env bash
#
# Install the agent-rails Codex adapter into your global hooks.json.
#
# Registers:
#   PreToolUse  -> tripwire.py  (allow / nudge / block)
#   PostToolUse -> record.py    (record success/error from tool_response)
#   SubagentStart/Stop -> delegation.py (identity + active-child lifecycle)
#   UserPromptSubmit -> operator_turn.py (prompt-free operator recency)
# for matcher "*".
#
# Behavior:
#   * MERGES into existing hooks (never overwrites); preserves other hooks.
#   * Idempotent AND self-healing: an existing entry that references our script
#     by basename is UPDATED in place, so moving the repo refreshes the path.
#   * Backs up hooks.json ONLY when a change is actually written.
#   * Default detector mode is "observe"; operator resource authority is
#     configured separately.
#
# Override the hooks path with CODEX_HOOKS=/path/to/hooks.json.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
HOOKS="${CODEX_HOOKS:-$CODEX_HOME/hooks.json}"
PRE="$REPO_ROOT/agent_rails/adapters/codex/tripwire.py"
POST="$REPO_ROOT/agent_rails/adapters/codex/record.py"
LIFECYCLE="$REPO_ROOT/agent_rails/adapters/delegation.py"
OPERATOR="$REPO_ROOT/agent_rails/adapters/operator_turn.py"

PYBIN="$(command -v python3 || command -v python || true)"
if [ -z "$PYBIN" ]; then
    echo "error: python3 not found on PATH" >&2
    exit 1
fi

case "$PYBIN" in
    */shims/*|*/.venv/*|*/venv/*|*/conda/*|*/envs/*|*/miniconda*|*/anaconda*)
        echo "warning: python interpreter '$PYBIN' looks like a pyenv/venv/conda shim." >&2
        echo "         If that environment is changed or removed, the hooks silently stop" >&2
        echo "         working (they fail open). Consider a stable system python." >&2
        ;;
esac

mkdir -p "$(dirname "$HOOKS")"
[ -f "$HOOKS" ] || echo '{"hooks": {}}' > "$HOOKS"

BACKUP="$HOOKS.bak.$(date +%s).$$"
cp "$HOOKS" "$BACKUP"

set +e
RESULT="$("$PYBIN" - "$HOOKS" "$PRE" "$POST" "$LIFECYCLE" "$OPERATOR" "$PYBIN" <<'PY'
import json, os, sys

hooks_path, pre, post, lifecycle, operator, pybin = sys.argv[1:7]

try:
    with open(hooks_path, encoding="utf-8") as fh:
        cfg = json.load(fh)
except Exception as exc:
    print(f"error: refusing to modify malformed hooks config: {exc}", file=sys.stderr)
    raise SystemExit(2)
if not isinstance(cfg, dict):
    print("error: refusing to modify hooks config whose top level is not an object", file=sys.stderr)
    raise SystemExit(2)

before = json.dumps(cfg, sort_keys=True)

hooks = cfg.get("hooks")
if hooks is None:
    hooks = {}
    cfg["hooks"] = hooks
elif not isinstance(hooks, dict):
    print("error: refusing to modify config whose hooks field is not an object", file=sys.stderr)
    raise SystemExit(2)


def quote(p):
    return '"' + p.replace('\\', '\\\\').replace('"', '\\"') + '"'


def is_ours(h, base, status):
    cmd = str(h.get("command", "")).replace("\\", "/")
    return (
        base in cmd
        and "agent_rails/adapters/" in cmd
    ) or h.get("statusMessage") == status


def upsert(event, script, status):
    cmd = quote(pybin) + " " + quote(script)
    base = os.path.basename(script)
    entries = hooks.get(event)
    if not isinstance(entries, list):
        entries = []
        hooks[event] = entries
    for matcher_obj in entries:
        if not isinstance(matcher_obj, dict):
            continue
        hk = matcher_obj.get("hooks")
        if not isinstance(hk, list):
            continue
        for h in hk:
            if isinstance(h, dict) and is_ours(h, base, status):
                h["command"] = cmd
                h["type"] = "command"
                h["statusMessage"] = status
                return
    entries.append({
        "matcher": "*",
        "hooks": [{
            "type": "command",
            "command": cmd,
            "statusMessage": status,
        }],
    })


upsert("PreToolUse", pre, "Checking agent-rails")
upsert("PostToolUse", post, "Recording agent-rails")
upsert("SubagentStart", lifecycle, "Recording agent-rails subagent start")
upsert("SubagentStop", lifecycle, "Recording agent-rails subagent stop")
upsert("UserPromptSubmit", operator, "Recording agent-rails operator turn")

after = json.dumps(cfg, sort_keys=True)
if after == before:
    print("UNCHANGED")
else:
    with open(hooks_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")
    print("CHANGED")
PY
)"
RC=$?
set -e
if [ "$RC" -ne 0 ]; then
    if [ "$RC" -eq 2 ]; then
        rm -f "$BACKUP"
    else
        echo "backup preserved after installer failure: $BACKUP" >&2
    fi
    exit "$RC"
fi

if [ "$RESULT" = "CHANGED" ]; then
    echo "updated:  $HOOKS"
    echo "backup:   $BACKUP"
else
    rm -f "$BACKUP"
    echo "no change: $HOOKS already up to date"
fi

echo "detectors: observe by default (operator resource authority is separate)"
echo
echo "Review/trust hooks in Codex with /hooks if prompted."
echo "Opt out per repo: touch .agent-rails-off in that project's root."
echo "Uninstall: agent-rails uninstall codex"
