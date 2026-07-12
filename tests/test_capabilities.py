from agent_rails.adapters.capabilities import manifest
from agent_rails.adapters.claude_code import CAPABILITIES as CLAUDE_CAPABILITIES
from agent_rails.adapters.codex import CAPABILITIES as CODEX_CAPABILITIES


def test_first_class_manifests_are_versioned_and_runtime_specific():
    codex = manifest("codex")
    claude = manifest("claude_code")
    assert codex["version"] == claude["version"] == 2
    assert codex["quota_probe"] is True
    assert claude["quota_probe"] is False
    assert codex["pre_tool_enforcement"] == "partial"
    assert claude["pre_tool_enforcement"] == "full"
    assert codex["delegation_fallback"] == "monotonic_grants"
    assert CODEX_CAPABILITIES == codex
    assert CLAUDE_CAPABILITIES == claude


def test_runtime_probe_can_downgrade_but_not_upgrade():
    downgraded = manifest("codex", {
        "quota_probe": False,
        "delegation_lineage": True,
        "pre_tool_enforcement": "none",
        "runtime": "other",
    })
    assert downgraded["quota_probe"] is False
    assert downgraded["delegation_lineage"] is False
    assert downgraded["pre_tool_enforcement"] == "none"
    assert downgraded["runtime"] == "codex"


def test_unknown_or_malformed_manifest_request_fails_open():
    assert manifest("unknown") == {}
    assert manifest(None) == {}
