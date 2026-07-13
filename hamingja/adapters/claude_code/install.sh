#!/usr/bin/env bash
#
# Install the hamingja Claude Code adapter into your global settings.json.
#
# Registers:
#   PreToolUse        -> tripwire.py  (allow / nudge / block)
#   PostToolUse       -> record.py    (record success, heuristic fallback)
#   PostToolUseFailure-> record.py    (record failure, authoritative)
#   SubagentStart/Stop-> delegation.py (identity + active-child lifecycle)
#   UserPromptSubmit  -> operator_turn.py (prompt-free operator recency)
# all for matcher "*".
#
# Behavior:
#   * MERGES into existing settings (never overwrites); preserves other hooks.
#   * Idempotent AND self-healing: an existing entry that references our script
#     (matched by basename) is UPDATED in place, so moving/renaming the repo
#     refreshes the path instead of leaving a dead duplicate.
#   * Backs up settings ONLY when a change is actually written (no backup litter
#     on no-op re-runs).
#   * Default detector mode is "observe"; operator resource authority is
#     configured separately.
#
# Override the settings path with CLAUDE_SETTINGS=/path/to/settings.json.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SETTINGS="${CLAUDE_SETTINGS:-$HOME/.claude/settings.json}"
PRE="$REPO_ROOT/hamingja/adapters/claude_code/tripwire.py"
POST="$REPO_ROOT/hamingja/adapters/claude_code/record.py"
LIFECYCLE="$REPO_ROOT/hamingja/adapters/delegation.py"
OPERATOR="$REPO_ROOT/hamingja/adapters/operator_turn.py"

PYBIN="${HAMINGJA_PYTHON:-}"
if [ -z "$PYBIN" ]; then
    PYBIN="$(command -v python3 || command -v python || true)"
fi
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

mkdir -p "$(dirname "$SETTINGS")"
[ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"

BACKUP="$SETTINGS.bak.$(date +%s).$$"
cp "$SETTINGS" "$BACKUP"

set +e
RESULT="$("$PYBIN" - "$SETTINGS" "$PRE" "$POST" "$LIFECYCLE" "$OPERATOR" "$PYBIN" <<'PY'
import json, os, shlex, sys, tempfile

settings_path, pre, post, lifecycle, operator, pybin = sys.argv[1:7]
settings_path = os.path.realpath(settings_path)

try:
    with open(settings_path, encoding="utf-8") as fh:
        cfg = json.load(fh)
except Exception as exc:
    print(f"error: refusing to modify malformed settings: {exc}", file=sys.stderr)
    raise SystemExit(2)
if not isinstance(cfg, dict):
    print("error: refusing to modify settings whose top level is not an object", file=sys.stderr)
    raise SystemExit(2)

before = json.dumps(cfg, sort_keys=True)

hooks = cfg.get("hooks")
if hooks is None:
    hooks = {}
    cfg["hooks"] = hooks
elif not isinstance(hooks, dict):
    print("error: refusing to modify settings whose hooks field is not an object", file=sys.stderr)
    raise SystemExit(2)


def quote(p):
    return '"' + p.replace('\\', '\\\\').replace('"', '\\"') + '"'


def owns_command(value, base):
    try:
        parts = shlex.split(str(value))
    except ValueError:
        return False
    return any(
        os.path.basename(part) == base
        and any(
            owned in part.replace("\\", "/")
            for owned in ("hamingja/adapters/", "agent_rails/adapters/")
        )
        for part in parts
    )


def upsert(event, script):
    cmd = quote(pybin) + " " + quote(script)
    base = os.path.basename(script)
    entries = hooks.get(event)
    if entries is None:
        entries = []
        hooks[event] = entries
    elif not isinstance(entries, list):
        print(f"error: refusing to replace malformed {event} hooks", file=sys.stderr)
        raise SystemExit(2)
    # refresh any existing entry that references our script (by basename)
    for matcher_obj in entries:
        if not isinstance(matcher_obj, dict):
            continue
        hk = matcher_obj.get("hooks")
        if not isinstance(hk, list):
            continue
        for h in hk:
            if not isinstance(h, dict):
                continue
            if owns_command(h.get("command", ""), base):
                h["command"] = cmd
                h["type"] = "command"
                return
    entries.append({"matcher": "*", "hooks": [{"type": "command", "command": cmd}]})


upsert("PreToolUse", pre)
upsert("PostToolUse", post)
upsert("PostToolUseFailure", post)
upsert("SubagentStart", lifecycle)
upsert("SubagentStop", lifecycle)
upsert("UserPromptSubmit", operator)

after = json.dumps(cfg, sort_keys=True)
if after == before:
    print("UNCHANGED")
else:
    directory = os.path.dirname(settings_path) or "."
    fd, temporary = tempfile.mkstemp(prefix=".hamingja-install-", dir=directory)
    try:
        os.fchmod(fd, os.stat(settings_path).st_mode & 0o7777)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, settings_path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
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
    echo "updated:  $SETTINGS"
    echo "backup:   $BACKUP"
else
    rm -f "$BACKUP"
    echo "no change: $SETTINGS already up to date"
fi

echo "detectors: observe by default (operator resource authority is separate)"
echo
echo "Opt out per repo: touch .hamingja-off in that project's root."
echo "Uninstall: hamingja uninstall claude"
