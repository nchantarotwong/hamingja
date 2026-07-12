"""Tests for agent_rails.adapters.codex.quota (the Codex rate-limit probe).

Synthetic rollout fixtures only — never a captured real session. The probe is a
fail-open reader of Codex's append-only session JSONL, so the bulk of these
tests pin the failure paths: missing file, malformed JSON, partial trailing
write, a mid-line tail window, window growth past a giant line, the cap, and
null rate_limits. The happy path and the last_token_usage-vs-total regression
round it out.
"""
from __future__ import annotations

import json
import os
import time

import pytest

from agent_rails.adapters.codex import quota
from agent_rails.adapters.codex.quota import QuotaReading, read_quota

SID = "019f2bb0-0bc9-7460-9afb-3d285b26b886"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def codex_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    return tmp_path


def _token_count_event(
    *,
    primary_pct=6.0,
    secondary_pct=30.0,
    primary_reset=1783137258,
    secondary_reset=1783395399,
    last_total=198513,
    ctx_window=258400,
    plan="prolite",
    total_usage=33488351,
    rate_limits="default",
) -> dict:
    """Build a representative Codex token_count event_msg.

    ``rate_limits="default"`` builds the normal primary/secondary block;
    ``None`` sets the field to JSON null (the credits/"premium" path);
    a dict is used verbatim.
    """
    if rate_limits == "default":
        rate_limits = {
            "limit_id": "codex",
            "primary": {
                "used_percent": primary_pct,
                "window_minutes": 300,
                "resets_at": primary_reset,
            },
            "secondary": {
                "used_percent": secondary_pct,
                "window_minutes": 10080,
                "resets_at": secondary_reset,
            },
            "plan_type": plan,
        }
    return {
        "timestamp": "2026-07-05T00:00:00.000Z",
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {"total_tokens": total_usage},
                "last_token_usage": {"total_tokens": last_total},
                "model_context_window": ctx_window,
            },
            "rate_limits": rate_limits,
        },
    }


def _write_rollout(home, session_id, lines, *, trailing_newline=True, subdir="2026/07/05"):
    """Write a synthetic rollout JSONL and return its path.

    ``lines`` entries may be dicts (JSON-encoded) or raw str/bytes (written
    verbatim — used to inject malformed or oversized lines).
    """
    d = home / "sessions" / subdir
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"rollout-2026-07-05T00-00-00-{session_id}.jsonl"
    chunks = []
    for item in lines:
        if isinstance(item, dict):
            chunks.append(json.dumps(item))
        else:
            chunks.append(item)
    body = "\n".join(chunks)
    if trailing_newline:
        body += "\n"
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_reads_latest_rate_limits(codex_home):
    _write_rollout(codex_home, SID, [_token_count_event()])
    r = read_quota(SID)
    assert isinstance(r, QuotaReading)
    assert r.window_used_pct == 6.0
    assert r.weekly_used_pct == 30.0
    assert r.window_resets_at == 1783137258
    assert r.weekly_resets_at == 1783395399
    assert r.plan_type == "prolite"
    assert r.source == "codex-rollout"


def test_newest_event_wins(codex_home):
    # Two token_count events; the probe must return the LAST (newest) one.
    _write_rollout(
        codex_home,
        SID,
        [
            _token_count_event(primary_pct=10.0, secondary_pct=20.0),
            {"type": "response_item", "payload": {"junk": "x" * 100}},
            _token_count_event(primary_pct=77.0, secondary_pct=88.0),
        ],
    )
    r = read_quota(SID)
    assert r.window_used_pct == 77.0
    assert r.weekly_used_pct == 88.0


# ---------------------------------------------------------------------------
# The last_token_usage regression (the bug the proof caught)
# ---------------------------------------------------------------------------

def test_context_uses_last_not_cumulative_usage(codex_home):
    # total_token_usage is cumulative (tens of millions) and must NOT be used for
    # context occupancy; last_token_usage is the current footprint.
    _write_rollout(
        codex_home,
        SID,
        [_token_count_event(total_usage=33_000_000, last_total=129_200, ctx_window=258_400)],
    )
    r = read_quota(SID)
    # 129200 / 258400 = 50.0, NOT pegged at 100 by the cumulative total.
    assert r.context_used_pct == pytest.approx(50.0)


def test_context_pct_clamped_to_100(codex_home):
    _write_rollout(codex_home, SID, [_token_count_event(last_total=999_999, ctx_window=100_000)])
    r = read_quota(SID)
    assert r.context_used_pct == 100.0


# ---------------------------------------------------------------------------
# Null / partial rate_limits (the credits/"premium" path)
# ---------------------------------------------------------------------------

def test_null_rate_limits_falls_back_to_context(codex_home):
    ev = _token_count_event(rate_limits={
        "limit_id": "premium",
        "primary": None,
        "secondary": None,
        "credits": {"has_credits": False, "balance": "0"},
        "plan_type": "prolite",
    }, last_total=129_200, ctx_window=258_400)
    _write_rollout(codex_home, SID, [ev])
    r = read_quota(SID)
    assert r is not None
    assert r.window_used_pct is None
    assert r.weekly_used_pct is None
    assert r.context_used_pct == pytest.approx(50.0)


def test_no_usable_fields_returns_none(codex_home):
    # rate_limits null AND no usable context -> no reading at all.
    ev = {
        "type": "event_msg",
        "payload": {"type": "token_count", "info": {}, "rate_limits": None},
    }
    _write_rollout(codex_home, SID, [ev])
    assert read_quota(SID) is None


# ---------------------------------------------------------------------------
# Consistency: partial writes, mid-line windows, window growth, the cap
# ---------------------------------------------------------------------------

def test_partial_trailing_line_ignored(codex_home):
    # A complete event, then an in-flight partial write with no trailing newline.
    good = json.dumps(_token_count_event(primary_pct=42.0))
    partial = '{"type":"event_msg","payload":{"type":"token_count","info":{"in'
    _write_rollout(codex_home, SID, [good, partial], trailing_newline=False)
    r = read_quota(SID)
    assert r is not None
    assert r.window_used_pct == 42.0


def test_window_grows_past_giant_trailing_line(codex_home, monkeypatch):
    # token_count event, then a giant tool-output line AFTER it. A tail window
    # smaller than the giant line lands mid-line and must expand to find the
    # event that precedes it.
    monkeypatch.setattr(quota, "_TAIL_INITIAL", 4096)
    monkeypatch.setattr(quota, "_TAIL_CAP", 1 << 20)
    giant = json.dumps({"type": "response_item", "payload": {"output": "z" * 20000}})
    _write_rollout(codex_home, SID, [_token_count_event(primary_pct=13.0), giant])
    r = read_quota(SID)
    assert r is not None
    assert r.window_used_pct == 13.0


def test_event_beyond_cap_fails_open(codex_home, monkeypatch):
    # If the newest token_count is farther from EOF than the cap allows, the
    # probe gives up (fail-open None) rather than read the whole file.
    monkeypatch.setattr(quota, "_TAIL_INITIAL", 512)
    monkeypatch.setattr(quota, "_TAIL_CAP", 1024)
    giant = json.dumps({"type": "response_item", "payload": {"output": "z" * 5000}})
    _write_rollout(codex_home, SID, [_token_count_event(), giant])
    assert read_quota(SID) is None


# ---------------------------------------------------------------------------
# Malformed / missing / degenerate inputs — all fail open
# ---------------------------------------------------------------------------

def test_missing_file_returns_none(codex_home):
    assert read_quota("no-such-session-id") is None


def test_no_sessions_dir_returns_none(codex_home):
    # CODEX_HOME exists but has no sessions/ subdir.
    assert read_quota(SID) is None


def test_empty_session_id_returns_none(codex_home):
    _write_rollout(codex_home, "", [_token_count_event()])
    assert read_quota("") is None
    assert read_quota("   ") is None


def test_malformed_json_lines_skipped(codex_home):
    _write_rollout(
        codex_home,
        SID,
        ["not json at all", "{broken", _token_count_event(primary_pct=9.0), "}{"],
    )
    # newest valid token_count still found despite garbage around it
    r = read_quota(SID)
    assert r is not None
    assert r.window_used_pct == 9.0


def test_empty_file_returns_none(codex_home):
    _write_rollout(codex_home, SID, [], trailing_newline=False)
    assert read_quota(SID) is None


def test_stale_rollout_cannot_supply_quota_evidence(codex_home):
    path = _write_rollout(
        codex_home, SID, [_token_count_event(primary_pct=99.0)]
    )
    stale = time.time() - quota._READING_TTL_SECONDS - 1
    os.utime(path, (stale, stale))
    assert read_quota(SID) is None


def test_no_token_count_events_returns_none(codex_home):
    _write_rollout(
        codex_home,
        SID,
        [
            {"type": "session_meta", "payload": {"id": SID}},
            {"type": "response_item", "payload": {"output": "hello"}},
        ],
    )
    assert read_quota(SID) is None


def test_wrong_types_in_fields_fail_open(codex_home):
    # used_percent as a string, resets_at as a bool, context window as a list.
    ev = {
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "last_token_usage": {"total_tokens": "lots"},
                "model_context_window": [1, 2],
            },
            "rate_limits": {
                "primary": {"used_percent": "high", "resets_at": True},
                "secondary": {"used_percent": None},
            },
        },
    }
    _write_rollout(codex_home, SID, [ev])
    # Nothing coerces to a usable number -> no reading.
    assert read_quota(SID) is None


def test_bool_is_not_a_number(codex_home):
    # Guard: isinstance(True, int) is True in Python; used_percent=True must not
    # be read as 1.0%.
    ev = _token_count_event()
    ev["payload"]["rate_limits"]["primary"]["used_percent"] = True
    _write_rollout(codex_home, SID, [ev])
    r = read_quota(SID)
    # primary rejected, but secondary still valid -> reading present, window None.
    assert r is not None
    assert r.window_used_pct is None
    assert r.weekly_used_pct == 30.0
