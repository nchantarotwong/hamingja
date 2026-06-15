"""Tests for the python_command detector.

Synthetic candidate events only (per CLAUDE.md). The detector inspects the
candidate's arg_preview, so we construct ToolEvents via the public candidate
factory and assert the verdict.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_rails.core.events import ToolEvent
from agent_rails.detectors.base import NUDGE
from agent_rails.detectors.python_command import PythonCommandDetector

SESSION = "test-python-detector"


def _candidate(command: str) -> ToolEvent:
    return ToolEvent.candidate(SESSION, "Bash", {"command": command})


def test_nudges_on_bare_python_command():
    det = PythonCommandDetector()
    v = det.evaluate([], _candidate("python myscript.py"), {})
    assert v is not None
    assert v.action == NUDGE
    assert "python3" in v.reason or ".venv" in v.reason


def test_nudges_on_python_dash_c():
    det = PythonCommandDetector()
    v = det.evaluate([], _candidate("python -c 'print(1)'"), {})
    assert v is not None
    assert v.action == NUDGE


def test_does_not_fire_on_python3():
    det = PythonCommandDetector()
    assert det.evaluate([], _candidate("python3 myscript.py"), {}) is None
    assert det.evaluate([], _candidate("python3.11 -m pytest"), {}) is None


def test_does_not_fire_on_pythonsomething():
    """First-token must be exactly `python`, not a prefix."""
    det = PythonCommandDetector()
    assert det.evaluate([], _candidate("pythonsomething --help"), {}) is None
    assert det.evaluate([], _candidate("pythontex foo"), {}) is None


def test_does_not_fire_when_venv_python_used_directly():
    det = PythonCommandDetector()
    assert det.evaluate(
        [], _candidate("./.venv/bin/python myscript.py"), {}
    ) is None
    assert det.evaluate(
        [], _candidate("/repo/.venv/bin/python -m pytest"), {}
    ) is None


def test_does_not_fire_on_non_bash():
    det = PythonCommandDetector()
    # A Read event with read_path looking like python — should not fire
    ev = ToolEvent.candidate(SESSION, "Read", {"file_path": "python.txt"})
    assert det.evaluate([], ev, {}) is None


def test_does_not_fire_when_disabled():
    det = PythonCommandDetector()
    cfg = {"detectors": {"python_command": {"enabled": False}}}
    assert det.evaluate([], _candidate("python myscript.py"), cfg) is None


def test_uses_repo_venv_path_when_present(tmp_path, monkeypatch):
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/bin/sh\necho fake-python\n")
    monkeypatch.chdir(tmp_path)
    det = PythonCommandDetector()
    v = det.evaluate([], _candidate("python myscript.py"), {})
    assert v is not None
    assert str(venv_python) in v.reason


def test_fail_open_on_empty_command():
    det = PythonCommandDetector()
    # Empty preview should not raise and should not fire
    ev = ToolEvent.candidate(SESSION, "Bash", {"command": ""})
    assert det.evaluate([], ev, {}) is None


def test_fail_open_on_no_candidate_no_events():
    det = PythonCommandDetector()
    assert det.evaluate([], None, {}) is None


def test_evaluates_last_event_when_no_candidate():
    """When called with history but no candidate, falls back to last event."""
    det = PythonCommandDetector()
    last = _candidate("python script.py")
    v = det.evaluate([last], None, {})
    assert v is not None
    assert v.action == NUDGE
