import io
import json
import os
import sys
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_rails.cli import main  # noqa: E402
from agent_rails.code_atlas import build_code_atlas, format_code_atlas, format_repo_health, repo_health  # noqa: E402
from agent_rails.locator import locate  # noqa: E402


def _large_source(extra_lines=220):
    lines = [
        "class Handler:",
        "    def do_GET(self):",
        "        return self._pick_directory()",
        "",
        "    def do_POST(self):",
        "        return None",
        "",
        "def _pick_directory():",
        "    return '/tmp'",
    ]
    lines.extend(f"# filler {i}" for i in range(extra_lines))
    return "\n".join(lines)


def test_code_atlas_maps_symbols_without_file_contents(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    path = src / "ui.py"
    path.write_text(_large_source(), encoding="utf-8")

    atlas = build_code_atlas(tmp_path, min_lines=200)
    out = format_code_atlas(atlas, root=tmp_path)

    assert len(atlas) == 1
    assert "src/ui.py" in out
    assert "Handler  lines 1-" in out
    assert "do_GET  lines 2-" in out
    assert "_pick_directory  lines 8-" in out
    assert "return '/tmp'" not in out


def test_code_atlas_excludes_generated_build_and_vendor_trees(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "kept.py").write_text(_large_source(), encoding="utf-8")
    for dirname in ("generated", "build", "vendor", "target", "out"):
        directory = tmp_path / dirname
        directory.mkdir()
        (directory / "ignored.py").write_text(_large_source(), encoding="utf-8")

    atlas = build_code_atlas(tmp_path, min_lines=200)

    assert [item.path.relative_to(tmp_path).as_posix() for item in atlas] == ["src/kept.py"]


def test_cli_code_atlas_prints_map(tmp_path):
    (tmp_path / "ui.py").write_text(_large_source(), encoding="utf-8")

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["code-atlas", str(tmp_path), "--min-lines", "200"])

    assert rc == 0
    out = buf.getvalue()
    assert "Code Atlas:" in out
    assert "ui.py" in out
    assert "_pick_directory" in out


def test_cli_code_atlas_json_marks_possible_truncation(tmp_path):
    (tmp_path / "one.py").write_text(_large_source(), encoding="utf-8")
    (tmp_path / "two.py").write_text(_large_source(), encoding="utf-8")
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["code-atlas", str(tmp_path), "--min-lines", "200", "--max-files", "1", "--json"])
    payload = json.loads(buf.getvalue())
    assert rc == 0
    assert payload["schema_version"] == 1
    assert payload["kind"] == "code_atlas"
    assert payload["incomplete"] is True
    assert len(payload["files"]) == 1


def test_locate_uses_code_atlas_symbol_names_before_text_scan(tmp_path):
    path = tmp_path / "ui.py"
    path.write_text(
        "\n".join(
            [
                "def _pick_directory():",
                "    return config_value",
                *[f"value_{i} = {i}" for i in range(220)],
            ]
        ),
        encoding="utf-8",
    )

    results = locate("directory picker", root=tmp_path, max_results=1)

    assert results
    assert results[0].path == path.resolve()
    assert results[0].start == 1
    assert "code atlas" in results[0].reason


def test_repo_health_reports_large_files_and_split_hints(tmp_path):
    path = tmp_path / "ui.py"
    path.write_text(_large_source(extra_lines=1100), encoding="utf-8")

    health = repo_health(tmp_path, min_lines=1000)
    out = format_repo_health(health, root=tmp_path)

    assert len(health) == 1
    assert "Repo Health:" in out
    assert "ui.py" in out
    assert "token unscoped read" in out
    assert "handler.py" in out
    assert "pick_directory.py" in out


def test_repo_health_split_hints_preserve_source_language(tmp_path):
    path = tmp_path / "service.ts"
    path.write_text("\n".join([
        "class RequestRouter {}",
        "function parseRequest() {}",
        *[f"// filler {i}" for i in range(1000)],
    ]), encoding="utf-8")
    health = repo_health(tmp_path, min_lines=1000)
    assert "requestrouter.ts" in health[0].suggestions
    assert all(not item.endswith(".py") for item in health[0].suggestions)


def test_generated_artifact_is_labeled_without_split_advice(tmp_path):
    path = tmp_path / "schema.generated.ts"
    path.write_text("\n".join([
        "// AUTO-GENERATED; DO NOT EDIT",
        "class GeneratedSchema {}",
        *[f"// filler {i}" for i in range(1000)],
    ]), encoding="utf-8")
    health = repo_health(tmp_path, min_lines=1000)
    out = format_repo_health(health, root=tmp_path)
    assert health[0].generated is True
    assert health[0].suggestions == []
    assert "generated artifact; source split advice suppressed" in out


def test_repo_health_reports_large_files_without_symbols(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("\n".join(f"plain line {i}" for i in range(1000)), encoding="utf-8")

    health = repo_health(tmp_path, min_lines=1000)
    out = format_repo_health(health, root=tmp_path)

    assert len(health) == 1
    assert health[0].path == path.resolve()
    assert "notes.md" in out
    assert "Suggested splits" not in out


def test_cli_repo_health_prints_large_file_summary(tmp_path):
    (tmp_path / "ui.py").write_text(_large_source(extra_lines=1100), encoding="utf-8")

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["repo-health", str(tmp_path), "--min-lines", "1000"])

    assert rc == 0
    assert "Repo Health:" in buf.getvalue()
