"""Claude tripwire budget command-family classification."""
from __future__ import annotations

import pytest

from hamingja.adapters.claude_code import tripwire


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
    ({}, "Bash"),
])
def test_budget_tool_name_for_ledger_commands(tool_input, expected):
    assert tripwire._budget_tool_name("Bash", tool_input) == expected


def test_budget_tool_name_non_bash_is_unchanged():
    assert tripwire._budget_tool_name("Read", {"command": "hamingja ledger check"}) == "Read"
