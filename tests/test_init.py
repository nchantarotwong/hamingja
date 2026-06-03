"""Tests for `agent-rails init` — the offline AGENTS.md generator.

Init is not on the tool-call hot path, so these are conventional CLI tests
(no fail-open semantics to honor here). Coverage: listing, dry-run, default
profile set, --profile (repeated, comma-list, alias normalization), unknown
profile rejected, refuse-overwrite, --force, --out, and de-dup.
"""
import io
import os
import sys
import tempfile
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_rails.cli import main  # noqa: E402
from agent_rails.profiles import ALL_PROFILES, DEFAULT_PROFILES  # noqa: E402


def _run(argv, cwd=None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    prev = os.getcwd()
    if cwd is not None:
        os.chdir(cwd)
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(argv)
    finally:
        if cwd is not None:
            os.chdir(prev)
    return rc, out.getvalue(), err.getvalue()


def test_list_shows_all_profiles_and_marks_defaults():
    rc, out, _ = _run(["init", "--list"])
    assert rc == 0
    for name in ALL_PROFILES:
        assert name in out
    for name in DEFAULT_PROFILES:
        assert f"{name}  (default)" in out
    assert "compiler_language" in out
    assert "compiler_language  (default)" not in out


def test_dry_run_prints_default_set_and_does_not_write():
    with tempfile.TemporaryDirectory() as d:
        rc, out, _ = _run(["init", "--dry-run"], cwd=d)
        assert rc == 0
        # default profiles all present as section headers
        for name in DEFAULT_PROFILES:
            assert f"# {name}" in out
        # compiler_language is NOT in the default set
        assert "# compiler_language" not in out
        # header from the root template made it in
        assert "# Agent instructions" in out
        # no file produced
        assert not (Path(d) / "AGENTS.md").exists()


def test_writes_agents_md_in_cwd():
    with tempfile.TemporaryDirectory() as d:
        rc, out, _ = _run(["init"], cwd=d)
        assert rc == 0
        path = Path(d) / "AGENTS.md"
        assert path.exists()
        body = path.read_text(encoding="utf-8")
        assert "# Agent instructions" in body
        assert "# base" in body
        assert "wrote AGENTS.md" in out


def test_refuses_overwrite_without_force():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "AGENTS.md").write_text("preexisting\n", encoding="utf-8")
        rc, _, err = _run(["init"], cwd=d)
        assert rc == 1
        assert "already exists" in err
        # file untouched
        assert (Path(d) / "AGENTS.md").read_text(encoding="utf-8") == "preexisting\n"


def test_force_overwrites():
    SENTINEL = "ZZZ_PREEXISTING_SENTINEL_ZZZ"
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "AGENTS.md").write_text(SENTINEL + "\n", encoding="utf-8")
        rc, _, _ = _run(["init", "--force"], cwd=d)
        assert rc == 0
        body = (Path(d) / "AGENTS.md").read_text(encoding="utf-8")
        assert SENTINEL not in body
        assert "# base" in body


def test_unknown_profile_rejected_with_clear_error():
    with tempfile.TemporaryDirectory() as d:
        rc, _, err = _run(["init", "--profile", "made_up"], cwd=d)
        assert rc == 2
        assert "unknown profile" in err
        assert "made_up" in err
        # nothing written
        assert not (Path(d) / "AGENTS.md").exists()


def test_profile_flag_repeats_and_accepts_commas():
    with tempfile.TemporaryDirectory() as d:
        rc, _, _ = _run(
            ["init", "--profile", "base", "--profile", "debugging,escalation"],
            cwd=d,
        )
        assert rc == 0
        body = (Path(d) / "AGENTS.md").read_text(encoding="utf-8")
        assert "# base" in body
        assert "# debugging" in body
        assert "# escalation" in body
        # only what was asked for — review_passes was not requested
        assert "# review_passes" not in body


def test_compiler_language_is_opt_in():
    with tempfile.TemporaryDirectory() as d:
        rc, _, _ = _run(["init", "--profile", "compiler-language"], cwd=d)
        assert rc == 0
        body = (Path(d) / "AGENTS.md").read_text(encoding="utf-8")
        assert "# compiler_language" in body  # hyphen alias normalized


def test_duplicate_profiles_render_once():
    with tempfile.TemporaryDirectory() as d:
        rc, _, _ = _run(
            ["init", "--profile", "base", "--profile", "base,base"],
            cwd=d,
        )
        assert rc == 0
        body = (Path(d) / "AGENTS.md").read_text(encoding="utf-8")
        assert body.count("# base") == 1


def test_out_path_honored_and_creates_parent_dirs():
    with tempfile.TemporaryDirectory() as d:
        target = Path(d) / "nested" / "CLAUDE.md"
        rc, out, _ = _run(["init", "--out", str(target)], cwd=d)
        assert rc == 0
        assert target.exists()
        assert "# base" in target.read_text(encoding="utf-8")
        assert "CLAUDE.md" in out


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _run import run_module_tests
    sys.exit(run_module_tests(globals()))
