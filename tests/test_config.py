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
    """Run fn with AGENT_RAILS_MODE unset, restoring it after."""
    old = os.environ.pop("AGENT_RAILS_MODE", None)
    try:
        fn()
    finally:
        if old is not None:
            os.environ["AGENT_RAILS_MODE"] = old


# --- trust model: project config may only relax -------------------------

def test_project_cannot_escalate_to_enforce():
    def body():
        d = _proj({".agent-rails.json": json.dumps({"mode": "enforce"})})
        assert load_config(d)["mode"] == "observe"  # baseline observe wins
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


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _run import run_module_tests
    sys.exit(run_module_tests(globals()))
