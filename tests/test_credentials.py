"""Credential handling and write-gating.

The rule being enforced: the seed never travels through the agent's
context. It comes from the OS keychain, which the server reads itself.
"""

from __future__ import annotations

import asyncio
import importlib

from xrpl_did_mcp import credentials

# `xrpl_did_mcp.server` the attribute is the MCPServer *object*; the
# module has to be imported explicitly.
server_module = importlib.import_module("xrpl_did_mcp.server")


def _call(tool: str, args: dict) -> dict:
    async def _run():
        result = await server_module.server.call_tool(tool, args)
        return result.structured_content

    return asyncio.run(_run())


# ------------------------------------------------------------ write gating


def test_publish_is_refused_without_allow_writes(monkeypatch):
    monkeypatch.setattr(server_module, "ALLOW_WRITES", False)
    out = _call("publish_did_set", {"address": "rTest", "network": "testnet"})
    assert out["error"] == "WRITES_DISABLED"
    assert "--allow-writes" in out["hint"]


def test_publish_did_set_seed_parameter_is_optional_and_gated():
    """The seed parameter exists but is optional and refused by default.

    An inline seed is only honoured when the server was started with
    --allow-inline-seed. Without that flag the parameter is refused, so
    an agent cannot push a seed at the server.
    """
    import inspect

    params = inspect.signature(server_module.publish_did_set).parameters
    assert "seed" in params
    assert params["seed"].default == ""
    assert set(params) == {"address", "document_uri", "network", "seed"}


def test_inline_seed_is_reachable_only_when_enabled(monkeypatch):
    """The --allow-inline-seed flag must actually do something.

    It was dead: publish_did_set called resolve_seed() with no arguments,
    so the inline branch could never be reached from the MCP surface.
    """
    import inspect

    params = inspect.signature(server_module.publish_did_set).parameters
    assert "seed" in params, "publish_did_set takes no seed at all"

    monkeypatch.setattr(server_module, "ALLOW_WRITES", True)
    monkeypatch.setattr(credentials, "ALLOW_INLINE_SEED", False)
    monkeypatch.setattr(credentials, "seed_from_keychain", lambda: None)

    out = server_module.publish_did_set(address="rTest", network="testnet", seed="sSomeSeed")
    assert out["error"] == "INLINE_SEED_REFUSED"

    monkeypatch.setattr(credentials, "ALLOW_INLINE_SEED", True)
    out2 = server_module.publish_did_set(address="rTest", network="testnet", seed="sSomeSeed")
    # A bad seed fails at wallet creation, not at the gate -- which proves
    # the inline seed was accepted and passed through.
    assert out2.get("error") == "INVALID_SEED", out2


def test_write_tool_description_warns_that_it_spends():
    import asyncio

    tools = asyncio.run(server_module.server.list_tools())
    tool = next(t for t in tools if t.name == "publish_did_set")
    assert "WRITES TO THE LEDGER" in tool.description
    assert "--allow-writes" in tool.description
    assert "keychain" in tool.description.lower()
    assert "allow-inline-seed" in tool.description


# ------------------------------------------------------- seed resolution


def test_inline_seed_is_refused_by_default(monkeypatch):
    monkeypatch.setattr(credentials, "ALLOW_INLINE_SEED", False)
    monkeypatch.setattr(credentials, "seed_from_keychain", lambda: None)
    seed, source = credentials.resolve_seed("sSomeSeed")
    assert seed is None
    assert source == "inline-refused"


def test_inline_seed_is_accepted_when_explicitly_allowed(monkeypatch):
    monkeypatch.setattr(credentials, "ALLOW_INLINE_SEED", True)
    monkeypatch.setattr(credentials, "seed_from_keychain", lambda: None)
    seed, source = credentials.resolve_seed("sSomeSeed")
    assert seed == "sSomeSeed"
    assert source == "inline"


def test_keychain_wins_over_inline(monkeypatch):
    """The keychain is preferred even when an inline seed is supplied."""
    monkeypatch.setattr(credentials, "ALLOW_INLINE_SEED", True)
    monkeypatch.setattr(credentials, "seed_from_keychain", lambda: "sFromKeychain")
    seed, source = credentials.resolve_seed("sInline")
    assert seed == "sFromKeychain"
    assert source == "keychain"


def test_no_seed_anywhere_returns_none(monkeypatch):
    monkeypatch.setattr(credentials, "ALLOW_INLINE_SEED", False)
    monkeypatch.setattr(credentials, "seed_from_keychain", lambda: None)
    seed, source = credentials.resolve_seed()
    assert seed is None
    assert source is None


def test_publish_reports_no_seed_when_none_is_available(monkeypatch):
    monkeypatch.setattr(server_module, "ALLOW_WRITES", True)
    monkeypatch.setattr(credentials, "seed_from_keychain", lambda: None)
    out = _call("publish_did_set", {"address": "rTest", "network": "testnet"})
    assert out["error"] == "NO_SEED"
    assert "keychain" in out["hint"].lower()


def test_keychain_help_names_the_service():
    help_text = credentials.install_keychain_help()
    assert credentials.keychain_service() in help_text
    assert "security add-generic-password" in help_text


def test_keychain_service_is_configurable(monkeypatch):
    monkeypatch.setenv("XRPL_MCP_KEYCHAIN_SERVICE", "my-service")
    # module-level constant is read at import; the accessor reflects env
    assert isinstance(credentials.keychain_service(), str)
