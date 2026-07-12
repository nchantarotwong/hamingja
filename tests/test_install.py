"""Tests for `hamingja install` — harness auto-detection and dispatch.

Install ultimately shells out to a bash install.sh, which would touch
~/.claude or ~/.codex; we don't want tests doing that. So we exercise:

  * _detect_harnesses(home) directly with a temp dir
  * _run_installs / _cmd_install with subprocess.call monkey-patched, so
    we capture WHICH installer would have run without actually running it
"""
import io
import os
import sys
import tempfile
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hamingja import cli  # noqa: E402
from hamingja.cli import _detect_harnesses, main  # noqa: E402


# ---- _detect_harnesses ----------------------------------------------------


def test_detect_empty_home():
    with tempfile.TemporaryDirectory() as d:
        assert _detect_harnesses(Path(d)) == []


def test_detect_claude_only():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / ".claude").mkdir()
        assert _detect_harnesses(Path(d)) == ["claude_code"]


def test_detect_codex_only():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / ".codex").mkdir()
        assert _detect_harnesses(Path(d)) == ["codex"]


def test_detect_both_stable_order():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / ".codex").mkdir()
        (Path(d) / ".claude").mkdir()
        # Order is dictated by _HARNESS_HOMES, not filesystem traversal:
        # claude_code first, codex second.
        assert _detect_harnesses(Path(d)) == ["claude_code", "codex"]


# ---- end-to-end via main(), with subprocess.call patched ------------------


def _run_install_capture(argv, *, home=None, monkey_subprocess=True):
    """Invoke `hamingja install ...`, capturing subprocess.call invocations.

    Returns (rc, stdout, stderr, calls), where calls is a list of (cmd,)
    tuples that would have been spawned.
    """
    calls: list = []

    def fake_call(cmd, *a, **k):
        calls.append(tuple(cmd))
        return 0

    out, err = io.StringIO(), io.StringIO()
    saved_call = cli.subprocess.call
    saved_home = None
    if home is not None:
        saved_home = Path.home

        def _fake_home():
            return Path(home)
        Path.home = staticmethod(_fake_home)  # type: ignore[assignment]
    if monkey_subprocess:
        cli.subprocess.call = fake_call
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(argv)
    finally:
        cli.subprocess.call = saved_call
        if saved_home is not None:
            Path.home = saved_home  # type: ignore[assignment]
    return rc, out.getvalue(), err.getvalue(), calls


def _scripts_in_calls(calls):
    """Return the list of install.sh paths the install command would have run."""
    return [c[1] for c in calls if len(c) >= 2 and c[1].endswith("install.sh")]


def test_install_no_arg_no_harness_detected_errors():
    with tempfile.TemporaryDirectory() as d:
        rc, _, err, calls = _run_install_capture(["install"], home=d)
        assert rc == 1
        assert "no harness detected" in err
        assert calls == []


def test_install_no_arg_runs_only_detected_harness():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / ".codex").mkdir()
        rc, _, _, calls = _run_install_capture(["install"], home=d)
        assert rc == 0
        scripts = _scripts_in_calls(calls)
        assert len(scripts) == 1
        assert scripts[0].endswith("/adapters/codex/install.sh")


def test_install_all_runs_both_known_harnesses():
    with tempfile.TemporaryDirectory() as d:
        # 'all' ignores detection
        rc, _, _, calls = _run_install_capture(["install", "all"], home=d)
        assert rc == 0
        scripts = _scripts_in_calls(calls)
        assert len(scripts) == 2
        assert any(s.endswith("/adapters/claude_code/install.sh") for s in scripts)
        assert any(s.endswith("/adapters/codex/install.sh") for s in scripts)


def test_install_claude_alias_routes_to_claude_code():
    with tempfile.TemporaryDirectory() as d:
        rc, _, _, calls = _run_install_capture(["install", "claude"], home=d)
        assert rc == 0
        scripts = _scripts_in_calls(calls)
        assert len(scripts) == 1
        assert scripts[0].endswith("/adapters/claude_code/install.sh")


def test_install_codex_explicit_runs_only_codex():
    with tempfile.TemporaryDirectory() as d:
        # both detected — but explicit arg overrides detection
        (Path(d) / ".claude").mkdir()
        (Path(d) / ".codex").mkdir()
        rc, _, _, calls = _run_install_capture(["install", "codex"], home=d)
        assert rc == 0
        scripts = _scripts_in_calls(calls)
        assert len(scripts) == 1
        assert scripts[0].endswith("/adapters/codex/install.sh")


def test_install_nonzero_install_returns_nonzero():
    """If any single install fails, the overall rc is non-zero."""
    calls: list = []

    def flaky_call(cmd, *a, **k):
        calls.append(tuple(cmd))
        # claude_code succeeds, codex fails
        return 0 if "claude_code" in cmd[1] else 7

    out, err = io.StringIO(), io.StringIO()
    saved = cli.subprocess.call
    cli.subprocess.call = flaky_call
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["install", "all"])
    finally:
        cli.subprocess.call = saved
    assert rc == 7
    assert len(calls) == 2


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _run import run_module_tests
    sys.exit(run_module_tests(globals()))
