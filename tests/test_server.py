"""Tests for the xrpl-did MCP server.

These are offline: they exercise the tool surface and the pure-derivation
paths. Anything that touches the ledger is skipped unless XRPL_MCP_LIVE=1.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from xrpl_did_mcp.server import server

LIVE = os.environ.get("XRPL_MCP_LIVE") == "1"

MAINNET_ADDRESS = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"
MAINNET_DID = "did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"


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
        "ledger_lookup",
    }


def test_every_tool_has_a_description():
    tools = asyncio.run(server.list_tools())
    for tool in tools:
        assert tool.description, f"{tool.name} has no description"


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


def test_ledger_lookup_unknown_kind():
    out = call("ledger_lookup", {"target": MAINNET_ADDRESS, "kind": "nope", "network": "mainnet"})
    assert out["error"] == "UNKNOWN_KIND"


def test_publish_did_set_rejects_overlong_uri():
    long_uri = "https://example.com/" + ("x" * 300)
    out = call(
        "publish_did_set",
        {
            "address": MAINNET_ADDRESS,
            "seed": "snotaseed",
            "document_uri": long_uri,
            "network": "mainnet",
        },
    )
    assert out["error"] == "URI_TOO_LONG"


def test_publish_did_set_rejects_bad_seed():
    out = call(
        "publish_did_set",
        {"address": MAINNET_ADDRESS, "seed": "not-a-real-seed", "network": "mainnet"},
    )
    assert out["error"] == "INVALID_SEED"


@pytest.mark.skipif(not LIVE, reason="set XRPL_MCP_LIVE=1 to run ledger-backed tests")
def test_ledger_lookup_account():
    out = call(
        "ledger_lookup",
        {"target": MAINNET_ADDRESS, "kind": "account", "network": "mainnet"},
    )
    assert out["kind"] == "account"
    assert out["address"] == MAINNET_ADDRESS
    assert "balance_drops" in out
