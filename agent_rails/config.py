"""Config loading, trust model, and sanitization.

Trust model (this is the security boundary):

  * The DEFAULTS below and the packaged config/config.default.json are TRUSTED
    — they ship with agent-rails / are set by the operator who installed it.
  * User-level config under ~/.agent-rails/ is TRUSTED operator state. It may
    tighten (add protected patterns, lower thresholds, set global or detector
    modes to enforce), because it lives outside any repo the agent is editing.
  * A per-project .agent-rails.json is read from the agent's CURRENT WORKING
    DIRECTORY, i.e. from whatever repo the agent happens to be operating in.
    That is UNTRUSTED input. It may only RELAX the guard (raise thresholds,
    disable detectors, lower the window, downgrade global or detector modes
    toward "off"). It can NEVER tighten — it cannot escalate a mode to
    "enforce" or lower a threshold — so a hostile or careless repo cannot
    brick the agent by forcing its first tool call to be denied.
  * The AGENT_RAILS_MODE env var is operator-controlled (the human's shell),
    so it IS trusted and may set any valid mode, including "enforce".

Resolution order:
  1. built-in defaults
  2. packaged config.default.json            (trusted)        -> sanitized baseline
  3. ~/.agent-rails/config.json               (trusted)        -> may tighten
  4. ~/.agent-rails/policies/*.json (matched) (trusted)        -> may tighten
  5. per-project .agent-rails.json (cwd)      (untrusted)      -> relax-only overlay
  6. .agent-rails-off marker (cwd)            -> mode "off"
  7. AGENT_RAILS_MODE env var                 (trusted)        -> any valid mode

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

# Tools whose repeated identical use is normal, not flailing: read-only /
# idempotent queries (re-reading a file, re-grepping, re-listing, polling).
# Exempt from the repetition detector by default so a legitimately repeated
# lookup never trips a block. error_streak still applies — a read that keeps
# ERRORING is still a stuck loop.
_DEFAULT_EXEMPT_TOOLS = [
    "Read", "Glob", "Grep", "LS", "NotebookRead",
    "WebFetch", "WebSearch", "TodoRead",
]

_DEFAULT = {
    "mode": "observe",  # observe | enforce | off
    "window": 12,
    "detectors": {
        "repetition": {
            "enabled": True,
            "nudge_at": 3,
            "block_at": 4,
            "exempt_tools": list(_DEFAULT_EXEMPT_TOOLS),
        },
        "oscillation": {"enabled": True, "nudge_at": 4, "block_at": 6},
        "error_streak": {"enabled": True, "nudge_at": 3, "block_at": 6},
        "workflow_wrapper": {"enabled": True, "nudge_at": 1, "block_at": 2},
        "read_discipline": {
            "enabled": True,
            "large_file_lines": 200,
            "nudge_at": 2,
            "block_at": 3,
            "block_first_read_at_lines": 1000,
        },
        "leverage_fallback": {
            "enabled": True,
            "nudge_at": 1,
            "block_at": 2,
            "lookback": 4,
            "required_patterns": [],
            "fallback_patterns": ["grep ", "rg ", "sed ", "awk "],
            "protected_targets": [],
        },
        "python_command": {"enabled": True, "nudge_at": 1, "block_at": 2},
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


def _to_str_list(v) -> list:
    """Coerce a config value to a clean list of non-empty strings ([] on junk)."""
    if not isinstance(v, list):
        return []
    return [s for s in (str(x).strip() for x in v) if s]


def _canon_mode(v) -> Optional[str]:
    if isinstance(v, str):
        m = v.strip().lower()
        if m in _MODE_RANK:
            return m
    return None


def _effective_detector_mode(cfg: dict, name: str, det_cfg: Optional[dict] = None) -> str:
    if det_cfg is None:
        det_cfg = (cfg.get("detectors", {}) or {}).get(name, {}) or {}
    return _canon_mode(det_cfg.get("mode")) or _canon_mode(cfg.get("mode")) or "observe"


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
        dm = _canon_mode(d.get("mode"))
        if dm is not None:
            d["mode"] = dm
        elif "mode" in d:
            d.pop("mode", None)
        d["enabled"] = bool(d.get("enabled", True))
        d["block_at"] = max(_BLOCK_MIN, _to_int(d.get("block_at"), 4))
        d["nudge_at"] = max(_NUDGE_MIN, _to_int(d.get("nudge_at"), 3))
        if "exempt_tools" in d:
            d["exempt_tools"] = _to_str_list(d.get("exempt_tools"))
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
            if "mode" in pd:
                pm = _canon_mode(pd.get("mode"))
                current = _effective_detector_mode(out, name, d)
                if pm is not None and _MODE_RANK[pm] < _MODE_RANK[current]:
                    d["mode"] = pm  # only toward less-aggressive
            if "block_at" in pd:  # raise only
                d["block_at"] = max(d["block_at"], _to_int(pd.get("block_at"), d["block_at"]))
            if "nudge_at" in pd:  # raise only
                d["nudge_at"] = max(d["nudge_at"], _to_int(pd.get("nudge_at"), d["nudge_at"]))
            if "block_first_read_at_lines" in pd and "block_first_read_at_lines" in d:
                current = _to_int(d.get("block_first_read_at_lines"), 0)
                proposed = _to_int(pd.get("block_first_read_at_lines"), current)
                d["block_first_read_at_lines"] = 0 if proposed <= 0 else max(current, proposed)
            if "exempt_tools" in pd:  # extend only: more exemptions = less blocking
                base_ex = d.get("exempt_tools") if isinstance(d.get("exempt_tools"), list) else []
                merged = list(base_ex)
                for t in _to_str_list(pd.get("exempt_tools")):
                    if t not in merged:
                        merged.append(t)
                d["exempt_tools"] = merged

    pbud = project.get("budget")
    if isinstance(pbud, dict):
        bud = out.setdefault("budget", {})
        for key in ("checkpoint_at", "hard_block_at", "nudge_at"):
            if key in pbud:
                bud[key] = max(_to_int(bud.get(key), 1), _to_int(pbud.get(key), 1))
        psa = pbud.get("self_approve")
        if isinstance(psa, dict):
            sa = bud.setdefault("self_approve", {})
            for key in ("max_add", "max_times_per_session"):
                if key in psa:
                    sa[key] = max(_to_int(sa.get(key), 1), _to_int(psa.get(key), 1))
            if "replenish_every" in psa:
                current = _to_int(sa.get("replenish_every"), 0)
                proposed = _to_int(psa.get("replenish_every"), current)
                # Lower replenish_every (> 0) = faster slot recovery = more relaxed.
                if proposed > 0:
                    sa["replenish_every"] = min(current, proposed) if current > 0 else proposed
    return out


_PROJECT_BOUNDARY = (".git", ".hg", ".svn")
_TRUSTED_HOME_ENV = "AGENT_RAILS_HOME"


def _search_dirs(start: str):
    """Yield `start` and its ancestors, stopping at a repo/home/filesystem
    boundary. This lets a .agent-rails.json / .agent-rails-off placed at the
    REPO ROOT be honored even when the agent's cwd is a subdirectory — without
    ever wandering above the project (which could pick up an unrelated file)."""
    try:
        cur = Path(start).resolve()
    except Exception:
        return
    try:
        home = Path.home().resolve()
    except Exception:
        home = None
    seen = 0
    while True:
        yield cur
        seen += 1
        if cur.parent == cur or seen > 64:  # filesystem root / runaway guard
            break
        if any((cur / b).exists() for b in _PROJECT_BOUNDARY):
            break  # cur is the project root; do not ascend past it
        if home is not None and cur == home:
            break  # never ascend above the user's home dir
        cur = cur.parent


def _find_upwards(start: str, name: str) -> Optional[Path]:
    for d in _search_dirs(start):
        p = d / name
        try:
            if p.exists():
                return p
        except Exception:
            continue
    return None


def _repo_root(start: str) -> Optional[Path]:
    for d in _search_dirs(start):
        try:
            if any((d / b).exists() for b in _PROJECT_BOUNDARY):
                return d
        except Exception:
            continue
    return None


def _trusted_home() -> Path:
    override = os.environ.get(_TRUSTED_HOME_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".agent-rails"


def _read_json(path: Path):
    try:
        if path.exists() and path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None


def _git_dir(repo: Path) -> Optional[Path]:
    git = repo / ".git"
    try:
        if git.is_dir():
            return git
        if git.is_file():
            text = git.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                if line.lower().startswith("gitdir:"):
                    raw = line.split(":", 1)[1].strip()
                    p = Path(raw)
                    if not p.is_absolute():
                        p = repo / p
                    return p.resolve()
    except Exception:
        return None
    return None


def _normalize_remote(url: str) -> str:
    s = str(url or "").strip().lower()
    if s.endswith(".git"):
        s = s[:-4]
    return s


def _repo_remotes(repo: Optional[Path]) -> set[str]:
    if repo is None:
        return set()
    gd = _git_dir(repo)
    if gd is None:
        return set()
    config = gd / "config"
    out: set[str] = set()
    try:
        for line in config.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if stripped.startswith("url ="):
                raw = stripped.split("=", 1)[1].strip()
                if raw:
                    out.add(raw)
                    out.add(_normalize_remote(raw))
    except Exception:
        return set()
    return out


def _path_matches(repo: Optional[Path], configured: list[str]) -> bool:
    if repo is None:
        return False
    try:
        repo_resolved = repo.resolve()
    except Exception:
        repo_resolved = repo
    for raw in configured:
        try:
            p = Path(raw).expanduser().resolve()
        except Exception:
            continue
        if p == repo_resolved:
            return True
    return False


def _remote_matches(remotes: set[str], configured: list[str]) -> bool:
    normalized = set(remotes)
    normalized.update(_normalize_remote(r) for r in remotes)
    for raw in configured:
        if raw in remotes or _normalize_remote(raw) in normalized:
            return True
    return False


def _policy_matches(policy, repo: Optional[Path], remotes: set[str]) -> bool:
    if not isinstance(policy, dict):
        return False
    match = policy.get("match")
    if not isinstance(match, dict):
        return False
    repo_paths = _to_str_list(match.get("repo_paths"))
    repo_remotes = _to_str_list(match.get("repo_remotes"))
    if repo_paths and _path_matches(repo, repo_paths):
        return True
    if repo_remotes and _remote_matches(remotes, repo_remotes):
        return True
    return False


def _apply_trusted_config(cfg: dict, start: str) -> dict:
    """Apply trusted user/global config and matched policy files.

    Unlike repo-local .agent-rails.json, this layer is operator-owned and may
    tighten. It never raises; unreadable or malformed files simply do not apply.
    """
    out = deepcopy(cfg)
    home = _trusted_home()

    global_cfg = _read_json(home / "config.json")
    if isinstance(global_cfg, dict):
        out = _deep_merge(out, global_cfg)
        out = _sanitize_baseline(out)

    repo = _repo_root(start)
    remotes = _repo_remotes(repo)
    policies_dir = home / "policies"
    try:
        policy_paths = sorted(policies_dir.glob("*.json"))
    except Exception:
        policy_paths = []
    matched: list[str] = []
    for path in policy_paths:
        policy = _read_json(path)
        if not _policy_matches(policy, repo, remotes):
            continue
        payload = {
            k: v for k, v in policy.items()
            if k not in {"id", "match"}
        }
        out = _deep_merge(out, payload)
        matched.append(str(policy.get("id") or path.stem))
        out = _sanitize_baseline(out)
    if matched:
        meta = out.setdefault("_meta", {})
        if isinstance(meta, dict):
            meta["trusted_policies"] = matched
    return out


def load_config(project_dir: Optional[str] = None) -> dict:
    baseline = deepcopy(_DEFAULT)

    # 2. trusted packaged override
    try:
        pkg = Path(__file__).resolve().parent / "config.default.json"
        if pkg.exists():
            baseline = _deep_merge(baseline, json.loads(pkg.read_text(encoding="utf-8")))
    except Exception:
        pass
    baseline = _sanitize_baseline(baseline)

    start = project_dir or os.getcwd()

    # 3/4. trusted user config + matched policy registry — may tighten.
    try:
        baseline = _apply_trusted_config(baseline, start)
    except Exception:
        pass

    # 5. untrusted per-project overlay — relax only. Searched from cwd up to the
    #    repo root, so a repo-root config applies in any subdirectory.
    try:
        ov = _find_upwards(start, ".agent-rails.json")
        if ov is not None:
            baseline = _restrict_merge(baseline, json.loads(ov.read_text(encoding="utf-8")))
    except Exception:
        pass

    # 6. opt-out marker (a relaxation), same upward search.
    try:
        if _find_upwards(start, ".agent-rails-off") is not None:
            baseline["mode"] = "off"
    except Exception:
        pass

    # 7. trusted env override (operator-controlled; may tighten)
    env = _canon_mode(os.environ.get("AGENT_RAILS_MODE"))
    if env is not None:
        baseline["mode"] = env

    # final floor clamp — note: does NOT re-raise window to block_at, so a
    # project's window relaxation persists (an intentional, safe-direction change).
    return _clamp_floors(baseline)
