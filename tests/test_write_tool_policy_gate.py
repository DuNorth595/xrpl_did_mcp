"""The write tools refuse when the operator's policy says so.

These are the end-to-end tests. The unit tests in test_write_gating.py
prove the policy logic; these prove the LOGIC IS WIRED TO THE TOOLS, which
is the gap that was closed. A policy check that nothing calls is a policy
check that verifies nothing.

The refusal has to happen before the seed is resolved and before anything
is submitted, so a refused write costs nothing and leaks nothing.
"""

from __future__ import annotations

import asyncio
import importlib
import json

from xrpl_did_mcp import credentials
from xrpl_did_mcp import policy as policy_module

# `xrpl_did_mcp.server` the attribute is the MCPServer *object*; the
# module has to be imported explicitly.
server_module = importlib.import_module("xrpl_did_mcp.server")


def _call(tool: str, args: dict) -> dict:
    async def _run():
        result = await server_module.server.call_tool(tool, args)
        return result.structured_content

    return asyncio.run(_run())


def _policy(tmp_path, body):
    path = tmp_path / "trust_policy.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _refusing_registry(monkeypatch):
    """Make every trust check refuse, without touching a ledger."""
    from xrpl_did_mcp import policy

    class _Result:
        def __init__(self):
            self.satisfied = False
            self.banned = False
            self.missing_required = [("rTrustedIssuer", b"agent-authorization")]
            self.denied_held = []
            self.ban_reason = None

    class _Registry:
        def require(self, **kw):
            pass

        def deny(self, **kw):
            pass

        def ban(self, **kw):
            pass

        def check(self, agent_did):
            return _Result()

    monkeypatch.setattr(
        policy,
        "authorize_write",
        lambda agent_did, **kw: {
            "allowed": False,
            "error": "NOT_AUTHORIZED",
            "message": "the agent does not satisfy the operator's trust policy",
            "missing_required": [
                {"issuer": "rTrustedIssuer", "credential_type": "agent-authorization"}
            ],
            "denied_held": [],
        },
    )


def _counting_registry(monkeypatch):
    """Record every authorize_write call, and allow."""
    from xrpl_did_mcp import policy

    calls = []

    def fake(agent_did, **kw):
        calls.append(agent_did)
        return {"allowed": True}

    monkeypatch.setattr(policy, "authorize_write", fake)
    return calls


# ------------------------------------------------------- the refusal


def test_publish_did_set_is_refused_by_the_policy(monkeypatch, tmp_path):
    monkeypatch.setattr(server_module, "ALLOW_WRITES", True)
    monkeypatch.setenv("XRPL_MCP_POLICY", str(_policy(tmp_path, {"ban": {"agents": ["rX"]}})))
    _refusing_registry(monkeypatch)

    out = _call("publish_did_set", {"address": "rTest", "network": "testnet"})
    assert out["error"] == "NOT_AUTHORIZED"
    assert out["missing_required"] == [
        {"issuer": "rTrustedIssuer", "credential_type": "agent-authorization"}
    ]


def test_revoke_did_is_refused_by_the_policy(monkeypatch, tmp_path):
    monkeypatch.setattr(server_module, "ALLOW_WRITES", True)
    monkeypatch.setenv("XRPL_MCP_POLICY", str(_policy(tmp_path, {"ban": {"agents": ["rX"]}})))
    _refusing_registry(monkeypatch)

    out = _call("revoke_did", {"address": "rTest", "network": "testnet"})
    assert out["error"] == "NOT_AUTHORIZED"


def test_issue_credential_is_refused_by_the_policy(monkeypatch, tmp_path):
    monkeypatch.setattr(server_module, "ALLOW_WRITES", True)
    monkeypatch.setenv("XRPL_MCP_POLICY", str(_policy(tmp_path, {"ban": {"agents": ["rX"]}})))
    _refusing_registry(monkeypatch)

    out = _call(
        "issue_credential",
        {"subject": "rTest", "credential_type": "agent-authorization", "network": "testnet"},
    )
    assert out["error"] == "NOT_AUTHORIZED"


def test_revoke_credential_is_refused_by_the_policy(monkeypatch, tmp_path):
    monkeypatch.setattr(server_module, "ALLOW_WRITES", True)
    monkeypatch.setenv("XRPL_MCP_POLICY", str(_policy(tmp_path, {"ban": {"agents": ["rX"]}})))
    _refusing_registry(monkeypatch)

    out = _call(
        "revoke_credential",
        {"subject": "rTest", "credential_type": "agent-authorization", "network": "testnet"},
    )
    assert out["error"] == "NOT_AUTHORIZED"


def test_accept_credential_is_refused_by_the_policy(monkeypatch, tmp_path):
    monkeypatch.setattr(server_module, "ALLOW_WRITES", True)
    monkeypatch.setattr(credentials, "ALLOW_INLINE_SEED", True)
    monkeypatch.setattr(credentials, "seed_from_keychain", lambda: None)
    monkeypatch.setenv("XRPL_MCP_POLICY", str(_policy(tmp_path, {"ban": {"agents": ["rX"]}})))
    _refusing_registry(monkeypatch)

    out = _call(
        "accept_credential",
        {
            "issuer": "rIssuer",
            "credential_type": "agent-authorization",
            "network": "testnet",
            # A freshly generated, unfunded throwaway testnet account. The
            # seed has to be valid, because for this tool the agent being
            # checked is the SIGNING ACCOUNT -- which only exists once the
            # wallet is derived from the seed.
            "seed": "sEdTZBaLLMYBFbNioFEhKpeQXzt7nMC",
        },
    )
    assert out["error"] == "NOT_AUTHORIZED"


# ------------------------------------------ the gate runs BEFORE the seed


def test_the_refusal_happens_before_the_seed_is_resolved(monkeypatch, tmp_path):
    """A refused write must not touch the keychain.

    If the check ran after the seed was resolved, a refused write would
    still have pulled a secret into the process for nothing.
    """
    monkeypatch.setattr(server_module, "ALLOW_WRITES", True)
    monkeypatch.setenv("XRPL_MCP_POLICY", str(_policy(tmp_path, {"ban": {"agents": ["rX"]}})))
    _refusing_registry(monkeypatch)

    touched = []

    def _boom():
        touched.append("keychain")
        return None

    monkeypatch.setattr(credentials, "seed_from_keychain", _boom)

    out = _call("publish_did_set", {"address": "rTest", "network": "testnet"})
    assert out["error"] == "NOT_AUTHORIZED"
    assert touched == [], "the seed was resolved before the policy refused the write"


def test_the_write_flag_is_still_checked_first(monkeypatch, tmp_path):
    """--allow-writes is still the first gate.

    The policy check is a second line of defence, not a replacement. A
    server started without --allow-writes refuses for that reason, so the
    caller learns the real fix.
    """
    monkeypatch.setattr(server_module, "ALLOW_WRITES", False)
    monkeypatch.setenv("XRPL_MCP_POLICY", str(_policy(tmp_path, {"ban": {"agents": ["rX"]}})))
    _refusing_registry(monkeypatch)

    out = _call("publish_did_set", {"address": "rTest", "network": "testnet"})
    assert out["error"] == "WRITES_DISABLED"


# ------------------------------------------------- the gate is actually called


def test_the_policy_is_consulted_on_a_write(monkeypatch, tmp_path):
    """The write tool asks the policy. Without this, the rest is decoration."""
    monkeypatch.setattr(server_module, "ALLOW_WRITES", True)
    monkeypatch.setenv("XRPL_MCP_POLICY", str(_policy(tmp_path, {"ban": {"agents": ["rX"]}})))
    calls = _counting_registry(monkeypatch)

    # The seed is refused, so the write stops there -- but the policy was
    # consulted FIRST, which is the point.
    monkeypatch.setattr(credentials, "ALLOW_INLINE_SEED", False)
    monkeypatch.setattr(credentials, "seed_from_keychain", lambda: None)

    _call("publish_did_set", {"address": "rTest", "network": "testnet"})
    assert calls, "the write tool never asked the policy"


def test_a_write_with_no_policy_is_unaffected(monkeypatch, tmp_path):
    """A server with no policy file behaves exactly as it did before.

    The gate must not change the behaviour of an operator who has not
    expressed an opinion.
    """
    monkeypatch.setattr(server_module, "ALLOW_WRITES", True)
    monkeypatch.setenv("XRPL_MCP_POLICY", str(tmp_path / "absent.json"))
    calls = _counting_registry(monkeypatch)

    monkeypatch.setattr(credentials, "ALLOW_INLINE_SEED", False)
    monkeypatch.setattr(credentials, "seed_from_keychain", lambda: None)

    out = _call("publish_did_set", {"address": "rTest", "network": "testnet"})
    # It gets past the policy and fails on the seed, which is where it
    # failed before the gate existed.
    assert out["error"] in {"NO_SEED", "INLINE_SEED_REFUSED"}
    assert calls, "the policy was never consulted, not even to allow"


def test_the_did_uses_the_network_the_write_targets():
    """A DID carries its network id, so it has to be derived for the
    network the write is about to touch.

    Deriving it for the default network instead would check the agent
    against the wrong ledger -- a check that looks real and is not.
    """
    srv = server_module

    # A real classic address: "rTest" is not one, and a bad address
    # is returned unchanged, which would make the two network ids
    # identical and the test prove nothing.
    addr = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"
    testnet = srv._did_for(addr, "testnet")
    mainnet = srv._did_for(addr, "mainnet")
    assert testnet != mainnet
    assert testnet.startswith("did:xrpl:")
    assert mainnet.startswith("did:xrpl:")


def test_the_write_tool_derives_the_did_for_its_own_network(monkeypatch, tmp_path):
    """The network the tool writes to is the network the DID is derived for."""
    srv = server_module

    monkeypatch.setattr(server_module, "ALLOW_WRITES", True)
    monkeypatch.setenv("XRPL_MCP_POLICY", str(_policy(tmp_path, {"ban": {"agents": ["rX"]}})))

    seen = {}

    def fake(agent_did, **kw):
        seen["did"] = agent_did
        seen["network"] = kw.get("network")
        return {"allowed": True}

    monkeypatch.setattr(policy_module, "authorize_write", fake)
    monkeypatch.setattr(credentials, "ALLOW_INLINE_SEED", False)
    monkeypatch.setattr(credentials, "seed_from_keychain", lambda: None)

    _call(
        "publish_did_set",
        {"address": "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh", "network": "mainnet"},
    )

    assert seen["network"] == "mainnet"
    # The DID's network id must match the network being written to.
    assert seen["did"] == srv._did_for("rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh", "mainnet")
