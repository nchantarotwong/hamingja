import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_rails.cli as cli_module  # noqa: E402
from agent_rails.cli import main  # noqa: E402
from agent_rails.locator import Location, _rg_hits, format_locations, locate  # noqa: E402


def test_locate_returns_ranked_ranges_not_contents(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    target = src / "server.py"
    target.write_text(
        "\n".join(
            [
                "class Handler:",
                "    def do_GET(self):",
                "        if self.path == '/pick-directory':",
                "            return self.pick_directory()",
                "",
                "    def other(self):",
                "        return None",
            ]
        )
    )
    (src / "client.py").write_text("def call_api():\n    return 'pick directory endpoint'\n")

    results = locate("do_GET pick directory route", root=tmp_path, max_results=3)

    assert results
    assert results[0].path == target.resolve()
    assert results[0].start == 2
    assert results[0].end <= 7


def test_locate_symbol_boosts_definition(tmp_path):
    path = tmp_path / "server.py"
    path.write_text(
        "\n".join(
            [
                "def helper():",
                "    return 'do_GET reference'",
                "",
                "class Handler:",
                "    def do_GET(self):",
                "        return 'ok'",
            ]
        )
    )

    results = locate("do_GET", root=tmp_path, symbol=True)

    assert results
    assert results[0].start == 5
    assert "symbol definition" in results[0].reason


def test_locate_honors_glob(tmp_path):
    (tmp_path / "a.py").write_text("def route():\n    return 'pick directory'\n")
    (tmp_path / "a.md").write_text("pick directory docs\n")

    results = locate("pick directory", root=tmp_path, glob="*.py")

    assert results
    assert {r.path.name for r in results} == {"a.py"}


def test_locate_fallback_scans_single_file_root(tmp_path, monkeypatch):
    path = tmp_path / "server.py"
    path.write_text("def route():\n    return 'pick directory'\n")
    monkeypatch.setattr("agent_rails.locator.shutil.which", lambda _name: None)

    results = locate("pick directory", root=path)

    assert results
    assert results[0].path == path.resolve()


def test_locate_empty_or_missing_root_fails_open(tmp_path):
    assert locate("", root=tmp_path) == []
    assert locate("anything", root=tmp_path / "missing") == []


def test_cli_locate_prints_sed_commands(tmp_path):
    path = tmp_path / "server.py"
    path.write_text("def do_GET(self):\n    return 'pick directory'\n")

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["locate", "do_GET pick directory", "--dir", str(tmp_path)])

    out = buf.getvalue()
    assert rc == 0
    assert "Likely targets:" in out
    assert "server.py:1-2 -" in out
    assert "sed -n '1,2p'" in out
    assert str(path.resolve()) in out


def test_cli_locate_symbol_parses(tmp_path):
    (tmp_path / "server.py").write_text("def do_GET(self):\n    return 'ok'\n")

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["locate-symbol", "do_GET", "--dir", str(tmp_path)])

    assert rc == 0
    assert "symbol definition" in buf.getvalue()


def test_rg_json_parsing_handles_colons_in_filenames(tmp_path, monkeypatch):
    path = tmp_path / "name:with:colon.py"
    path.write_text("def route():\n    return 'pick directory'\n")
    payload = {
        "type": "match",
        "data": {
            "path": {"text": str(path)},
            "lines": {"text": "    return 'pick directory'\n"},
            "line_number": 2,
        },
    }

    monkeypatch.setattr("agent_rails.locator.shutil.which", lambda _name: "/usr/bin/rg")

    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload) + "\n")

    monkeypatch.setattr("agent_rails.locator.subprocess.run", fake_run)

    hits = _rg_hits(tmp_path, ["pick", "directory"], None)

    assert len(hits) == 1
    assert hits[0].path == path.resolve()
    assert hits[0].line == 2


def test_format_locations_empty_result_stays_quiet(tmp_path):
    assert format_locations([], root=tmp_path) == "No likely targets found."


def test_cli_locate_fails_open_when_formatter_raises(monkeypatch):
    monkeypatch.setattr(cli_module, "format_locations", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["locate", "anything"])

    assert rc == 0
    assert buf.getvalue().strip() == "No likely targets found."


def test_format_locations_uses_runnable_command_path_for_external_root(tmp_path):
    path = tmp_path / "server.py"
    path.write_text("def route():\n    return 'ok'\n")
    loc = Location(path.resolve(), 1, 2, 0.5, "text matches")

    out = format_locations([loc], root=tmp_path)

    assert "server.py:1-2" in out
    assert str(path.resolve()) in out
