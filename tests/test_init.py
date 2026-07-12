"""Tests for `hamingja init` — the offline AGENTS.md generator.

Init is not on the tool-call hot path, so these are conventional CLI tests
(no fail-open semantics to honor here). Coverage: listing, dry-run, default
profile set, default symlink behavior (CLAUDE.md + AGENTS.md -> CLAUDE.md),
managed-block append/upsert, --profile (repeated, comma-list, alias
normalization), --link / --no-link, unknown profile rejected, weird-state
refusal, --force, --out, de-dup, and a few mutual-exclusion guards.
"""
import io
import os
import sys
import tempfile
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hamingja.cli import main  # noqa: E402
from hamingja.profiles import ALL_PROFILES, DEFAULT_PROFILES  # noqa: E402


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
        assert "<!-- BEGIN hamingja workflow profiles -->" in out
        assert "## Workflow rails (hamingja)" in out
        # default would also produce the AGENTS.md symlink — dry-run announces it
        assert "would also create symlink" in out
        assert "AGENTS.md" in out
        # no file produced
        assert not (Path(d) / "CLAUDE.md").exists()
        assert not (Path(d) / "AGENTS.md").exists()
        assert not (Path(d) / "AGENTS.md").is_symlink()


def test_default_includes_sub_agent_escalation_guidance():
    with tempfile.TemporaryDirectory() as d:
        rc, out, _ = _run(["init", "--dry-run"], cwd=d)
        assert rc == 0
        assert "Sub-agent packet:" in out
        assert "Model choice for the sub-agent:" in out
        assert "use the day-to-day model when the packet is narrow" in out


def test_default_includes_wrapper_usage_guidance():
    with tempfile.TemporaryDirectory() as d:
        rc, out, _ = _run(["init", "--dry-run"], cwd=d)
        assert rc == 0
        assert "run `hamingja commands`" in out
        assert "use the listed wrapper if one exists" in out
        assert "fallback behavior only when the wrapper is unavailable or fails loudly" in out


def test_default_includes_locate_before_large_read_guidance():
    with tempfile.TemporaryDirectory() as d:
        rc, out, _ = _run(["init", "--dry-run"], cwd=d)
        assert rc == 0
        assert 'hamingja locate "<what you need>"' in out
        assert "hamingja code-atlas" in out
        assert "hamingja repo-health" in out
        assert "read only the returned line range" in out


def test_default_preserves_compaction_safety_invariants():
    with tempfile.TemporaryDirectory() as d:
        rc, out, _ = _run(["init", "--dry-run"], cwd=d)
        assert rc == 0
        assert "enter **review mode** immediately" in out
        assert "No new tool calls that mutate files" in out
        assert "**Original goal**" in out
        assert "**Proposed next step**" in out
        assert "**Hypothesis:**" in out
        assert "**Evidence:**" in out
        assert "**Falsification:**" in out
        assert "Do **not** dump full session history" in out
        assert "**Main agent**: implements" in out
        assert "specific findings or honestly say \"No findings.\"" in out


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
        assert "<!-- BEGIN hamingja workflow profiles -->" in body
        assert "## Workflow rails (hamingja)" in body
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


def test_existing_claude_md_gets_managed_block_appended_without_force():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "CLAUDE.md").write_text("preexisting\n", encoding="utf-8")
        rc, out, err = _run(["init"], cwd=d)
        assert rc == 0
        assert err == ""
        body = (Path(d) / "CLAUDE.md").read_text(encoding="utf-8")
        assert body.startswith("preexisting\n\n")
        assert body.count("<!-- BEGIN hamingja workflow profiles -->") == 1
        assert "# base" in body
        assert "appended to CLAUDE.md" in out
        assert not (Path(d) / "AGENTS.md").exists()


def test_existing_agents_md_gets_managed_block_appended_without_force():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "AGENTS.md").write_text("preexisting AGENTS\n", encoding="utf-8")
        rc, out, err = _run(["init"], cwd=d)
        assert rc == 0
        assert err == ""
        assert not (Path(d) / "CLAUDE.md").exists()
        body = (Path(d) / "AGENTS.md").read_text(encoding="utf-8")
        assert body.startswith("preexisting AGENTS\n\n")
        assert body.count("<!-- BEGIN hamingja workflow profiles -->") == 1
        assert "# base" in body
        assert "appended to AGENTS.md" in out


def test_existing_managed_block_is_replaced_not_duplicated():
    with tempfile.TemporaryDirectory() as d:
        existing = "\n".join(
            [
                "# local instructions",
                "",
                "<!-- BEGIN hamingja workflow profiles -->",
                "old managed content",
                "<!-- END hamingja workflow profiles -->",
                "",
                "tail note",
                "",
            ]
        )
        (Path(d) / "CLAUDE.md").write_text(existing, encoding="utf-8")
        rc, out, err = _run(["init", "--profile", "base"], cwd=d)
        assert rc == 0
        assert err == ""
        body = (Path(d) / "CLAUDE.md").read_text(encoding="utf-8")
        assert "# local instructions" in body
        assert "tail note" in body
        assert "old managed content" not in body
        assert body.count("<!-- BEGIN hamingja workflow profiles -->") == 1
        assert body.count("<!-- END hamingja workflow profiles -->") == 1
        assert "# base" in body
        assert "# debugging" not in body
        assert "updated CLAUDE.md" in out


def test_refuses_weird_canonical_pair_without_force():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "CLAUDE.md").write_text("preexisting\n", encoding="utf-8")
        os.symlink("elsewhere.md", Path(d) / "AGENTS.md")
        rc, _, err = _run(["init"], cwd=d)
        assert rc == 1
        assert "won't guess" in err
        assert "not pointing at the sibling" in err
        assert (Path(d) / "CLAUDE.md").read_text(encoding="utf-8") == "preexisting\n"
        assert os.readlink(Path(d) / "AGENTS.md") == "elsewhere.md"


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


def test_rerun_without_profile_preserves_existing_compiler_language_opt_in():
    with tempfile.TemporaryDirectory() as d:
        profiles = [
            "base",
            "non-convergence",
            "debugging",
            "escalation",
            "review-passes",
            "compiler-language",
        ]
        rc, _, _ = _run(["init", "--profile", ",".join(profiles)], cwd=d)
        assert rc == 0

        rc, out, err = _run(["init"], cwd=d)
        assert rc == 0
        assert err == ""
        body = (Path(d) / "CLAUDE.md").read_text(encoding="utf-8")
        assert "# compiler_language" in body
        assert "(6 profile(s): base, non_convergence, debugging, escalation, review_passes, compiler_language)" in out


def test_rerun_without_profile_reuses_existing_managed_profile_set():
    with tempfile.TemporaryDirectory() as d:
        rc, _, _ = _run(["init", "--profile", "compiler-language"], cwd=d)
        assert rc == 0

        rc, out, err = _run(["init"], cwd=d)
        assert rc == 0
        assert err == ""
        body = (Path(d) / "CLAUDE.md").read_text(encoding="utf-8")
        assert "# compiler_language" in body
        assert "# base" not in body
        assert "(1 profile(s): compiler_language)" in out


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
