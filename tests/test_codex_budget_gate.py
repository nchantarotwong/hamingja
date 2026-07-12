"""Codex tripwire budget-gate wiring, fed by the real quota probe.

These test the *integration* the tripwire owns — budget-command exemption, the
fail-open quota fetch, and that a low real quota defers the checkpoint end to
end — not the gate math (test_budget.py) or the probe (test_codex_quota.py).
Driven in-process (monkeypatched stdin + load_config) so no subprocess hangs on
the default 60s approval poll.
"""
from __future__ import annotations

import io
import json

import pytest

import hamingja.config as config_mod
from hamingja.adapters.codex import tripwire
from hamingja.core import budget as budget_mod

SID = "019f2bb0-0bc9-7460-9afb-3d285b26b886"

# Tight, non-polling budget config: checkpoint at 12, hard block at 20.
_BUDGET_CFG = {
    "enabled": True,
    "nudge_at": 8,
    "checkpoint_at": 12,
    "hard_block_at": 20,
    "poll_timeout_s": 0,
}


# ---------------------------------------------------------------------------
# _is_budget_command — Codex arg shapes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool_input,expected", [
    ({"command": "hamingja budget s add 3 --self"}, True),
    ({"parameters": {"command": "hamingja budget s reset"}}, True),
    ({"args": {"cmd": "  hamingja budget s"}}, True),
    ({"command": "hamingja report"}, False),
    ({"command": "ls -la"}, False),
    ({}, False),
])
def test_is_budget_command(tool_input, expected):
    assert tripwire._is_budget_command("Bash", tool_input) is expected


def test_is_budget_command_non_bash():
    assert tripwire._is_budget_command("Read", {"command": "hamingja budget s"}) is False


@pytest.mark.parametrize("tool_input,expected", [
    ({"command": "hamingja ledger check"}, "Bash:hamingja ledger check"),
    ({"command": "hamingja ledger relevant hamingja/core/budget.py"}, "Bash:hamingja ledger relevant"),
    ({"command": "timeout 30 hamingja ledger add --claim x"}, "Bash:hamingja ledger add"),
    ({"command": "/usr/local/bin/hamingja ledger retire old-claim"}, "Bash:hamingja ledger retire"),
    ({"command": "hamingja ledger reverify old-claim"}, "Bash:hamingja ledger reverify"),
    ({"command": "hamingja ledger unknown"}, "Bash"),
    ({"command": "hamingja ledger"}, "Bash"),
    ({"command": "hamingja report"}, "Bash"),
    ({"command": "hamingja ledger 'unterminated"}, "Bash"),
])
def test_budget_tool_name_for_ledger_commands(tool_input, expected):
    assert tripwire._budget_tool_name("Bash", tool_input) == expected


def test_budget_tool_name_non_bash_is_unchanged():
    assert tripwire._budget_tool_name("Read", {"command": "hamingja ledger check"}) == "Read"


# ---------------------------------------------------------------------------
# _read_quota_safe — always fail-open
# ---------------------------------------------------------------------------

def test_read_quota_safe_missing_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert tripwire._read_quota_safe("no-such-session") is None


def test_read_quota_safe_reads_rollout(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_rollout(tmp_path, SID, window=6.0, weekly=30.0)
    r = tripwire._read_quota_safe(SID)
    assert r is not None
    assert r.window_used_pct == 6.0


# ---------------------------------------------------------------------------
# End-to-end: budget block vs. quota-deferred checkpoint through main()
# ---------------------------------------------------------------------------

def _write_rollout(codex_home, session_id, *, window, weekly):
    d = codex_home / "sessions" / "2026" / "07" / "05"
    d.mkdir(parents=True, exist_ok=True)
    ev = {
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "last_token_usage": {"total_tokens": 1000},
                "model_context_window": 258400,
            },
            "rate_limits": {
                "primary": {"used_percent": window, "window_minutes": 300, "resets_at": 1},
                "secondary": {"used_percent": weekly, "window_minutes": 10080, "resets_at": 2},
                "plan_type": "prolite",
            },
        },
    }
    (d / f"rollout-2026-07-05T00-00-00-{session_id}.jsonl").write_text(
        json.dumps(ev) + "\n", encoding="utf-8"
    )


def _seed_at_checkpoint(state_dir, session_id, checkpoint_at):
    path = budget_mod._budget_path(session_id)  # honors HAMINGJA_STATE_DIR
    state = budget_mod._default_state(checkpoint_at)
    state["tool_calls"] = checkpoint_at
    state["weighted_calls"] = float(checkpoint_at)
    state["approved_tool_calls"] = checkpoint_at
    path.write_text(json.dumps(state), encoding="utf-8")


def _run_main(payload, monkeypatch, capsys):
    monkeypatch.setattr(config_mod, "load_config", lambda cwd=None: {"mode": "observe", "budget": _BUDGET_CFG})
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    rc = tripwire.main()
    out = capsys.readouterr().out
    return rc, (json.loads(out) if out.strip() else {})


@pytest.fixture
def gate_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HAMINGJA_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    return tmp_path


def test_checkpoint_blocks_without_quota(gate_env, monkeypatch, capsys):
    # No rollout -> no reading -> checkpoint fires as a hard deny.
    _seed_at_checkpoint(gate_env / "state", SID, 12)
    payload = {"session_id": SID, "tool_name": "Bash", "tool_input": {"command": "echo hi"}, "cwd": str(gate_env)}
    _, out = _run_main(payload, monkeypatch, capsys)
    hso = out.get("hookSpecificOutput", {})
    assert hso.get("permissionDecision") == "deny"
    assert "Checkpoint" in hso.get("permissionDecisionReason", "")


def test_low_quota_defers_checkpoint(gate_env, monkeypatch, capsys):
    # Same state, but a low real quota -> checkpoint deferred to an advisory nudge.
    _seed_at_checkpoint(gate_env / "state", SID, 12)
    _write_rollout(gate_env / "codex", SID, window=6.0, weekly=30.0)
    payload = {"session_id": SID, "tool_name": "Bash", "tool_input": {"command": "echo hi"}, "cwd": str(gate_env)}
    _, out = _run_main(payload, monkeypatch, capsys)
    hso = out.get("hookSpecificOutput", {})
    assert "permissionDecision" not in hso  # NOT denied
    assert "deferred" in hso.get("additionalContext", "").lower()


def test_high_quota_still_blocks(gate_env, monkeypatch, capsys):
    _seed_at_checkpoint(gate_env / "state", SID, 12)
    _write_rollout(gate_env / "codex", SID, window=6.0, weekly=95.0)  # weekly hot
    payload = {"session_id": SID, "tool_name": "Bash", "tool_input": {"command": "echo hi"}, "cwd": str(gate_env)}
    _, out = _run_main(payload, monkeypatch, capsys)
    assert out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def test_budget_command_is_exempt(gate_env, monkeypatch, capsys):
    # A self-approve command at the checkpoint must NOT be gated (no wedge).
    _seed_at_checkpoint(gate_env / "state", SID, 12)
    payload = {
        "session_id": SID, "tool_name": "Bash",
        "tool_input": {"command": f"hamingja budget {SID} add 3 --self"},
        "cwd": str(gate_env),
    }
    _, out = _run_main(payload, monkeypatch, capsys)
    assert out.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"


def test_ledger_relevant_uses_zero_weight(gate_env, monkeypatch, capsys):
    for _ in range(20):
        payload = {
            "session_id": SID,
            "tool_name": "Bash",
            "tool_input": {"command": "hamingja ledger relevant hamingja/core/budget.py"},
            "cwd": str(gate_env),
        }
        _, out = _run_main(payload, monkeypatch, capsys)
        assert out.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"
    state = budget_mod.read_state(SID)
    assert state["tool_calls"] == 20
    assert state["weighted_calls"] == 0.0


def test_ledger_add_uses_low_nonzero_weight(gate_env, monkeypatch, capsys):
    for _ in range(10):
        payload = {
            "session_id": SID,
            "tool_name": "Bash",
            "tool_input": {"command": "hamingja ledger add --claim x --evidence y --falsifier z"},
            "cwd": str(gate_env),
        }
        _, out = _run_main(payload, monkeypatch, capsys)
        assert out.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"
    state = budget_mod.read_state(SID)
    assert state["tool_calls"] == 10
    assert state["weighted_calls"] == pytest.approx(2.0)
