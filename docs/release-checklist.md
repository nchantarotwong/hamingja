# Release checklist

Use this checklist from a clean `main` worktree. Publishing and tagging are
external, irreversible actions and require explicit operator confirmation.

1. Choose the version and update `agent_rails.__version__` plus the changelog
   heading/date.
2. Run the full synthetic suite with pipefail and retain `.pytest_output.log`.
   Use Python 3.13, the declared minimum; `python:3.13-slim` is the preferred
   container smoke environment when container validation is available.
3. Build both distributions in isolation:

   ```bash
   python -m build
   ```

4. Inspect the sdist and wheel for `LICENSE`, `NOTICE`, packaged configuration,
   profiles, templates, and both adapter installers.
5. Install the wheel into a fresh virtual environment and verify:

   ```bash
   agent-rails --version
   agent-rails --help
   agent-rails status .
   ```

6. With temporary `CLAUDE_SETTINGS` and `CODEX_HOOKS` paths, exercise fresh
   install, repeat install (no change/no extra backup), upgrade from old
   agent-rails paths, and uninstall while preserving unrelated hooks.
7. Verify README links, package metadata, Python requirement, and the installed
   `agent-rails init --dry-run` output. Confirm the documented pipx install,
   upgrade, hook refresh, process restart, and uninstall guidance matches the
   built artifact.
8. Review the final diff and distribution metadata. Confirm the worktree has no
   captured sessions, local state, or unrelated files.
9. After explicit approval, publish the immutable artifacts and create the
   corresponding signed/annotated tag and release notes.
