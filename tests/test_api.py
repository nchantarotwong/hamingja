"""Shared core API tests — the entry point both adapters call.

Verifies check()/record() agree end-to-end and that record() honors the
opt-out (mode=off) so an opted-out repo stays fully inert.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp(prefix="agent-rails-test-")
os.environ["AGENT_RAILS_STATE_DIR"] = _TMP

from agent_rails.core.api import check, record  # noqa: E402
from agent_rails.core.state import read_recent  # noqa: E402


def _proj(files):
    d = tempfile.mkdtemp(prefix="agent-rails-api-")
    for name, content in files.items():
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(content)
    return d


def test_check_blocks_after_repeats_via_api_in_enforce():
    old = os.environ.get("AGENT_RAILS_MODE")
    try:
        os.environ["AGENT_RAILS_MODE"] = "enforce"
        d = _proj({})
        args = {"command": "npm test"}
        for _ in range(3):
            record("sy", "Bash", args, ok=True, project_dir=d)
        v = check("sy", "Bash", args, project_dir=d)
        assert v.action == "block"
    finally:
        if old is None:
            os.environ.pop("AGENT_RAILS_MODE", None)
        else:
            os.environ["AGENT_RAILS_MODE"] = old


def test_record_is_inert_when_off():
    d = _proj({".agent-rails-off": ""})
    for _ in range(6):
        record("sx", "Bash", {"command": "t"}, ok=False, project_dir=d)
    assert read_recent("sx", 50) == []


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _run import run_module_tests
    sys.exit(run_module_tests(globals()))
