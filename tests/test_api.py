"""Shared core API tests — the entry point both adapters call.

Verifies check()/record() agree end-to-end and that record() honors the
opt-out (mode=off) so an opted-out repo stays fully inert.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp(prefix="hamingja-test-")
os.environ["HAMINGJA_STATE_DIR"] = _TMP

from hamingja.core.api import check, record  # noqa: E402
from hamingja.core.state import read_recent  # noqa: E402


def _proj(files):
    d = tempfile.mkdtemp(prefix="hamingja-api-")
    for name, content in files.items():
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(content)
    return d


def test_check_blocks_after_repeats_via_api_in_enforce():
    old = os.environ.get("HAMINGJA_MODE")
    try:
        os.environ["HAMINGJA_MODE"] = "enforce"
        d = _proj({})
        args = {"command": "npm test"}
        for _ in range(3):
            record("sy", "Bash", args, ok=False, project_dir=d, output={"exit_code": 1, "stderr": "same failure"})
        v = check("sy", "Bash", args, project_dir=d)
        assert v.action == "block"
    finally:
        if old is None:
            os.environ.pop("HAMINGJA_MODE", None)
        else:
            os.environ["HAMINGJA_MODE"] = old


def test_enforced_block_records_marker_and_breaks_streak():
    """Regression: an enforced block must not wedge the agent.

    error_streak is candidate-independent, so once it trips, a naive
    implementation denies EVERY following call; the denied calls never run, so
    no success is ever recorded to reset the streak. check() records a BLOCKED
    marker for the denied call, which is not an ERROR, so the streak resets and
    the agent can run a *different* (diagnostic) call.
    """
    old = os.environ.get("HAMINGJA_MODE")
    try:
        os.environ["HAMINGJA_MODE"] = "enforce"
        d = _proj({})
        for _ in range(6):
            record("wedge", "Bash", {"command": "broken"}, ok=False, project_dir=d)
        # error_streak (6) trips -> block; check() records the BLOCKED marker.
        assert check("wedge", "Bash", {"command": "broken"}, project_dir=d).action == "block"
        # A DIFFERENT call must now be allowed: the marker broke the streak.
        assert check("wedge", "Bash", {"command": "diagnose"}, project_dir=d).action == "allow"
    finally:
        if old is None:
            os.environ.pop("HAMINGJA_MODE", None)
        else:
            os.environ["HAMINGJA_MODE"] = old


def test_enforced_repetition_block_marker_keeps_identical_retry_blocked():
    old = os.environ.get("HAMINGJA_MODE")
    try:
        os.environ["HAMINGJA_MODE"] = "enforce"
        d = _proj({})
        args = {"command": "rg foo"}
        for _ in range(3):
            record("repeat-marker", "Bash", args, ok=False, project_dir=d)

        assert check("repeat-marker", "Bash", args, project_dir=d).action == "block"
        assert check("repeat-marker", "Bash", args, project_dir=d).action == "block"
        assert check("repeat-marker", "Bash", {"command": "git status -sb"}, project_dir=d).action == "allow"
    finally:
        if old is None:
            os.environ.pop("HAMINGJA_MODE", None)
        else:
            os.environ["HAMINGJA_MODE"] = old


def test_enforced_repetition_block_marker_survives_window_rollover():
    old = os.environ.get("HAMINGJA_MODE")
    try:
        os.environ["HAMINGJA_MODE"] = "enforce"
        d = _proj({})
        args = {"command": "rg foo"}
        for _ in range(3):
            record("repeat-rollover", "Bash", args, ok=False, project_dir=d)

        for _ in range(20):
            assert check("repeat-rollover", "Bash", args, project_dir=d).action == "block"
        assert check("repeat-rollover", "Bash", {"command": "git status -sb"}, project_dir=d).action == "allow"
    finally:
        if old is None:
            os.environ.pop("HAMINGJA_MODE", None)
        else:
            os.environ["HAMINGJA_MODE"] = old


def test_observe_block_does_not_record_marker():
    """observe mode downgrades a block to a nudge — the call PROCEEDS and will
    record its own outcome, so check() must NOT inject a phantom BLOCKED marker."""
    d = _proj({})  # default mode is observe
    before = len(read_recent("obs-nomark", 200))
    v = check("obs-nomark", "Bash", {"command": "x"}, project_dir=d)
    assert v.action != "block"  # downgraded
    assert len(read_recent("obs-nomark", 200)) == before  # nothing recorded


def test_record_is_inert_when_off():
    d = _proj({".hamingja-off": ""})
    for _ in range(6):
        record("sx", "Bash", {"command": "t"}, ok=False, project_dir=d)
    assert read_recent("sx", 50) == []


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _run import run_module_tests
    sys.exit(run_module_tests(globals()))
