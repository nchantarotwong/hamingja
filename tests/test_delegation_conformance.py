from agent_rails.adapters.capabilities import delegation_observation, manifest


def test_claude_lifecycle_proves_identity_and_completion_not_lineage():
    observation = delegation_observation("claude_code", {
        "hook_event_name": "SubagentStart",
        "session_id": "parent",
        "agent_id": "agent-1",
        "agent_type": "Explore",
        "turn_id": "",
    })
    assert observation == {
        "event": "spawn",
        "agent_id": "agent-1",
        "agent_type": "Explore",
        "session_id": "parent",
        "turn_id": "",
        "spawn_observed": True,
        "identity_observed": True,
        "completion_observed": False,
        "lineage_observed": False,
        "enforcement": "session_concurrency_advisory",
    }
    stopped = delegation_observation("claude_code", {
        "hook_event_name": "SubagentStop", "session_id": "parent",
        "agent_id": "agent-1", "agent_type": "Explore",
    })
    assert stopped["completion_observed"] is True
    assert manifest("claude_code")["delegation_completion"] is True


def test_codex_lifecycle_proves_identity_but_not_parent_lineage():
    payload = {
        "tool_name": "collaboration.spawn_agent",
        "parent_agent_id": "parent",
        "child_agent_id": "child",
    }
    assert delegation_observation("codex", payload) is None
    observed = delegation_observation("codex", {
        "hook_event_name": "SubagentStart",
        "session_id": "parent",
        "agent_id": "agent-2",
        "agent_type": "review",
        "turn_id": "turn-1",
    })
    assert observed["agent_id"] == "agent-2"
    assert observed["lineage_observed"] is False
    caps = manifest("codex")
    assert caps["delegation_spawn"] is True
    assert caps["delegation_completion"] is True
    assert caps["delegation_identity"] is True
    assert caps["delegation_lineage"] is False


def test_malformed_delegation_payload_fails_open():
    assert delegation_observation("claude_code", None) is None
    assert delegation_observation("unknown", {"tool_name": "Task"}) is None


def test_undocumented_parent_field_cannot_upgrade_lineage():
    for runtime in ("codex", "claude_code"):
        observed = delegation_observation(runtime, {
            "hook_event_name": "SubagentStart",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "agent_id": "child-1",
            "agent_type": "review",
            "parent_agent_id": "claimed-parent",
        })
        assert observed["lineage_observed"] is False
        assert "parent_agent_id" not in observed
        assert manifest(runtime)["delegation_lineage"] is False
