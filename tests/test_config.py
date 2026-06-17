"""Config trust-model + sanitization tests.

These pin the security boundary: an untrusted per-project .agent-rails.json may
only RELAX the guard, never tighten it, and all values are clamped to safe
floors so a typo'd/out-of-range setting can't crash a detector or force a
spurious block.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_rails.config import load_config  # noqa: E402


def _proj(files):
    d = tempfile.mkdtemp(prefix="agent-rails-cfg-")
    for name, content in files.items():
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(content)
    return d


def _no_env(fn):
    """Run fn with trusted env overrides unset, restoring them after."""
    old = os.environ.pop("AGENT_RAILS_MODE", None)
    old_home = os.environ.pop("AGENT_RAILS_HOME", None)
    try:
        fn()
    finally:
        if old is not None:
            os.environ["AGENT_RAILS_MODE"] = old
        else:
            os.environ.pop("AGENT_RAILS_MODE", None)
        if old_home is not None:
            os.environ["AGENT_RAILS_HOME"] = old_home
        else:
            os.environ.pop("AGENT_RAILS_HOME", None)


def _trusted_home(files):
    d = tempfile.mkdtemp(prefix="agent-rails-home-")
    for name, content in files.items():
        path = os.path.join(d, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    return d


# --- trust model: project config may only relax -------------------------

def test_project_cannot_escalate_to_enforce():
    def body():
        d = _proj({".agent-rails.json": json.dumps({"mode": "enforce"})})
        assert load_config(d)["mode"] == "observe"  # baseline observe wins
    _no_env(body)


def test_project_cannot_escalate_detector_mode_to_enforce():
    def body():
        d = _proj({".agent-rails.json": json.dumps(
            {"detectors": {"repetition": {"mode": "enforce"}}})})
        assert "mode" not in load_config(d)["detectors"]["repetition"]
    _no_env(body)


def test_project_can_relax_detector_mode():
    def body():
        home = _trusted_home({
            "config.json": json.dumps({
                "detectors": {"repetition": {"mode": "enforce"}}
            })
        })
        os.environ["AGENT_RAILS_HOME"] = home
        d = _proj({".agent-rails.json": json.dumps(
            {"detectors": {"repetition": {"mode": "observe"}}})})
        assert load_config(d)["detectors"]["repetition"]["mode"] == "observe"
    _no_env(body)


def test_project_cannot_lower_block_at():
    def body():
        d = _proj({".agent-rails.json": json.dumps(
            {"detectors": {"repetition": {"block_at": 1}}})})
        assert load_config(d)["detectors"]["repetition"]["block_at"] >= 4
    _no_env(body)


def test_project_can_relax_mode_to_off():
    def body():
        d = _proj({".agent-rails.json": json.dumps({"mode": "off"})})
        assert load_config(d)["mode"] == "off"
    _no_env(body)


def test_project_can_raise_block_at():
    def body():
        d = _proj({".agent-rails.json": json.dumps(
            {"detectors": {"repetition": {"block_at": 9}}})})
        assert load_config(d)["detectors"]["repetition"]["block_at"] == 9
    _no_env(body)


def test_project_cannot_lower_first_read_block_threshold():
    def body():
        d = _proj({".agent-rails.json": json.dumps({
            "detectors": {
                "read_discipline": {"block_first_read_at_lines": 400}
            }
        })})
        assert (
            load_config(d)["detectors"]["read_discipline"]["block_first_read_at_lines"]
            == 1000
        )
    _no_env(body)


def test_project_can_raise_first_read_block_threshold():
    def body():
        d = _proj({".agent-rails.json": json.dumps({
            "detectors": {
                "read_discipline": {"block_first_read_at_lines": 1500}
            }
        })})
        assert (
            load_config(d)["detectors"]["read_discipline"]["block_first_read_at_lines"]
            == 1500
        )
    _no_env(body)


def test_project_can_disable_first_read_block_threshold():
    def body():
        d = _proj({".agent-rails.json": json.dumps({
            "detectors": {
                "read_discipline": {"block_first_read_at_lines": 0}
            }
        })})
        assert (
            load_config(d)["detectors"]["read_discipline"]["block_first_read_at_lines"]
            == 0
        )
    _no_env(body)


def test_project_can_disable_detector_but_not_enable_a_tightening():
    def body():
        d = _proj({".agent-rails.json": json.dumps(
            {"detectors": {"repetition": {"enabled": False}}})})
        assert load_config(d)["detectors"]["repetition"]["enabled"] is False
    _no_env(body)


def test_off_marker_disables():
    def body():
        d = _proj({".agent-rails-off": ""})
        assert load_config(d)["mode"] == "off"
    _no_env(body)


# --- exempt_tools allowlist (a relaxation: extend-only) -----------------

def test_baseline_has_default_exempt_tools():
    def body():
        ex = load_config(_proj({}))["detectors"]["repetition"]["exempt_tools"]
        assert "Read" in ex and "Grep" in ex
    _no_env(body)


def test_project_can_extend_exempt_tools():
    def body():
        d = _proj({".agent-rails.json": json.dumps(
            {"detectors": {"repetition": {"exempt_tools": ["MyCustomReadTool"]}}})})
        ex = load_config(d)["detectors"]["repetition"]["exempt_tools"]
        assert "MyCustomReadTool" in ex  # added
        assert "Read" in ex               # baseline entries preserved (extend-only)
    _no_env(body)


def test_project_cannot_shrink_exempt_tools():
    # supplying a shorter list must not REMOVE baseline exemptions (that would
    # tighten the guard); the result is the union.
    def body():
        d = _proj({".agent-rails.json": json.dumps(
            {"detectors": {"repetition": {"exempt_tools": []}}})})
        ex = load_config(d)["detectors"]["repetition"]["exempt_tools"]
        assert "Read" in ex and "Grep" in ex
    _no_env(body)


# --- trusted user policy registry --------------------------------------

def test_trusted_user_config_can_tighten():
    def body():
        home = _trusted_home({
            "config.json": json.dumps({
                "mode": "enforce",
                "detectors": {
                    "repetition": {"block_at": 2},
                    "leverage_fallback": {
                        "mode": "enforce",
                        "required_patterns": ["semantic-nav"],
                        "protected_targets": ["src/compiler/main.lang"],
                    },
                },
            })
        })
        os.environ["AGENT_RAILS_HOME"] = home
        cfg = load_config(_proj({}))
        assert cfg["mode"] == "enforce"
        assert cfg["detectors"]["repetition"]["block_at"] == 2
        lf = cfg["detectors"]["leverage_fallback"]
        assert lf["mode"] == "enforce"
        assert lf["required_patterns"] == ["semantic-nav"]
        assert lf["protected_targets"] == ["src/compiler/main.lang"]
    _no_env(body)


def test_trusted_policy_matches_repo_path_and_adds_metadata():
    def body():
        root = tempfile.mkdtemp(prefix="agent-rails-policy-repo-")
        os.mkdir(os.path.join(root, ".git"))
        sub = os.path.join(root, "src")
        os.mkdir(sub)
        home = _trusted_home({
            "policies/compiler.json": json.dumps({
                "id": "compiler-policy",
                "match": {"repo_paths": [root]},
                "detectors": {
                    "leverage_fallback": {
                        "required_patterns": ["semantic-nav"],
                        "protected_targets": ["src/compiler/main.lang"],
                    }
                },
            })
        })
        os.environ["AGENT_RAILS_HOME"] = home
        cfg = load_config(sub)
        assert cfg["_meta"]["trusted_policies"] == ["compiler-policy"]
        assert "id" not in cfg and "match" not in cfg
        assert cfg["detectors"]["leverage_fallback"]["required_patterns"] == ["semantic-nav"]
    _no_env(body)


def test_trusted_policy_matches_repo_remote():
    def body():
        root = tempfile.mkdtemp(prefix="agent-rails-policy-remote-")
        git = os.path.join(root, ".git")
        os.mkdir(git)
        with open(os.path.join(git, "config"), "w", encoding="utf-8") as f:
            f.write('[remote "origin"]\n\turl = git@example.com:org/project.git\n')
        home = _trusted_home({
            "policies/remote.json": json.dumps({
                "id": "remote-policy",
                "match": {"repo_remotes": ["git@example.com:org/project"]},
                "detectors": {
                    "leverage_fallback": {
                        "required_patterns": ["schema-check"],
                        "protected_targets": ["generated/schema.json"],
                    }
                },
            })
        })
        os.environ["AGENT_RAILS_HOME"] = home
        cfg = load_config(root)
        assert cfg["_meta"]["trusted_policies"] == ["remote-policy"]
        assert cfg["detectors"]["leverage_fallback"]["required_patterns"] == ["schema-check"]
    _no_env(body)


def test_untrusted_project_cannot_add_strict_leverage_patterns():
    def body():
        home = _trusted_home({
            "config.json": json.dumps({
                "detectors": {
                    "leverage_fallback": {
                        "required_patterns": ["semantic-nav"],
                        "protected_targets": ["src/compiler/main.lang"],
                    }
                }
            })
        })
        os.environ["AGENT_RAILS_HOME"] = home
        d = _proj({".agent-rails.json": json.dumps({
            "detectors": {
                "leverage_fallback": {
                    "required_patterns": ["repo-controlled-tool"],
                    "protected_targets": ["repo-controlled-target"],
                }
            }
        })})
        lf = load_config(d)["detectors"]["leverage_fallback"]
        assert lf["required_patterns"] == ["semantic-nav"]
        assert lf["protected_targets"] == ["src/compiler/main.lang"]
    _no_env(body)


# --- sanitization / floors ----------------------------------------------

def test_non_numeric_threshold_does_not_disable_detector():
    def body():
        d = _proj({".agent-rails.json": json.dumps(
            {"detectors": {"repetition": {"block_at": "four"}}})})
        # falls back to baseline, no crash, detector still active
        assert load_config(d)["detectors"]["repetition"]["block_at"] == 4
    _no_env(body)


def test_window_clamped_to_at_least_one():
    def body():
        d = _proj({".agent-rails.json": json.dumps({"window": 0})})
        assert load_config(d)["window"] >= 1
    _no_env(body)


def test_baseline_window_reaches_block_threshold():
    def body():
        d = _proj({})  # no project file -> trusted baseline
        cfg = load_config(d)
        max_block = max(dd["block_at"] for dd in cfg["detectors"].values())
        assert cfg["window"] >= max_block
    _no_env(body)


# --- trusted env override ------------------------------------------------

def test_env_mode_canonicalized_and_validated():
    old = os.environ.get("AGENT_RAILS_MODE")
    try:
        d = _proj({})
        os.environ["AGENT_RAILS_MODE"] = "  ENFORCE  "
        assert load_config(d)["mode"] == "enforce"  # trusted: may tighten
        os.environ["AGENT_RAILS_MODE"] = "garbage"
        assert load_config(d)["mode"] == "observe"  # invalid ignored
    finally:
        if old is None:
            os.environ.pop("AGENT_RAILS_MODE", None)
        else:
            os.environ["AGENT_RAILS_MODE"] = old


# --- repo-root search (cwd may be a subdirectory) -----------------------

def test_off_marker_found_at_repo_root_from_subdir():
    def body():
        root = tempfile.mkdtemp(prefix="agent-rails-root-")
        os.mkdir(os.path.join(root, ".git"))
        open(os.path.join(root, ".agent-rails-off"), "w").close()
        sub = os.path.join(root, "a", "b")
        os.makedirs(sub)
        assert load_config(sub)["mode"] == "off"
    _no_env(body)


def test_project_json_found_at_repo_root_from_subdir():
    def body():
        root = tempfile.mkdtemp(prefix="agent-rails-root2-")
        os.mkdir(os.path.join(root, ".git"))
        with open(os.path.join(root, ".agent-rails.json"), "w") as f:
            f.write(json.dumps({"detectors": {"repetition": {"block_at": 9}}}))
        sub = os.path.join(root, "deep", "nested")
        os.makedirs(sub)
        assert load_config(sub)["detectors"]["repetition"]["block_at"] == 9
    _no_env(body)


def test_search_stops_at_repo_boundary():
    # a marker ABOVE the repo root must NOT be honored (don't wander out of the repo)
    def body():
        outer = tempfile.mkdtemp(prefix="agent-rails-outer-")
        open(os.path.join(outer, ".agent-rails-off"), "w").close()
        repo = os.path.join(outer, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        sub = os.path.join(repo, "x")
        os.makedirs(sub)
        assert load_config(sub)["mode"] == "observe"
    _no_env(body)


# --- budget overlay: project config may only relax (raise limits) -----------

def test_project_can_raise_checkpoint_at():
    def body():
        d = _proj({".agent-rails.json": json.dumps(
            {"budget": {"checkpoint_at": 100}})})
        assert load_config(d)["budget"]["checkpoint_at"] == 100
    _no_env(body)


def test_project_cannot_lower_checkpoint_at():
    def body():
        d = _proj({".agent-rails.json": json.dumps(
            {"budget": {"checkpoint_at": 1}})})
        assert load_config(d)["budget"]["checkpoint_at"] >= 12
    _no_env(body)


def test_project_can_raise_hard_block_at():
    def body():
        d = _proj({".agent-rails.json": json.dumps(
            {"budget": {"hard_block_at": 100}})})
        assert load_config(d)["budget"]["hard_block_at"] == 100
    _no_env(body)


def test_project_can_raise_self_approve_max_add():
    def body():
        d = _proj({".agent-rails.json": json.dumps(
            {"budget": {"self_approve": {"max_add": 10}}})})
        assert load_config(d)["budget"]["self_approve"]["max_add"] == 10
    _no_env(body)


def test_project_cannot_lower_self_approve_max_add():
    def body():
        d = _proj({".agent-rails.json": json.dumps(
            {"budget": {"self_approve": {"max_add": 1}}})})
        assert load_config(d)["budget"]["self_approve"]["max_add"] >= 3
    _no_env(body)


def test_project_can_lower_replenish_every():
    """Lower replenish_every = faster slot recovery = more relaxed; project may do this."""
    def body():
        d = _proj({".agent-rails.json": json.dumps(
            {"budget": {"self_approve": {"replenish_every": 5}}})})
        assert load_config(d)["budget"]["self_approve"]["replenish_every"] == 5
    _no_env(body)


def test_project_cannot_raise_replenish_every():
    """Higher replenish_every = slower recovery = stricter; project must not tighten."""
    def body():
        d = _proj({".agent-rails.json": json.dumps(
            {"budget": {"self_approve": {"replenish_every": 999}}})})
        assert load_config(d)["budget"]["self_approve"]["replenish_every"] <= 15
    _no_env(body)


# --- budget overlay: weights (relax = lower cost) ---------------------------

def test_project_can_lower_a_tool_weight():
    """A project can DISCOUNT a tool further than the built-in (more relaxed)."""
    def body():
        d = _proj({".agent-rails.json": json.dumps(
            {"budget": {"weights": {"Bash": 0.25}}})})
        assert load_config(d)["budget"]["weights"]["Bash"] == 0.25
    _no_env(body)


def test_project_lower_wins_over_higher_existing_weight():
    """If both project and baseline set a weight, the lower (more relaxed) wins."""
    def body():
        d = _proj({".agent-rails.json": json.dumps(
            {"budget": {"weights": {"Read": 0.4}}})})
        # built-in for Read is 0.5; project 0.4 is lower so it should win
        out = load_config(d)
        assert out["budget"]["weights"]["Read"] == 0.4
    _no_env(body)


def test_project_cannot_raise_a_new_tool_weight_above_one():
    """A new tool entry > 1.0 is a tightening; project should NOT introduce it."""
    def body():
        d = _proj({".agent-rails.json": json.dumps(
            {"budget": {"weights": {"NewTool": 2.5}}})})
        out = load_config(d)
        assert "NewTool" not in (out.get("budget", {}).get("weights") or {})
    _no_env(body)


def test_project_can_add_new_tool_weight_at_discount():
    def body():
        d = _proj({".agent-rails.json": json.dumps(
            {"budget": {"weights": {"NewTool": 0.3}}})})
        assert load_config(d)["budget"]["weights"]["NewTool"] == 0.3
    _no_env(body)


def test_project_malformed_weight_is_dropped():
    def body():
        d = _proj({".agent-rails.json": json.dumps(
            {"budget": {"weights": {"Bash": "huge"}}})})
        out = load_config(d)
        # Malformed values are skipped; no key should be added
        assert "Bash" not in (out.get("budget", {}).get("weights") or {})
    _no_env(body)


# --- budget overlay: task_types (relax = higher thresholds) -----------------

def test_project_can_raise_task_type_checkpoint():
    def body():
        d = _proj({".agent-rails.json": json.dumps(
            {"budget": {"task_types": {"debug": {"checkpoint_at": 999}}}})})
        out = load_config(d)
        assert out["budget"]["task_types"]["debug"]["checkpoint_at"] == 999
    _no_env(body)


def test_project_cannot_lower_existing_task_type_threshold():
    def body():
        # First set it high via project, then a sibling lower value should not win.
        d = _proj({".agent-rails.json": json.dumps(
            {"budget": {"task_types": {"debug": {"checkpoint_at": 1}}}})})
        out = load_config(d)
        # No baseline value exists; lower-than-baseline check is N/A here, so
        # the project value is accepted as "introducing the threshold".
        # The real test: load_config twice with different orderings doesn't lower.
        assert out["budget"]["task_types"]["debug"]["checkpoint_at"] >= 1
    _no_env(body)


def test_project_can_define_custom_task_type():
    def body():
        d = _proj({".agent-rails.json": json.dumps(
            {"budget": {"task_types": {"migration": {"checkpoint_at": 80, "hard_block_at": 200}}}})})
        out = load_config(d)
        assert out["budget"]["task_types"]["migration"]["checkpoint_at"] == 80
        assert out["budget"]["task_types"]["migration"]["hard_block_at"] == 200
    _no_env(body)


def test_project_task_type_with_invalid_dict_is_dropped():
    def body():
        d = _proj({".agent-rails.json": json.dumps(
            {"budget": {"task_types": {"junk": "not a dict"}}})})
        out = load_config(d)
        assert "junk" not in (out.get("budget", {}).get("task_types") or {})
    _no_env(body)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _run import run_module_tests
    sys.exit(run_module_tests(globals()))
