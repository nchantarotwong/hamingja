#!/usr/bin/env bash
#
# Install the agent-rails Claude Code adapter into your global settings.json.
#
# Registers a PreToolUse tripwire and a PostToolUse recorder for matcher "*".
# MERGES into existing settings (never overwrites) and backs up first.
# Idempotent: re-running won't duplicate the hooks.
#
# The default mode is "observe" — nothing is blocked until you flip
# config/config.default.json (or a per-project .agent-rails.json) to
# "mode": "enforce". Install safely, watch the [observe] nudges, then enforce.
#
# Override the settings path with CLAUDE_SETTINGS=/path/to/settings.json.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SETTINGS="${CLAUDE_SETTINGS:-$HOME/.claude/settings.json}"
PRE="$REPO_ROOT/agent_rails/adapters/claude_code/tripwire.py"
POST="$REPO_ROOT/agent_rails/adapters/claude_code/record.py"

PYBIN="$(command -v python3 || command -v python || true)"
if [ -z "$PYBIN" ]; then
    echo "error: python3 not found on PATH" >&2
    exit 1
fi

mkdir -p "$(dirname "$SETTINGS")"
[ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"

BACKUP="$SETTINGS.bak.$(date +%s)"
cp "$SETTINGS" "$BACKUP"

"$PYBIN" - "$SETTINGS" "$PRE" "$POST" "$PYBIN" <<'PY'
import json, sys

settings_path, pre, post, pybin = sys.argv[1:5]

with open(settings_path, encoding="utf-8") as fh:
    try:
        cfg = json.load(fh)
    except Exception:
        cfg = {}

hooks = cfg.setdefault("hooks", {})

def ensure(event, script):
    cmd = f"{pybin} {script}"
    entries = hooks.setdefault(event, [])
    # already installed? (match by our script path appearing in any command)
    for matcher_obj in entries:
        for h in matcher_obj.get("hooks", []):
            if script in str(h.get("command", "")):
                return False
    entries.append({
        "matcher": "*",
        "hooks": [{"type": "command", "command": cmd}],
    })
    return True

added_pre = ensure("PreToolUse", pre)
added_post = ensure("PostToolUse", post)

with open(settings_path, "w", encoding="utf-8") as fh:
    json.dump(cfg, fh, indent=2)
    fh.write("\n")

print(f"PreToolUse  tripwire: {'added' if added_pre else 'already present'}")
print(f"PostToolUse recorder: {'added' if added_post else 'already present'}")
PY

echo "settings: $SETTINGS"
echo "backup:   $BACKUP"
echo "mode:     observe (nothing is blocked until you set mode=enforce)"
echo
echo "Opt out per repo: touch .agent-rails-off in that project's root."
echo "Uninstall: restore the backup, or remove the two agent-rails hook entries."
