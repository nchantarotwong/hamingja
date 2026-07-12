from agent_rails.adapters.capabilities import delegation_observation, manifest


def test_claude_task_proves_spawn_but_not_identity_completion_or_lineage():
    observation = delegation_observation("claude_code", {
        "tool_name": "Task",
        "tool_input": {"prompt": "bounded review"},
    })
    assert observation == {
        "event": "spawn",
        "spawn_observed": True,
        "identity_observed": False,
        "completion_observed": False,
        "lineage_observed": False,
        "enforcement": "monotonic_grants",
    }
    assert manifest("claude_code")["delegation_fallback"] == "monotonic_grants"


def test_codex_tool_hook_cannot_claim_collaboration_lineage():
    payload = {
        "tool_name": "collaboration.spawn_agent",
        "parent_agent_id": "parent",
        "child_agent_id": "child",
    }
    assert delegation_observation("codex", payload) is None
    caps = manifest("codex")
    assert caps["delegation_spawn"] is False
    assert caps["delegation_completion"] is False
    assert caps["delegation_lineage"] is False


def test_malformed_delegation_payload_fails_open():
    assert delegation_observation("claude_code", None) is None
    assert delegation_observation("unknown", {"tool_name": "Task"}) is None
