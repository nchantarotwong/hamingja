"""Claude tripwire budget command-family classification."""
from __future__ import annotations

import pytest

from agent_rails.adapters.claude_code import tripwire


@pytest.mark.parametrize("tool_input,expected", [
    ({"command": "agent-rails ledger check"}, "Bash:agent-rails ledger check"),
    ({"command": "agent-rails ledger relevant agent_rails/core/budget.py"}, "Bash:agent-rails ledger relevant"),
    ({"command": "timeout 30 agent-rails ledger add --claim x"}, "Bash:agent-rails ledger add"),
    ({"command": "/usr/local/bin/agent-rails ledger retire old-claim"}, "Bash:agent-rails ledger retire"),
    ({"command": "agent-rails ledger reverify old-claim"}, "Bash:agent-rails ledger reverify"),
    ({"command": "agent-rails ledger unknown"}, "Bash"),
    ({"command": "agent-rails ledger"}, "Bash"),
    ({"command": "agent-rails report"}, "Bash"),
    ({"command": "agent-rails ledger 'unterminated"}, "Bash"),
    ({}, "Bash"),
])
def test_budget_tool_name_for_ledger_commands(tool_input, expected):
    assert tripwire._budget_tool_name("Bash", tool_input) == expected


def test_budget_tool_name_non_bash_is_unchanged():
    assert tripwire._budget_tool_name("Read", {"command": "agent-rails ledger check"}) == "Read"
