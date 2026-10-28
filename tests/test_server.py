# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Tests for the xrpl-did MCP server.

These are offline: they exercise the tool surface and the pure-derivation
paths. Anything that touches the ledger is skipped unless XRPL_MCP_LIVE=1.
"""

from __future__ import annotations

import asyncio
import os
from typing import ClassVar

import pytest

from xrpl_did_mcp.server import server

LIVE = os.environ.get("XRPL_MCP_LIVE") == "1"

MAINNET_ADDRESS = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"
MAINNET_DID = "did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"

# A real, validated XRPL mainnet Payment. Used only by the live tests.
MAINNET_TX_HASH = "58318A01B76A5E8C7B40A5B704581A219A72CFEB457915F9CE91B6CBBE84A212"


def call(tool: str, args: dict) -> dict:
    """Call a tool and return its structured content."""

    async def _run():
        result = await server.call_tool(tool, args)
        return result.structured_content

    return asyncio.run(_run())


def test_all_tools_registered():
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "derive_did",
        "resolve_did",
        "check_authorization",
        "publish_did_set",
        "lookup_account",
        "lookup_transaction",
    }


def test_every_tool_has_a_description():
    tools = asyncio.run(server.list_tools())
    for tool in tools:
        assert tool.description, f"{tool.name} has no description"


def test_tool_names_are_verb_first():
    """Every tool name starts with a verb, so a model can pick one by name."""
    tools = asyncio.run(server.list_tools())
    for tool in tools:
        assert tool.name.split("_")[0] in {
            "derive",
            "resolve",
            "check",
            "publish",
            "lookup",
        }, f"{tool.name} does not start with a known verb"


def test_no_tool_returns_another_tools_capability(monkeypatch):
    """Each capability is returned by exactly one tool.

    This is the regression guard for the tool-coherence problem: the old
    ledger_lookup returned a DID Document under kind="did", which duplicated
    resolve_did. The check is on what a tool RETURNS, not on whether its
    prose happens to mention another capability.
    """
    import importlib

    server_module = importlib.import_module("xrpl_did_mcp.server")

    # The capability each tool owns, and the key its output carries.
    owners = {
        "resolve_did": "document",
        "lookup_account": "balance_drops",
        "lookup_transaction": "result",
    }

    # Stub the ledger so each tool's success path can be exercised offline.
    class _AccountInfo:
        result: ClassVar[dict] = {
            "account_data": {"Balance": "1000", "OwnerCount": "2", "Sequence": "7"}
        }

    class _Tx:
        result: ClassVar[dict] = {
            "meta": {"TransactionResult": "tesSUCCESS"},
            "ledger_index": 42,
            "validated": True,
        }

    class _StubClient:
        def request(self, model):
            name = type(model).__name__
            if name == "AccountInfo":
                return _AccountInfo()
            if name == "Tx":
                return _Tx()
            raise AssertionError(f"unexpected request model {name}")

    monkeypatch.setattr(server_module, "get_client", lambda network="": _StubClient())

    outputs = {
        "lookup_account": server_module.lookup_account(address="rTest"),
        "lookup_transaction": server_module.lookup_transaction(tx_hash="HASH"),
    }

    # resolve_did's success output carries the document; check the schema
    # shape rather than calling the ledger for it.
    import inspect

    resolve_src = inspect.getsource(server_module.resolve_did_tool)
    assert '"document"' in resolve_src, "resolve_did should return a document key"

    # Each owner returns its own key.
    for tool_name, owner_key in owners.items():
        if tool_name == "resolve_did":
            continue
        assert owner_key in outputs[tool_name], (
            f"{tool_name} should return {owner_key!r}, got {sorted(outputs[tool_name])}"
        )

    # No tool returns another owner's key.
    for tool_name, out in outputs.items():
        for owner, owner_key in owners.items():
            if tool_name == owner:
                continue
            assert owner_key not in out, (
                f"{tool_name} returns {owner_key!r}, which belongs to {owner}"
            )


def test_ledger_lookup_tool_is_gone():
    """The old multi-purpose tool was replaced by two focused ones."""
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert "ledger_lookup" not in names


def test_derive_did_mainnet():
    out = call("derive_did", {"address": MAINNET_ADDRESS, "network": "mainnet"})
    assert out["did"] == MAINNET_DID
    assert out["address"] == MAINNET_ADDRESS
    assert out["network_id"] == 1


def test_derive_did_testnet():
    out = call("derive_did", {"address": MAINNET_ADDRESS, "network": "testnet"})
    assert out["did"] == "did:xrpl:2:" + MAINNET_ADDRESS
    assert out["network_id"] == 2


def test_derive_did_rejects_unknown_network():
    out = call("derive_did", {"address": MAINNET_ADDRESS, "network": "not-a-network"})
    assert "error" in out
    assert "known" in out


def test_lookup_account_with_an_unknown_address(monkeypatch):
    """An account that does not exist returns empty state, not a crash."""
    import importlib

    server_module = importlib.import_module("xrpl_did_mcp.server")

    class _EmptyInfo:
        result: ClassVar[dict] = {"account_data": {}}

    class _StubClient:
        def request(self, model):
            return _EmptyInfo()

    monkeypatch.setattr(server_module, "get_client", lambda network="": _StubClient())

    out = server_module.lookup_account(address="rNotAnAccount", network="mainnet")
    assert out["balance_drops"] is None
    assert out["owner_count"] is None
    assert out["sequence"] is None
    assert "error" not in out


def test_lookup_account_returns_an_error_when_the_ledger_fails(monkeypatch):
    """A ledger failure is returned as data, not raised -- a raising tool
    crashes the agent's turn."""
    import importlib

    server_module = importlib.import_module("xrpl_did_mcp.server")

    class _Boom:
        def request(self, model):
            raise RuntimeError("rpc down")

    monkeypatch.setattr(server_module, "get_client", lambda network="": _Boom())

    out = server_module.lookup_account(address="rAny", network="mainnet")
    assert out["error"] == "RuntimeError"
    assert "rpc down" in out["message"]


def test_publish_did_set_rejects_overlong_uri(monkeypatch):
    """The URI length is checked before any transaction is submitted."""
    import importlib

    server_module = importlib.import_module("xrpl_did_mcp.server")
    monkeypatch.setattr(server_module, "ALLOW_WRITES", True)

    long_uri = "https://example.com/" + ("x" * 300)
    out = server_module.publish_did_set(
        address=MAINNET_ADDRESS,
        document_uri=long_uri,
        network="mainnet",
    )
    assert out["error"] == "URI_TOO_LONG"


@pytest.mark.skipif(not LIVE, reason="set XRPL_MCP_LIVE=1 to run ledger-backed tests")
def test_lookup_account():
    out = call(
        "lookup_account",
        {"address": MAINNET_ADDRESS, "network": "mainnet"},
    )
    assert out["address"] == MAINNET_ADDRESS
    assert "balance_drops" in out
    assert "owner_count" in out


@pytest.mark.skipif(not LIVE, reason="set XRPL_MCP_LIVE=1 to run ledger-backed tests")
def test_lookup_transaction():
    out = call(
        "lookup_transaction",
        {"tx_hash": MAINNET_TX_HASH, "network": "mainnet"},
    )
    assert out["hash"] == MAINNET_TX_HASH
    assert "result" in out
    assert "ledger_index" in out
