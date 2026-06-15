"""Tests for ReadDisciplineDetector and _read_scope event metadata."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_rails.core.events import OK, PENDING, ToolEvent, _read_scope  # noqa: E402
from agent_rails.detectors.base import ALLOW, BLOCK, NUDGE  # noqa: E402
from agent_rails.detectors.read_discipline import ReadDisciplineDetector  # noqa: E402

CFG = {"detectors": {"read_discipline": {"enabled": True, "nudge_at": 2, "block_at": 3}}}
DET = ReadDisciplineDetector()


# ---------------------------------------------------------------------------
# _read_scope helper
# ---------------------------------------------------------------------------

def test_read_scope_unscoped_returns_false_and_path():
    scoped, path = _read_scope("Read", {"file_path": "/a/b.py"})
    assert scoped is False
    assert path == "/a/b.py"


def test_read_scope_with_offset_is_scoped():
    scoped, _ = _read_scope("Read", {"file_path": "/a/b.py", "offset": 100})
    assert scoped is True


def test_read_scope_with_limit_is_scoped():
    scoped, _ = _read_scope("Read", {"file_path": "/a/b.py", "limit": 50})
    assert scoped is True


def test_read_scope_non_read_tool_always_scoped():
    scoped, path = _read_scope("Bash", {"command": "cat foo.py"})
    assert scoped is True
    assert path == ""


def test_read_scope_path_field_alias():
    scoped, path = _read_scope("Read", {"path": "/x/y.py"})
    assert scoped is False
    assert path == "/x/y.py"


def test_read_scope_non_dict_args_is_scoped():
    scoped, path = _read_scope("Read", None)
    assert scoped is True
    assert path == ""


# ---------------------------------------------------------------------------
# ToolEvent.candidate populates read_scoped / read_path
# ---------------------------------------------------------------------------

def test_candidate_unscoped_read_sets_fields():
    ev = ToolEvent.candidate("s", "Read", {"file_path": "/tmp/big.py"})
    assert ev.read_scoped is False
    assert ev.read_path == "/tmp/big.py"


def test_candidate_scoped_read_sets_scoped_true():
    ev = ToolEvent.candidate("s", "Read", {"file_path": "/tmp/big.py", "offset": 10, "limit": 40})
    assert ev.read_scoped is True


def test_candidate_bash_read_scoped_true():
    ev = ToolEvent.candidate("s", "Bash", {"command": "grep -n foo bar.py"})
    assert ev.read_scoped is True
    assert ev.read_path == ""


def test_round_trip_preserves_read_fields():
    ev = ToolEvent.candidate("s", "Read", {"file_path": "/a/b.py"})
    restored = ToolEvent.from_json(ev.to_json())
    assert restored.read_scoped is False
    assert restored.read_path == "/a/b.py"


# ---------------------------------------------------------------------------
# ReadDisciplineDetector — synthetic sequences (no real files)
# ---------------------------------------------------------------------------

def _ev(path="/big.py", scoped=False, status=OK, sid="s"):
    """Synthetic unscoped Read event for a given path."""
    e = ToolEvent(sid, "Read", f"hash-{path}-{scoped}", status, 0.0)
    e.read_scoped = scoped
    e.read_path = path
    return e


def _make_large_file(tmp_path: Path, lines: int = 250) -> str:
    """Write a temporary file with the given number of lines; return its path."""
    p = tmp_path / "large.py"
    p.write_text("\n".join(f"line {i}" for i in range(lines)))
    return str(p)


def test_first_unscoped_read_is_quiet(tmp_path):
    path = _make_large_file(tmp_path)
    candidate = _ev(path=path)
    verdict = DET.evaluate([], candidate, CFG)
    assert verdict is None


def test_first_unscoped_read_blocks_for_huge_file(tmp_path):
    path = _make_large_file(tmp_path, lines=1000)
    candidate = _ev(path=path)
    verdict = DET.evaluate([], candidate, CFG)
    assert verdict is not None
    assert verdict.action == BLOCK
    assert "1000+ lines" in verdict.reason
    assert "offset+limit" in verdict.reason


def test_first_unscoped_read_below_huge_threshold_uses_repeat_policy(tmp_path):
    path = _make_large_file(tmp_path, lines=999)
    candidate = _ev(path=path)
    verdict = DET.evaluate([], candidate, CFG)
    assert verdict is None


def test_first_read_block_threshold_can_be_raised(tmp_path):
    path = _make_large_file(tmp_path, lines=1000)
    cfg = {
        "detectors": {
            "read_discipline": {
                "enabled": True,
                "nudge_at": 2,
                "block_at": 3,
                "block_first_read_at_lines": 1200,
            }
        }
    }
    verdict = DET.evaluate([], _ev(path=path), cfg)
    assert verdict is None


def test_first_read_block_threshold_zero_disables_first_read_block(tmp_path):
    path = _make_large_file(tmp_path, lines=1000)
    cfg = {
        "detectors": {
            "read_discipline": {
                "enabled": True,
                "nudge_at": 2,
                "block_at": 3,
                "block_first_read_at_lines": 0,
            }
        }
    }
    verdict = DET.evaluate([], _ev(path=path), cfg)
    assert verdict is None


def test_second_unscoped_read_nudges(tmp_path):
    path = _make_large_file(tmp_path)
    prior = _ev(path=path)
    candidate = _ev(path=path)
    verdict = DET.evaluate([prior], candidate, CFG)
    assert verdict is not None
    assert verdict.action == NUDGE
    assert "large.py" in verdict.reason


def test_third_unscoped_read_blocks(tmp_path):
    path = _make_large_file(tmp_path)
    events = [_ev(path=path), _ev(path=path)]
    candidate = _ev(path=path)
    verdict = DET.evaluate(events, candidate, CFG)
    assert verdict is not None
    assert verdict.action == BLOCK
    assert "large.py" in verdict.reason


def test_scoped_reads_are_never_flagged(tmp_path):
    path = _make_large_file(tmp_path)
    events = [_ev(path=path, scoped=True)] * 5
    candidate = _ev(path=path, scoped=True)
    verdict = DET.evaluate(events, candidate, CFG)
    assert verdict is None


def test_unscoped_reads_of_different_files_dont_cross_count(tmp_path):
    path_a = _make_large_file(tmp_path, lines=250)
    path_b = tmp_path / "other.py"
    path_b.write_text("\n".join(f"x {i}" for i in range(250)))
    # two prior unscoped reads of path_a; candidate reads path_b (first time)
    events = [_ev(path=path_a), _ev(path=path_a)]
    candidate = _ev(path=str(path_b))
    verdict = DET.evaluate(events, candidate, CFG)
    assert verdict is None


def test_small_file_not_flagged(tmp_path):
    small = tmp_path / "small.py"
    small.write_text("\n".join(f"line {i}" for i in range(50)))
    path = str(small)
    events = [_ev(path=path)] * 5
    candidate = _ev(path=path)
    verdict = DET.evaluate(events, candidate, CFG)
    assert verdict is None


def test_missing_file_not_flagged():
    events = [_ev(path="/nonexistent/ghost.py")] * 5
    candidate = _ev(path="/nonexistent/ghost.py")
    verdict = DET.evaluate(events, candidate, CFG)
    assert verdict is None


def test_file_inspection_exception_fails_open(tmp_path, monkeypatch):
    path = _make_large_file(tmp_path, lines=1000)

    def boom(*args, **kwargs):
        raise RuntimeError("unexpected inspection failure")

    monkeypatch.setattr(Path, "open", boom)
    verdict = DET.evaluate([], _ev(path=path), CFG)
    assert verdict is None


def test_disabled_detector_is_silent(tmp_path):
    path = _make_large_file(tmp_path)
    cfg = {"detectors": {"read_discipline": {"enabled": False}}}
    events = [_ev(path=path)] * 5
    candidate = _ev(path=path)
    verdict = DET.evaluate(events, candidate, cfg)
    assert verdict is None


def test_custom_thresholds(tmp_path):
    path = _make_large_file(tmp_path)
    cfg = {"detectors": {"read_discipline": {"enabled": True, "nudge_at": 3, "block_at": 5}}}
    # 2 prior + candidate = 3 total → nudge_at=3 → nudge
    events = [_ev(path=path), _ev(path=path)]
    candidate = _ev(path=path)
    verdict = DET.evaluate(events, candidate, cfg)
    assert verdict is not None
    assert verdict.action == NUDGE

    # 4 prior + candidate = 5 total → block_at=5 → block
    events2 = [_ev(path=path)] * 4
    verdict2 = DET.evaluate(events2, _ev(path=path), cfg)
    assert verdict2 is not None
    assert verdict2.action == BLOCK


def test_refs_script_hint_when_repo_refs_helper_exists(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    refs = repo / "refs.sh"
    refs.write_text("#!/bin/sh\n")
    path = repo / "large.py"
    path.write_text("\n".join(f"line {i}" for i in range(1000)))

    verdict = DET.evaluate([], _ev(path=str(path)), CFG)
    assert verdict is not None
    assert verdict.action == BLOCK
    assert "./refs.sh <symbol-or-pattern>" in verdict.reason


def test_refs_script_hint_when_scripts_refs_helper_exists(tmp_path):
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    (repo / ".git").mkdir()
    (scripts / "refs.sh").write_text("#!/bin/sh\n")
    path = repo / "large.py"
    path.write_text("\n".join(f"line {i}" for i in range(1000)))

    verdict = DET.evaluate([], _ev(path=str(path)), CFG)
    assert verdict is not None
    assert verdict.action == BLOCK
    assert "scripts/refs.sh <symbol-or-pattern>" in verdict.reason


# ---------------------------------------------------------------------------
# tripwire advisory (unit-level, no subprocess)
# ---------------------------------------------------------------------------

def test_large_read_advisory_fires_on_unscoped_large_file(tmp_path):
    from agent_rails.adapters.claude_code.tripwire import _large_read_advisory
    p = _make_large_file(tmp_path, lines=250)
    result = _large_read_advisory("Read", {"file_path": p})
    assert result is not None
    assert "large.py" in result
    assert "offset" in result or "grep" in result


def test_large_read_advisory_silent_for_small_file(tmp_path):
    from agent_rails.adapters.claude_code.tripwire import _large_read_advisory
    small = tmp_path / "s.py"
    small.write_text("\n".join(f"x {i}" for i in range(50)))
    result = _large_read_advisory("Read", {"file_path": str(small)})
    assert result is None


def test_large_read_advisory_silent_for_scoped_read(tmp_path):
    from agent_rails.adapters.claude_code.tripwire import _large_read_advisory
    p = _make_large_file(tmp_path, lines=250)
    result = _large_read_advisory("Read", {"file_path": p, "offset": 10, "limit": 40})
    assert result is None


def test_large_read_advisory_silent_for_nonexistent_path():
    from agent_rails.adapters.claude_code.tripwire import _large_read_advisory
    result = _large_read_advisory("Read", {"file_path": "/no/such/file.py"})
    assert result is None


def test_large_read_advisory_silent_for_non_read_tool(tmp_path):
    from agent_rails.adapters.claude_code.tripwire import _large_read_advisory
    p = _make_large_file(tmp_path, lines=250)
    result = _large_read_advisory("Bash", {"command": f"cat {p}"})
    assert result is None
