"""Config loading, trust model, and sanitization.

Trust model (this is the security boundary):

  * The DEFAULTS below and the packaged config/config.default.json are TRUSTED
    — they ship with agent-rails / are set by the operator who installed it.
  * A per-project .agent-rails.json is read from the agent's CURRENT WORKING
    DIRECTORY, i.e. from whatever repo the agent happens to be operating in.
    That is UNTRUSTED input. It may only RELAX the guard (raise thresholds,
    disable detectors, lower the window, downgrade the mode toward "off"). It
    can NEVER tighten — it cannot escalate mode to "enforce" or lower a
    threshold — so a hostile or careless repo cannot brick the agent by
    forcing its first tool call to be denied.
  * The AGENT_RAILS_MODE env var is operator-controlled (the human's shell),
    so it IS trusted and may set any valid mode, including "enforce".

Resolution order:
  1. built-in defaults
  2. packaged config.default.json            (trusted)        -> sanitized baseline
  3. per-project .agent-rails.json (cwd)      (untrusted)      -> relax-only overlay
  4. .agent-rails-off marker (cwd)            -> mode "off"
  5. AGENT_RAILS_MODE env var                 (trusted)        -> any valid mode

Everything is sanitized: modes are canonicalized to {off,observe,enforce};
window/block_at/nudge_at are coerced to ints with safe floors (so a typo'd or
out-of-range value can neither crash a detector nor cause a spurious block);
and the trusted baseline's window is raised if needed so the configured block
threshold is actually reachable (an unreachable block is a silent fail-open).
"""
from __future__ import annotations

import json
import os
from copy import deepcopy
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

_MODE_RANK = {"off": 0, "observe": 1, "enforce": 2}

# floors that make spurious blocks impossible and keep nudges meaningful
_WINDOW_MIN = 1
_BLOCK_MIN = 2   # block_at < 2 would deny the very first call
_NUDGE_MIN = 1


def _to_int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _canon_mode(v) -> Optional[str]:
    if isinstance(v, str):
        m = v.strip().lower()
        if m in _MODE_RANK:
            return m
    return None


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _clamp_floors(cfg: dict) -> dict:
    cfg["mode"] = _canon_mode(cfg.get("mode")) or "observe"
    cfg["window"] = max(_WINDOW_MIN, _to_int(cfg.get("window"), 12))
    dets = cfg.get("detectors")
    if not isinstance(dets, dict):
        dets = {}
    for d in dets.values():
        if not isinstance(d, dict):
            continue
        d["enabled"] = bool(d.get("enabled", True))
        d["block_at"] = max(_BLOCK_MIN, _to_int(d.get("block_at"), 4))
        d["nudge_at"] = max(_NUDGE_MIN, _to_int(d.get("nudge_at"), 3))
    cfg["detectors"] = dets
    return cfg


def _sanitize_baseline(cfg: dict) -> dict:
    """Clamp the TRUSTED baseline and ensure every enabled block is reachable."""
    cfg = _clamp_floors(cfg)
    max_block = max(
        (d["block_at"] for d in cfg["detectors"].values()
         if isinstance(d, dict) and d.get("enabled")),
        default=0,
    )
    if max_block:
        cfg["window"] = max(cfg["window"], max_block)
    return cfg


def _restrict_merge(baseline: dict, project) -> dict:
    """Overlay an UNTRUSTED project config that may only RELAX, never tighten."""
    out = deepcopy(baseline)
    if not isinstance(project, dict):
        return out

    pm = _canon_mode(project.get("mode"))
    if pm is not None and _MODE_RANK[pm] < _MODE_RANK[out["mode"]]:
        out["mode"] = pm  # only toward less-aggressive

    if "window" in project:
        out["window"] = min(out["window"], _to_int(project.get("window"), out["window"]))

    pdet = project.get("detectors")
    if isinstance(pdet, dict):
        for name, d in out["detectors"].items():
            pd = pdet.get(name)
            if not isinstance(pd, dict):
                continue
            if "enabled" in pd:
                d["enabled"] = bool(d.get("enabled", True)) and bool(pd.get("enabled"))
            if "block_at" in pd:  # raise only
                d["block_at"] = max(d["block_at"], _to_int(pd.get("block_at"), d["block_at"]))
            if "nudge_at" in pd:  # raise only
                d["nudge_at"] = max(d["nudge_at"], _to_int(pd.get("nudge_at"), d["nudge_at"]))
    return out


def load_config(project_dir: Optional[str] = None) -> dict:
    baseline = deepcopy(_DEFAULT)

    # 2. trusted packaged override
    try:
        pkg = Path(__file__).resolve().parent.parent / "config" / "config.default.json"
        if pkg.exists():
            baseline = _deep_merge(baseline, json.loads(pkg.read_text(encoding="utf-8")))
    except Exception:
        pass
    baseline = _sanitize_baseline(baseline)

    proj = Path(project_dir or os.getcwd())

    # 3. untrusted per-project overlay — relax only
    try:
        ov = proj / ".agent-rails.json"
        if ov.exists():
            baseline = _restrict_merge(baseline, json.loads(ov.read_text(encoding="utf-8")))
    except Exception:
        pass

    # 4. opt-out marker (a relaxation)
    try:
        if (proj / ".agent-rails-off").exists():
            baseline["mode"] = "off"
    except Exception:
        pass

    # 5. trusted env override (operator-controlled; may tighten)
    env = _canon_mode(os.environ.get("AGENT_RAILS_MODE"))
    if env is not None:
        baseline["mode"] = env

    # final floor clamp — note: does NOT re-raise window to block_at, so a
    # project's window relaxation persists (an intentional, safe-direction change).
    return _clamp_floors(baseline)
