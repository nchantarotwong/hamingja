"""Tests for agent_rails.adapters.claude_code.quota (context-fill probe).

Synthetic transcripts only. Claude has no persisted rate-limit signal, so the
probe reports only context occupancy; these pin that estimate and the fail-open
paths (missing file, malformed lines, no usage, bad types, custom/zero window).
"""
from __future__ import annotations

import json

import pytest

from agent_rails.adapters.claude_code.quota import read_quota
from agent_rails.adapters.codex.quota import QuotaReading

SID = "b920153f-0187-4fb0-8ccd-3b6f2e09d036"


@pytest.fixture(autouse=True)
def claude_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _assistant(usage: dict, model="claude-opus-4-8") -> dict:
    return {"type": "assistant", "message": {"model": model, "usage": usage}}


def _write_transcript(home, session_id, lines, *, project="-Users-me-proj", trailing_newline=True):
    d = home / "projects" / project
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{session_id}.jsonl"
    chunks = [json.dumps(x) if isinstance(x, dict) else x for x in lines]
    body = "\n".join(chunks) + ("\n" if trailing_newline else "")
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_context_fill_from_usage(claude_home):
    _write_transcript(claude_home, SID, [
        _assistant({"input_tokens": 100_000, "cache_read_input_tokens": 50_000,
                    "cache_creation_input_tokens": 0, "output_tokens": 300}),
    ])
    r = read_quota(SID)
    assert isinstance(r, QuotaReading)
    # (100000 + 50000) / 200000 = 75%. output_tokens excluded.
    assert r.context_used_pct == pytest.approx(75.0)
    assert r.window_used_pct is None and r.weekly_used_pct is None
    assert r.source == "claude-transcript"


def test_newest_assistant_usage_wins(claude_home):
    _write_transcript(claude_home, SID, [
        _assistant({"input_tokens": 20_000, "cache_read_input_tokens": 0}),
        {"type": "user", "message": {"role": "user", "content": "hi"}},
        _assistant({"input_tokens": 180_000, "cache_read_input_tokens": 0}),
    ])
    assert read_quota(SID).context_used_pct == pytest.approx(90.0)


def test_custom_context_window(claude_home):
    _write_transcript(claude_home, SID, [
        _assistant({"input_tokens": 500_000, "cache_read_input_tokens": 0}),
    ])
    # Against a 1M window, 500k = 50%.
    assert read_quota(SID, context_window_tokens=1_000_000).context_used_pct == pytest.approx(50.0)


def test_context_pct_clamped(claude_home):
    _write_transcript(claude_home, SID, [
        _assistant({"input_tokens": 900_000, "cache_read_input_tokens": 0}),
    ])
    assert read_quota(SID).context_used_pct == 100.0


def test_zero_window_falls_back_to_default(claude_home):
    _write_transcript(claude_home, SID, [
        _assistant({"input_tokens": 100_000, "cache_read_input_tokens": 0}),
    ])
    # window <= 0 -> default 200000 -> 50%
    assert read_quota(SID, context_window_tokens=0).context_used_pct == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# Fail-open paths
# ---------------------------------------------------------------------------

def test_missing_transcript_none(claude_home):
    assert read_quota("no-such-session") is None


def test_no_projects_dir_none(claude_home):
    assert read_quota(SID) is None


def test_empty_session_id_none(claude_home):
    _write_transcript(claude_home, "", [_assistant({"input_tokens": 1})])
    assert read_quota("") is None


def test_no_usage_lines_none(claude_home):
    _write_transcript(claude_home, SID, [
        {"type": "user", "message": {"role": "user", "content": "hi"}},
        {"type": "summary", "summary": "x"},
    ])
    assert read_quota(SID) is None


def test_malformed_lines_skipped(claude_home):
    _write_transcript(claude_home, SID, [
        "not json", "{broken",
        _assistant({"input_tokens": 40_000, "cache_read_input_tokens": 0}),
    ])
    assert read_quota(SID).context_used_pct == pytest.approx(20.0)


def test_non_dict_message_skipped(claude_home):
    _write_transcript(claude_home, SID, [
        {"type": "assistant", "message": "oops-a-string"},
        _assistant({"input_tokens": 60_000, "cache_read_input_tokens": 0}),
    ])
    assert read_quota(SID).context_used_pct == pytest.approx(30.0)


def test_bool_tokens_rejected(claude_home):
    # isinstance(True, int) is True; must not count True as 1 token.
    _write_transcript(claude_home, SID, [
        _assistant({"input_tokens": True, "cache_read_input_tokens": True}),
    ])
    # No usable numeric field -> None.
    assert read_quota(SID) is None


def test_partial_trailing_write_ignored(claude_home):
    good = json.dumps(_assistant({"input_tokens": 80_000, "cache_read_input_tokens": 0}))
    partial = '{"type":"assistant","message":{"usage":{"input_'
    _write_transcript(claude_home, SID, [good, partial], trailing_newline=False)
    assert read_quota(SID).context_used_pct == pytest.approx(40.0)
