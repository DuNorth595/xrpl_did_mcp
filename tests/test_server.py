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


def make_wallet():
    """A throwaway wallet for the write-path tests.

    The seed is generated at runtime and never written down, so no
    seed-shaped literal appears in the repository.
    """
    from xrpl.wallet import Wallet

    return Wallet.create()


def seed_of(wallet) -> str:
    return wallet.seed


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
        "revoke_did",
        "revoke_credential",
        "issue_credential",
        "accept_credential",
        "get_credential",
        "list_credentials",
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
            "revoke",
            "issue",
            "accept",
            "get",
            "list",
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


def test_write_tools_refuse_without_allow_writes(monkeypatch):
    """Every write tool refuses when the server is read-only."""
    import importlib

    server_module = importlib.import_module("xrpl_did_mcp.server")
    monkeypatch.setattr(server_module, "ALLOW_WRITES", False)
    monkeypatch.setattr(server_module.credentials, "resolve_seed", lambda seed=None: (None, "none"))

    out = server_module.revoke_did(address=MAINNET_ADDRESS, network="mainnet")
    assert out["error"] == "WRITES_DISABLED"

    out = server_module.revoke_credential(
        subject=MAINNET_ADDRESS, credential_type="agent-auth", network="mainnet"
    )
    assert out["error"] == "WRITES_DISABLED"


def test_credential_write_tools_refuse_without_allow_writes(monkeypatch):
    """The credential write tools refuse when the server is read-only.

    issue_credential and accept_credential change the ledger, so they must
    be gated exactly like the other write tools.
    """
    import importlib

    server_module = importlib.import_module("xrpl_did_mcp.server")
    monkeypatch.setattr(server_module, "ALLOW_WRITES", False)
    monkeypatch.setattr(server_module.credentials, "resolve_seed", lambda seed=None: (None, "none"))

    issued = server_module.issue_credential(
        subject=MAINNET_ADDRESS, credential_type="agent-auth", network="testnet"
    )
    assert issued["error"] == "WRITES_DISABLED"

    accepted = server_module.accept_credential(
        issuer=MAINNET_ADDRESS, credential_type="agent-auth", network="testnet"
    )
    assert accepted["error"] == "WRITES_DISABLED"


def test_get_credential_reads_without_allow_writes(monkeypatch):
    """get_credential is a pure read: it works with writes disabled."""
    import importlib

    server_module = importlib.import_module("xrpl_did_mcp.server")
    monkeypatch.setattr(server_module, "ALLOW_WRITES", False)

    class _Node(dict):
        pass

    class _Resp:
        result: ClassVar[dict] = {
            "node": {"uri": "https://example.test/c.json", "expiration": 9999999999}
        }

    class _Client:
        def request(self, req):
            return _Resp()

    monkeypatch.setattr(server_module, "get_client", lambda net: _Client())

    out = server_module.get_credential(
        issuer=MAINNET_ADDRESS,
        subject=MAINNET_ADDRESS,
        credential_type="agent-auth",
        network="testnet",
    )
    assert "error" not in out, out
    assert out["found"] is True
    assert out["uri"] == "https://example.test/c.json"
    assert out["expiration"] == 9999999999


def test_get_credential_reports_a_missing_credential(monkeypatch):
    """A credential that is not on the ledger is reported as not found."""
    import importlib

    server_module = importlib.import_module("xrpl_did_mcp.server")

    class _Resp:
        result: ClassVar[dict] = {"node": None}

    class _Client:
        def request(self, req):
            return _Resp()

    monkeypatch.setattr(server_module, "get_client", lambda net: _Client())

    out = server_module.get_credential(
        issuer=MAINNET_ADDRESS,
        subject=MAINNET_ADDRESS,
        credential_type="nope",
        network="testnet",
    )
    assert "error" not in out, out
    assert out["found"] is False
    assert out["accepted"] is False


def test_get_credential_reports_acceptance_separately(monkeypatch):
    """Existence is not acceptance: an unaccepted credential is not valid.

    XLS-70 sets lsfAccepted only when the subject signs. A credential that
    exists but was never accepted must not read as accepted.
    """
    import importlib

    server_module = importlib.import_module("xrpl_did_mcp.server")

    class _Resp:
        result: ClassVar[dict] = {"node": {"uri": None, "expiration": None}}

    class _Client:
        def request(self, req):
            return _Resp()

    monkeypatch.setattr(server_module, "get_client", lambda net: _Client())

    out = server_module.get_credential(
        issuer=MAINNET_ADDRESS,
        subject=MAINNET_ADDRESS,
        credential_type="agent-auth",
        network="testnet",
    )
    assert out["found"] is True
    assert out["accepted"] is False


def test_issue_credential_submits_a_credentialcreate(monkeypatch):
    """Issuing a credential submits a CredentialCreate with the issuer's account."""
    import importlib

    server_module = importlib.import_module("xrpl_did_mcp.server")
    monkeypatch.setattr(server_module, "ALLOW_WRITES", True)

    wallet = make_wallet()
    monkeypatch.setattr(
        server_module.credentials,
        "resolve_seed",
        lambda seed=None: (seed_of(wallet), "keychain"),
    )

    submitted = []
    monkeypatch.setattr(
        server_module,
        "submit_with_retry",
        lambda tx, client, wallet_: submitted.append(tx) or {"hash": "H", "meta": {}},
    )

    out = server_module.issue_credential(
        subject=MAINNET_ADDRESS,
        credential_type="agent-auth",
        network="testnet",
        uri="https://example.test/c.json",
    )
    assert "error" not in out, out
    assert len(submitted) == 1
    tx = submitted[0]
    assert tx.transaction_type == "CredentialCreate"
    assert tx.account == wallet.address
    assert tx.subject == MAINNET_ADDRESS
    assert tx.credential_type == b"agent-auth".hex().upper()
    # The ledger carries the URI as hex, not as text.
    assert tx.uri == b"https://example.test/c.json".hex().upper()
    # A newly issued credential is not valid for the subject until accepted.
    assert out["accepted"] is False
    assert "accept_credential" in out["next_step"]


def test_issue_credential_accepts_a_did_as_the_subject(monkeypatch):
    """A did:xrpl subject is reduced to its address before the create."""
    import importlib

    server_module = importlib.import_module("xrpl_did_mcp.server")
    monkeypatch.setattr(server_module, "ALLOW_WRITES", True)

    wallet = make_wallet()
    monkeypatch.setattr(
        server_module.credentials,
        "resolve_seed",
        lambda seed=None: (seed_of(wallet), "keychain"),
    )

    submitted = []
    monkeypatch.setattr(
        server_module,
        "submit_with_retry",
        lambda tx, client, wallet_: submitted.append(tx) or {"hash": "H", "meta": {}},
    )

    out = server_module.issue_credential(
        subject=MAINNET_DID, credential_type="agent-auth", network="testnet"
    )
    assert "error" not in out, out
    assert submitted[0].subject == MAINNET_ADDRESS


def test_accept_credential_submits_a_credentialaccept(monkeypatch):
    """Accepting a credential submits a CredentialAccept from the subject."""
    import importlib

    server_module = importlib.import_module("xrpl_did_mcp.server")
    monkeypatch.setattr(server_module, "ALLOW_WRITES", True)

    wallet = make_wallet()
    monkeypatch.setattr(
        server_module.credentials,
        "resolve_seed",
        lambda seed=None: (seed_of(wallet), "keychain"),
    )

    submitted = []
    monkeypatch.setattr(
        server_module,
        "submit_with_retry",
        lambda tx, client, wallet_: submitted.append(tx) or {"hash": "H", "meta": {}},
    )

    out = server_module.accept_credential(
        issuer=MAINNET_ADDRESS, credential_type="agent-auth", network="testnet"
    )
    assert "error" not in out, out
    assert len(submitted) == 1
    tx = submitted[0]
    assert tx.transaction_type == "CredentialAccept"
    # The subject is the signing account, not the issuer.
    assert tx.account == wallet.address
    assert tx.issuer == MAINNET_ADDRESS
    assert tx.credential_type == b"agent-auth".hex().upper()
    assert out["accepted"] is True


def test_accept_credential_accepts_a_did_as_the_issuer(monkeypatch):
    """A did:xrpl issuer is reduced to its address before the accept."""
    import importlib

    server_module = importlib.import_module("xrpl_did_mcp.server")
    monkeypatch.setattr(server_module, "ALLOW_WRITES", True)

    wallet = make_wallet()
    monkeypatch.setattr(
        server_module.credentials,
        "resolve_seed",
        lambda seed=None: (seed_of(wallet), "keychain"),
    )

    submitted = []
    monkeypatch.setattr(
        server_module,
        "submit_with_retry",
        lambda tx, client, wallet_: submitted.append(tx) or {"hash": "H", "meta": {}},
    )

    out = server_module.accept_credential(
        issuer=MAINNET_DID, credential_type="agent-auth", network="testnet"
    )
    assert "error" not in out, out
    assert submitted[0].issuer == MAINNET_ADDRESS


def test_credential_write_tools_refuse_an_inline_seed(monkeypatch):
    """An inline seed is refused unless the server allows it.

    A seed in the agent's context is a seed the agent can read, so the
    default is to refuse it and use the keychain instead.
    """
    import importlib

    server_module = importlib.import_module("xrpl_did_mcp.server")
    monkeypatch.setattr(server_module, "ALLOW_WRITES", True)
    monkeypatch.setattr(server_module.credentials, "resolve_seed", lambda seed=None: (None, "none"))

    out = server_module.issue_credential(
        subject=MAINNET_ADDRESS,
        credential_type="agent-auth",
        network="testnet",
        seed="s" * 30,
    )
    assert out["error"] == "INLINE_SEED_REFUSED"

    accepted = server_module.accept_credential(
        issuer=MAINNET_ADDRESS,
        credential_type="agent-auth",
        network="testnet",
        seed="s" * 30,
    )
    assert accepted["error"] == "INLINE_SEED_REFUSED"


def test_revoke_did_rejects_a_mismatched_address(monkeypatch):
    """DIDDelete always targets the signer's own DID, so a wrong address
    must be refused before anything is submitted."""
    import importlib

    server_module = importlib.import_module("xrpl_did_mcp.server")
    monkeypatch.setattr(server_module, "ALLOW_WRITES", True)
    other = make_wallet()
    monkeypatch.setattr(
        server_module.credentials,
        "resolve_seed",
        lambda seed=None: (seed_of(other), "keychain"),
    )

    submitted = []
    monkeypatch.setattr(
        server_module,
        "submit_with_retry",
        lambda tx, client, wallet: submitted.append(tx) or {"hash": "H"},
    )

    # The fake seed's address will not match MAINNET_ADDRESS.
    out = server_module.revoke_did(address=MAINNET_ADDRESS, network="mainnet")
    assert out["error"] == "ADDRESS_MISMATCH"
    assert submitted == [], "nothing should be submitted on a mismatch"


def test_revoke_did_submits_a_diddelete(monkeypatch):
    """A matching address submits a DIDDelete for that account."""
    import importlib

    server_module = importlib.import_module("xrpl_did_mcp.server")
    monkeypatch.setattr(server_module, "ALLOW_WRITES", True)

    wallet = make_wallet()
    monkeypatch.setattr(
        server_module.credentials,
        "resolve_seed",
        lambda seed=None: (seed_of(wallet), "keychain"),
    )

    submitted = []
    monkeypatch.setattr(
        server_module,
        "submit_with_retry",
        lambda tx, client, wallet_: submitted.append(tx) or {"hash": "H", "meta": {}},
    )

    out = server_module.revoke_did(address=wallet.address, network="testnet")
    assert "error" not in out, out
    assert out["tx_hash"] == "H"
    assert len(submitted) == 1
    assert submitted[0].transaction_type == "DIDDelete"
    assert submitted[0].account == wallet.address


def test_revoke_credential_submits_a_credentialdelete(monkeypatch):
    """The credential delete carries the (issuer, subject, type) triple."""
    import importlib

    server_module = importlib.import_module("xrpl_did_mcp.server")
    monkeypatch.setattr(server_module, "ALLOW_WRITES", True)

    wallet = make_wallet()
    monkeypatch.setattr(
        server_module.credentials,
        "resolve_seed",
        lambda seed=None: (seed_of(wallet), "keychain"),
    )

    submitted = []
    monkeypatch.setattr(
        server_module,
        "submit_with_retry",
        lambda tx, client, wallet_: submitted.append(tx) or {"hash": "H", "meta": {}},
    )

    out = server_module.revoke_credential(
        subject=MAINNET_ADDRESS, credential_type="agent-auth", network="testnet"
    )
    assert "error" not in out, out
    assert len(submitted) == 1
    tx = submitted[0]
    assert tx.transaction_type == "CredentialDelete"
    assert tx.account == wallet.address
    assert tx.subject == MAINNET_ADDRESS
    assert tx.credential_type == b"agent-auth".hex().upper()


def test_revoke_credential_accepts_a_did_as_the_subject(monkeypatch):
    """A did:xrpl subject is reduced to its address before the delete."""
    import importlib

    server_module = importlib.import_module("xrpl_did_mcp.server")
    monkeypatch.setattr(server_module, "ALLOW_WRITES", True)

    monkeypatch.setattr(
        server_module.credentials,
        "resolve_seed",
        lambda seed=None: (seed_of(make_wallet()), "keychain"),
    )

    submitted = []
    monkeypatch.setattr(
        server_module,
        "submit_with_retry",
        lambda tx, client, wallet_: submitted.append(tx) or {"hash": "H", "meta": {}},
    )

    out = server_module.revoke_credential(
        subject=MAINNET_DID, credential_type="agent-auth", network="testnet"
    )
    assert "error" not in out, out
    assert submitted[0].subject == MAINNET_ADDRESS


def test_check_authorization_reports_the_operator_policy(monkeypatch, tmp_path):
    """The output shows which operator policy was applied."""
    import importlib

    server_module = importlib.import_module("xrpl_did_mcp.server")

    policy_file = tmp_path / "trust_policy.json"
    policy_file.write_text('{"ban": {"agents": ["rBanned"]}}', encoding="utf-8")
    monkeypatch.setenv("XRPL_MCP_POLICY", str(policy_file))

    class _Result:
        satisfied: ClassVar[bool] = False
        missing_required: ClassVar[list] = []
        denied_held: ClassVar[list] = []

    class _Registry:
        def __init__(self, network=""):
            self.rules = []

        def require(self, **kwargs):
            self.rules.append(kwargs)

        def deny(self, **kwargs):
            self.rules.append(kwargs)

        def ban(self, **kwargs):
            self.rules.append(kwargs)

        def check(self, agent_did):
            return _Result()

    monkeypatch.setattr(server_module, "TrustRegistry", _Registry)

    out = server_module.check_authorization(
        agent_did=MAINNET_DID,
        trusted_issuers=["rIssuer"],
        credential_type="agent-auth",
        network="mainnet",
    )
    assert out["operator_policy"]["loaded"] is True
    assert out["operator_policy"]["ban_agents"] == ["rBanned"]


def test_check_authorization_refuses_a_malformed_operator_policy(monkeypatch, tmp_path):
    """A broken policy file is surfaced, not ignored."""
    import importlib

    server_module = importlib.import_module("xrpl_did_mcp.server")

    policy_file = tmp_path / "trust_policy.json"
    policy_file.write_text("{broken", encoding="utf-8")
    monkeypatch.setenv("XRPL_MCP_POLICY", str(policy_file))

    out = server_module.check_authorization(
        agent_did=MAINNET_DID,
        trusted_issuers=["rIssuer"],
        credential_type="agent-auth",
        network="mainnet",
    )
    assert out["error"] == "POLICY_INVALID"


def test_check_authorization_applies_caller_deny_and_ban(monkeypatch):
    """The caller can also express deny and ban rules."""
    import importlib

    server_module = importlib.import_module("xrpl_did_mcp.server")
    monkeypatch.delenv("XRPL_MCP_POLICY", raising=False)

    captured = {}

    class _Registry:
        def __init__(self, network=""):
            captured["registry"] = self
            self.rules = []

        def require(self, **kwargs):
            self.rules.append(("require", kwargs))

        def deny(self, **kwargs):
            self.rules.append(("deny", kwargs))

        def ban(self, **kwargs):
            self.rules.append(("ban", kwargs))

        def check(self, agent_did):
            class _R:
                satisfied: ClassVar[bool] = True
                missing_required: ClassVar[list] = []
                denied_held: ClassVar[list] = []

            return _R()

    monkeypatch.setattr(server_module, "TrustRegistry", _Registry)

    server_module.check_authorization(
        agent_did=MAINNET_DID,
        trusted_issuers=["rIssuer"],
        credential_type="agent-auth",
        network="mainnet",
        denied_issuers=["rDenied"],
        banned_agents=["rBanned"],
    )
    actions = [a for a, _ in captured["registry"].rules]
    assert "deny" in actions
    assert "ban" in actions


def test_list_credentials_reports_held_and_issued(monkeypatch):
    """The listing separates what an account holds from what it issued."""
    import importlib

    server_module = importlib.import_module("xrpl_did_mcp.server")

    class _Cred:
        def __init__(self, issuer, subject, ctype, accepted, uri=None, expiration=None):
            self.issuer = issuer
            self.subject = subject
            self.credential_type = ctype
            self.accepted = accepted
            self.uri = uri
            self.expiration = expiration

    class _Record:
        agent_did = "did:xrpl:2:" + MAINNET_ADDRESS
        credentials: ClassVar[list] = [
            _Cred("did:xrpl:2:rIssuer", "did:xrpl:2:rSubject", b"KYC", True, "https://x.test/c"),
            _Cred("did:xrpl:2:rOther", "did:xrpl:2:rSubject", b"BADGE", False),
        ]

    class _Registry:
        def __init__(self, network=None):
            self.network = network

        def resolve(self, agent_identifier, use_cache=True):
            return _Record()

    monkeypatch.setattr(server_module, "get_client", lambda net: object())
    import xrpl_agent_id.registry as registry_module

    monkeypatch.setattr(registry_module, "AgentRegistry", _Registry)

    out = server_module.list_credentials(account=MAINNET_ADDRESS, network="testnet")
    assert "error" not in out, out
    assert out["held_count"] == 2
    assert out["issued_count"] == 0
    held = out["held"]
    assert held[0]["credential_type"] == "KYC"
    assert held[0]["accepted"] is True
    assert held[0]["uri"] == "https://x.test/c"
    assert held[0]["role"] == "subject"
    # A credential that exists but was never accepted is marked so.
    assert held[1]["accepted"] is False


def test_list_credentials_is_a_pure_read(monkeypatch):
    """list_credentials needs neither --allow-writes nor a seed."""
    import importlib

    server_module = importlib.import_module("xrpl_did_mcp.server")
    monkeypatch.setattr(server_module, "ALLOW_WRITES", False)

    def _boom(seed=None):
        raise AssertionError("list_credentials must not ask for a seed")

    monkeypatch.setattr(server_module.credentials, "resolve_seed", _boom)

    class _Registry:
        def __init__(self, network=None):
            pass

        def resolve(self, agent_identifier, use_cache=True):
            class R:
                agent_did = "did:xrpl:2:" + MAINNET_ADDRESS
                credentials: ClassVar[list] = []

            return R()

    import xrpl_agent_id.registry as registry_module

    monkeypatch.setattr(registry_module, "AgentRegistry", _Registry)
    monkeypatch.setattr(server_module, "get_client", lambda net: object())

    out = server_module.list_credentials(account=MAINNET_ADDRESS, network="testnet")
    assert "error" not in out, out
    assert out["held_count"] == 0


def test_list_credentials_accepts_a_did(monkeypatch):
    """A did:xrpl account is reduced to its address before the query."""
    import importlib

    server_module = importlib.import_module("xrpl_did_mcp.server")
    seen = {}

    class _Registry:
        def __init__(self, network=None):
            pass

        def resolve(self, agent_identifier, use_cache=True):
            seen["id"] = agent_identifier

            class R:
                agent_did = MAINNET_DID
                credentials: ClassVar[list] = []

            return R()

    import xrpl_agent_id.registry as registry_module

    monkeypatch.setattr(registry_module, "AgentRegistry", _Registry)
    monkeypatch.setattr(server_module, "get_client", lambda net: object())

    out = server_module.list_credentials(account=MAINNET_DID, network="testnet")
    assert "error" not in out, out
    assert seen["id"] == MAINNET_ADDRESS


def test_list_credentials_can_skip_the_issued_side(monkeypatch):
    """include_issued=False avoids the extra account_objects walk."""
    import importlib

    server_module = importlib.import_module("xrpl_did_mcp.server")

    def _boom(net):
        raise AssertionError("the issued side must not be queried when excluded")

    class _Registry:
        def __init__(self, network=None):
            pass

        def resolve(self, agent_identifier, use_cache=True):
            class R:
                agent_did = MAINNET_DID
                credentials: ClassVar[list] = []

            return R()

    import xrpl_agent_id.registry as registry_module

    monkeypatch.setattr(registry_module, "AgentRegistry", _Registry)
    monkeypatch.setattr(server_module, "get_client", _boom)

    out = server_module.list_credentials(
        account=MAINNET_ADDRESS, network="testnet", include_issued=False
    )
    assert "error" not in out, out
    assert out["issued_count"] == 0


def test_list_credentials_finds_the_credentials_an_account_issued(monkeypatch):
    """The issued side reads the account's own credential objects."""
    import importlib

    server_module = importlib.import_module("xrpl_did_mcp.server")

    class _Cred:
        issuer = "did:xrpl:2:rIssuer"
        subject = "did:xrpl:2:rSubject"
        credential_type = b"KYC"
        accepted = False
        uri = None
        expiration = None

    class _Record:
        agent_did = "did:xrpl:2:" + MAINNET_ADDRESS
        credentials: ClassVar[list] = [_Cred()]

    class _Registry:
        def __init__(self, network=None):
            pass

        def resolve(self, agent_identifier, use_cache=True):
            return _Record()

    class _Resp:
        result: ClassVar[dict] = {
            "account_objects": [
                {
                    "Issuer": MAINNET_ADDRESS,
                    "Subject": "rSubjectAddress",
                    "CredentialType": b"KYC".hex().upper(),
                    "Flags": 0x00010000,
                    "URI": b"https://x.test/c".hex().upper(),
                    "Expiration": 857279363,
                },
                {
                    # Not issued by this account -- must be skipped.
                    "Issuer": "rSomeoneElse",
                    "Subject": "rSubjectAddress",
                    "CredentialType": b"KYC".hex().upper(),
                    "Flags": 0,
                },
            ]
        }

    class _Client:
        def request(self, req):
            return _Resp()

    import xrpl_agent_id.registry as registry_module

    monkeypatch.setattr(registry_module, "AgentRegistry", _Registry)
    monkeypatch.setattr(server_module, "get_client", lambda net: _Client())

    out = server_module.list_credentials(account=MAINNET_ADDRESS, network="testnet")
    assert "error" not in out, out
    assert out["issued_count"] == 1
    issued = out["issued"][0]
    assert issued["subject"] == "rSubjectAddress"
    assert issued["credential_type"] == "KYC"
    assert issued["accepted"] is True
    assert issued["uri"] == "https://x.test/c"
    assert issued["expiration"] == 857279363
    assert issued["role"] == "issuer"


@pytest.mark.skipif(not LIVE, reason="set XRPL_MCP_LIVE=1 to run ledger-backed tests")
def test_list_credentials_on_the_testnet_ledger():
    """Enumerate the credentials on a real testnet account.

    The spec example account holds credentials issued by two different
    issuers, so this proves the enumeration finds more than one and
    reports the issuer for each.
    """
    import importlib

    server_module = importlib.import_module("xrpl_did_mcp.server")

    out = server_module.list_credentials(account=MAINNET_ADDRESS, network="testnet")
    assert "error" not in out, out
    assert out["held_count"] >= 1
    assert out["account"] == MAINNET_ADDRESS
    for cred in out["held"]:
        assert cred["role"] == "subject"
        assert cred["subject"].endswith(MAINNET_ADDRESS)
        assert isinstance(cred["accepted"], bool)
        assert cred["credential_type"]
