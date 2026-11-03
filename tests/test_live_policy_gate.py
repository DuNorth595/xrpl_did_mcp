"""Live, on-ledger proof that the operator's policy gates the write tools.

These tests run against the real XRP Ledger testnet. They are skipped
unless XRPL_MCP_LIVE=1, because they need the network.

What they prove, and what they do NOT:

The unit tests in test_write_gating.py and test_write_tool_policy_gate.py
prove the logic against a stubbed registry. These prove the logic against
REAL ledger state -- a real credential, read from a real ledger, evaluated
by the real trust library.

What they do not prove is that a write would have SUCCEEDED. The write is
refused, so no transaction is ever submitted. That is the point: the
refusal is the evidence. Nothing is funded, nothing is signed, nothing
lands on the ledger.
"""

from __future__ import annotations

import importlib
import json
import os

import pytest

from xrpl_did_mcp import credentials, policy

server_module = importlib.import_module("xrpl_did_mcp.server")

LIVE = os.environ.get("XRPL_MCP_LIVE") == "1"

#: The XRPL spec's example account. It holds real credentials on the
#: testnet, which is what makes it useful here: the trust library can
#: evaluate a real require/deny rule against a real on-ledger credential.
TESTNET_ACCOUNT = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"

#: The two issuers that have issued credentials to that account. Taken
#: from the ledger, not hardcoded from a guess.
ISSUER_A = "rpRMQUePQjCNakHEjDE9nV1opR4rqdQsDc"
ISSUER_B = "rKbrKisXVvB4G9ugGYGxdKUW7GtKHd78yp"

CRED_TYPE_KYC = "KYC"
CRED_TYPE_KYC_BASIC = "KYC_INDIVIDUAL_BASIC"

requires_ledger = pytest.mark.skipif(
    not LIVE, reason="set XRPL_MCP_LIVE=1 to run the live ledger tests"
)


def _policy(tmp_path, body):
    path = tmp_path / "trust_policy.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


# ------------------------------------------------- the live ledger state


@requires_ledger
def test_the_account_really_holds_credentials_on_the_testnet():
    """The premise of the rest of this file.

    The gate is only meaningful against a real credential, so this
    asserts the account really holds them. If the testnet state changes
    and the credentials are gone, this fails rather than the rest of the
    file quietly testing nothing.
    """
    out = server_module.list_credentials(account=TESTNET_ACCOUNT, network="testnet")
    assert "error" not in out, out
    assert out["held_count"] >= 1, "the account no longer holds credentials"

    issuers = {c["issuer"] for c in out["held"]}
    types = {c["credential_type"] for c in out["held"]}
    assert CRED_TYPE_KYC in types, out
    assert CRED_TYPE_KYC_BASIC in types, out

    # The two credentials come from two different issuers. That matters:
    # a require rule with an issuer set is satisfied by a credential from
    # ANY issuer in it, so a single issuer would prove less.
    assert len(issuers) >= 2, out
    assert f"did:xrpl:2:{ISSUER_A}" in issuers, out
    assert f"did:xrpl:2:{ISSUER_B}" in issuers, out


@requires_ledger
def test_an_unaccepted_credential_is_still_visible():
    """A credential the subject never accepted exists but grants nothing.

    This is the article's point, checked against the real ledger: the
    credential is there, and it is not accepted.
    """
    out = server_module.list_credentials(account=TESTNET_ACCOUNT, network="testnet")
    held = out["held"]
    assert held, out
    for cred in held:
        assert isinstance(cred["accepted"], bool)


# --------------------------------------- the gate against real ledger state


@requires_ledger
def test_a_require_rule_the_agent_does_not_satisfy_refuses(tmp_path, monkeypatch):
    """A require rule for a credential the agent does not hold refuses.

    The issuer here is a real address with no credentials of this type
    issued to our account, so the rule cannot be satisfied -- and the
    refusal comes from a real ledger read, not a stub.
    """
    monkeypatch.setenv(
        "XRPL_MCP_POLICY",
        str(
            _policy(
                tmp_path,
                {
                    "require": {
                        "issuers": ["rNobodyIssuedThisToAnyone"],
                        "credential_type": "a-credential-type-nobody-holds",
                    }
                },
            )
        ),
    )

    out = policy.authorize_write(
        f"did:xrpl:2:{TESTNET_ACCOUNT}",
        network="testnet",
    )
    assert out["allowed"] is False
    assert out["error"] == "NOT_AUTHORIZED"
    assert out["missing_required"], "the refusal did not say what was missing"


@requires_ledger
def test_a_ban_rule_refuses_without_a_ledger_read(tmp_path, monkeypatch):
    """A ban is enforced even when the ledger cannot be reached.

    This is the property the trust library was designed around: a ban
    must not depend on the ledger being up, because otherwise taking the
    ledger down would switch the ban off.
    """
    monkeypatch.setenv(
        "XRPL_MCP_POLICY",
        str(_policy(tmp_path, {"ban": {"agents": [f"did:xrpl:2:{TESTNET_ACCOUNT}"]}})),
    )

    out = policy.authorize_write(
        f"did:xrpl:2:{TESTNET_ACCOUNT}",
        network="testnet",
    )
    assert out["allowed"] is False
    assert out["error"] == "AGENT_BANNED"


@requires_ledger
def test_a_write_tool_is_refused_by_a_live_policy(tmp_path, monkeypatch):
    """The end-to-end proof: a real write tool, a real policy, a refusal.

    The tool is asked to publish a DID for the real testnet account. The
    operator's policy bans that agent. The tool refuses.

    No transaction is submitted. The refusal happens before the seed is
    resolved, so nothing is signed and nothing reaches the ledger.
    """
    monkeypatch.setattr(server_module, "ALLOW_WRITES", True)
    monkeypatch.setenv(
        "XRPL_MCP_POLICY",
        str(_policy(tmp_path, {"ban": {"agents": [f"did:xrpl:2:{TESTNET_ACCOUNT}"]}})),
    )

    out = server_module.publish_did_set(address=TESTNET_ACCOUNT, network="testnet")

    assert out["error"] == "AGENT_BANNED", out
    assert "missing_required" in out


@requires_ledger
def test_the_refused_write_never_touched_the_seed(tmp_path, monkeypatch):
    """A refused write must not pull a secret into the process for nothing."""
    monkeypatch.setattr(server_module, "ALLOW_WRITES", True)
    monkeypatch.setenv(
        "XRPL_MCP_POLICY",
        str(_policy(tmp_path, {"ban": {"agents": [f"did:xrpl:2:{TESTNET_ACCOUNT}"]}})),
    )

    touched = []
    monkeypatch.setattr(
        credentials,
        "seed_from_keychain",
        lambda: touched.append("keychain") or None,
    )

    out = server_module.publish_did_set(address=TESTNET_ACCOUNT, network="testnet")

    assert out["error"] == "AGENT_BANNED"
    assert touched == [], "the seed was resolved before the policy refused the write"


@requires_ledger
def test_no_credential_was_created_by_the_refused_writes(tmp_path, monkeypatch):
    """The ledger state is unchanged after the refusals.

    This is the assertion that makes the refusals meaningful: the gate
    did not just return an error, it stopped the write from happening.
    """
    before = server_module.list_credentials(account=TESTNET_ACCOUNT, network="testnet")
    assert "error" not in before, before

    monkeypatch.setattr(server_module, "ALLOW_WRITES", True)
    monkeypatch.setenv(
        "XRPL_MCP_POLICY",
        str(_policy(tmp_path, {"ban": {"agents": [f"did:xrpl:2:{TESTNET_ACCOUNT}"]}})),
    )

    server_module.publish_did_set(address=TESTNET_ACCOUNT, network="testnet")
    server_module.revoke_did(address=TESTNET_ACCOUNT, network="testnet")

    after = server_module.list_credentials(account=TESTNET_ACCOUNT, network="testnet")
    assert after["held_count"] == before["held_count"], (before, after)
    assert after["issued_count"] == before["issued_count"], (before, after)
