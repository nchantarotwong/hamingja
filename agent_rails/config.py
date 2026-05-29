"""Config loading + merge.

Resolution order (later overrides earlier):

  1. built-in defaults (below)
  2. packaged config/config.default.json (repo-level defaults)
  3. per-project .agent-rails.json in the project dir (cwd)
  4. AGENT_RAILS_MODE env var (overrides mode only)

Opt-out: a `.agent-rails-off` marker file in the project dir forces mode
"off" (the harness-neutral kill switch). This is the per-repo escape hatch —
drop the file in a repo that legitimately flails and the guard stands down.

Every step is wrapped: a malformed config file degrades to the defaults rather
than raising, so config can never be the reason a call is blocked.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

_DEFAULT = {
    "mode": "observe",  # observe | enforce | off
    "window": 12,
    "detectors": {
        "repetition": {"enabled": True, "nudge_at": 3, "block_at": 4},
        "error_streak": {"enabled": True, "nudge_at": 3, "block_at": 6},
    },
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(project_dir: Optional[str] = None) -> dict:
    cfg = dict(_DEFAULT)

    # 2. packaged repo default
    try:
        pkg_default = Path(__file__).resolve().parent.parent / "config" / "config.default.json"
        if pkg_default.exists():
            cfg = _deep_merge(cfg, json.loads(pkg_default.read_text(encoding="utf-8")))
    except Exception:
        pass

    proj = Path(project_dir or os.getcwd())

    # 3. per-project override
    try:
        override = proj / ".agent-rails.json"
        if override.exists():
            cfg = _deep_merge(cfg, json.loads(override.read_text(encoding="utf-8")))
    except Exception:
        pass

    # opt-out marker wins over everything but is itself overridable by env below
    try:
        if (proj / ".agent-rails-off").exists():
            cfg["mode"] = "off"
    except Exception:
        pass

    # 4. env override (handy for dry-run flips without editing files)
    mode = os.environ.get("AGENT_RAILS_MODE")
    if mode:
        cfg["mode"] = mode

    return cfg
