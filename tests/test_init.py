"""Tests for `agent-rails init` — the offline AGENTS.md generator.

Init is not on the tool-call hot path, so these are conventional CLI tests
(no fail-open semantics to honor here). Coverage: listing, dry-run, default
profile set, default symlink behavior (CLAUDE.md + AGENTS.md -> CLAUDE.md),
--profile (repeated, comma-list, alias normalization), --link / --no-link,
unknown profile rejected, refuse-overwrite (file AND symlink), --force,
--out, de-dup, and a few mutual-exclusion guards.
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
        for name in DEFAULT_PROFILES:
            assert f"# {name}" in out
        assert "# compiler_language" not in out
        assert "# Agent instructions" in out
        # default would also produce the AGENTS.md symlink — dry-run announces it
        assert "would also create symlink" in out
        assert "AGENTS.md" in out
        # no file produced
        assert not (Path(d) / "CLAUDE.md").exists()
        assert not (Path(d) / "AGENTS.md").exists()
        assert not (Path(d) / "AGENTS.md").is_symlink()


def test_default_writes_claude_md_and_symlinks_agents_md():
    with tempfile.TemporaryDirectory() as d:
        rc, out, _ = _run(["init"], cwd=d)
        assert rc == 0
        claude = Path(d) / "CLAUDE.md"
        agents = Path(d) / "AGENTS.md"
        assert claude.exists() and not claude.is_symlink()
        assert agents.is_symlink()
        # relative target (basename), not an absolute path
        assert os.readlink(agents) == "CLAUDE.md"
        # symlink resolves to the same content
        assert agents.read_text(encoding="utf-8") == claude.read_text(encoding="utf-8")
        body = claude.read_text(encoding="utf-8")
        assert "# Agent instructions" in body
        assert "# base" in body
        assert "wrote CLAUDE.md" in out
        assert "linked AGENTS.md -> CLAUDE.md" in out


def test_no_link_suppresses_default_symlink():
    with tempfile.TemporaryDirectory() as d:
        rc, _, _ = _run(["init", "--no-link"], cwd=d)
        assert rc == 0
        assert (Path(d) / "CLAUDE.md").exists()
        assert not (Path(d) / "AGENTS.md").exists()
        assert not (Path(d) / "AGENTS.md").is_symlink()


def test_explicit_link_overrides_default_target():
    with tempfile.TemporaryDirectory() as d:
        rc, _, _ = _run(["init", "--link", "INSTRUCTIONS.md"], cwd=d)
        assert rc == 0
        assert (Path(d) / "CLAUDE.md").exists()
        link = Path(d) / "INSTRUCTIONS.md"
        assert link.is_symlink()
        assert os.readlink(link) == "CLAUDE.md"
        # no AGENTS.md (we redirected the link target)
        assert not (Path(d) / "AGENTS.md").exists()


def test_link_with_custom_out():
    with tempfile.TemporaryDirectory() as d:
        rc, _, _ = _run(
            ["init", "--out", "FOO.md", "--link", "BAR.md"],
            cwd=d,
        )
        assert rc == 0
        assert (Path(d) / "FOO.md").exists()
        link = Path(d) / "BAR.md"
        assert link.is_symlink()
        assert os.readlink(link) == "FOO.md"
        # custom --out without an AGENTS.md request -> no AGENTS.md
        assert not (Path(d) / "AGENTS.md").exists()


def test_link_and_no_link_mutually_exclusive():
    with tempfile.TemporaryDirectory() as d:
        rc, _, err = _run(["init", "--link", "AGENTS.md", "--no-link"], cwd=d)
        assert rc == 2
        assert "mutually exclusive" in err
        assert not (Path(d) / "CLAUDE.md").exists()


def test_link_same_as_out_errors():
    with tempfile.TemporaryDirectory() as d:
        rc, _, err = _run(
            ["init", "--out", "X.md", "--link", "X.md"],
            cwd=d,
        )
        assert rc == 2
        assert "same as --out" in err
        assert not (Path(d) / "X.md").exists()


def test_refuses_overwrite_without_force_on_out():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "CLAUDE.md").write_text("preexisting\n", encoding="utf-8")
        rc, _, err = _run(["init"], cwd=d)
        assert rc == 1
        assert "already exists" in err
        assert (Path(d) / "CLAUDE.md").read_text(encoding="utf-8") == "preexisting\n"


def test_refuses_overwrite_without_force_on_symlink():
    with tempfile.TemporaryDirectory() as d:
        # AGENTS.md already exists (as a regular file, in this case)
        (Path(d) / "AGENTS.md").write_text("preexisting AGENTS\n", encoding="utf-8")
        rc, _, err = _run(["init"], cwd=d)
        assert rc == 1
        assert "already exists" in err
        # neither file written
        assert not (Path(d) / "CLAUDE.md").exists()
        assert (Path(d) / "AGENTS.md").read_text(encoding="utf-8") == "preexisting AGENTS\n"


def test_force_overwrites_both_files():
    SENTINEL = "ZZZ_PREEXISTING_SENTINEL_ZZZ"
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "CLAUDE.md").write_text(SENTINEL + " claude\n", encoding="utf-8")
        (Path(d) / "AGENTS.md").write_text(SENTINEL + " agents\n", encoding="utf-8")
        rc, _, _ = _run(["init", "--force"], cwd=d)
        assert rc == 0
        body = (Path(d) / "CLAUDE.md").read_text(encoding="utf-8")
        assert SENTINEL not in body
        assert "# base" in body
        # AGENTS.md is now the symlink, not the old file
        assert (Path(d) / "AGENTS.md").is_symlink()
        assert SENTINEL not in (Path(d) / "AGENTS.md").read_text(encoding="utf-8")


def test_force_replaces_existing_symlink():
    """Re-running init after a previous init should succeed with --force."""
    with tempfile.TemporaryDirectory() as d:
        rc, _, _ = _run(["init"], cwd=d)
        assert rc == 0
        assert (Path(d) / "AGENTS.md").is_symlink()
        # do it again — must not falsely trip "link == out" guard
        rc, _, _ = _run(["init", "--force"], cwd=d)
        assert rc == 0
        assert (Path(d) / "AGENTS.md").is_symlink()
        assert os.readlink(Path(d) / "AGENTS.md") == "CLAUDE.md"


def test_unknown_profile_rejected_with_clear_error():
    with tempfile.TemporaryDirectory() as d:
        rc, _, err = _run(["init", "--profile", "made_up"], cwd=d)
        assert rc == 2
        assert "unknown profile" in err
        assert "made_up" in err
        assert not (Path(d) / "CLAUDE.md").exists()
        assert not (Path(d) / "AGENTS.md").exists()


def test_profile_flag_repeats_and_accepts_commas():
    with tempfile.TemporaryDirectory() as d:
        rc, _, _ = _run(
            ["init", "--profile", "base", "--profile", "debugging,escalation"],
            cwd=d,
        )
        assert rc == 0
        body = (Path(d) / "CLAUDE.md").read_text(encoding="utf-8")
        assert "# base" in body
        assert "# debugging" in body
        assert "# escalation" in body
        assert "# review_passes" not in body


def test_compiler_language_is_opt_in():
    with tempfile.TemporaryDirectory() as d:
        rc, _, _ = _run(["init", "--profile", "compiler-language"], cwd=d)
        assert rc == 0
        body = (Path(d) / "CLAUDE.md").read_text(encoding="utf-8")
        assert "# compiler_language" in body  # hyphen alias normalized


def test_duplicate_profiles_render_once():
    with tempfile.TemporaryDirectory() as d:
        rc, _, _ = _run(
            ["init", "--profile", "base", "--profile", "base,base"],
            cwd=d,
        )
        assert rc == 0
        body = (Path(d) / "CLAUDE.md").read_text(encoding="utf-8")
        assert body.count("# base") == 1


def test_out_path_honored_and_creates_parent_dirs_no_symlink():
    """Custom --out implies single-file intent; no implicit AGENTS.md symlink."""
    with tempfile.TemporaryDirectory() as d:
        target = Path(d) / "nested" / "INSTRUCTIONS.md"
        rc, out, _ = _run(["init", "--out", str(target)], cwd=d)
        assert rc == 0
        assert target.exists()
        assert "# base" in target.read_text(encoding="utf-8")
        assert "INSTRUCTIONS.md" in out
        # no AGENTS.md anywhere unless --link was given
        assert not (Path(d) / "AGENTS.md").exists()
        assert not (Path(d) / "nested" / "AGENTS.md").exists()


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _run import run_module_tests
    sys.exit(run_module_tests(globals()))
