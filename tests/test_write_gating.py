"""The operator's policy gates the write tools.

The gap this closes: the operator's require/deny/ban rules were only
consulted when a caller explicitly asked for a check. The write tools never
called them, so a policy that said "only agents holding this credential may
act" was not enforced against a write. A caller could read the policy, see
that it would refuse, and then write anyway.

These tests assert the opposite: a write is refused when the operator's
policy says it should be, and the refusal happens BEFORE the seed is
touched and before anything is submitted.
"""

from __future__ import annotations

import json

from xrpl_did_mcp import policy


class _Result:
    """A stand-in for the trust library's check result."""

    def __init__(self, satisfied=True, banned=False, missing=None, denied=None):
        self.satisfied = satisfied
        self.banned = banned
        self.missing_required = missing or []
        self.denied_held = denied or []
        self.ban_reason = "the agent is banned"


class _Registry:
    """A registry that answers as told, without touching a ledger."""

    def __init__(self, result=None, raises=None):
        self._result = result
        self._raises = raises
        self.rules = []

    def require(self, **kw):
        self.rules.append(("require", kw))

    def deny(self, **kw):
        self.rules.append(("deny", kw))

    def ban(self, **kw):
        self.rules.append(("ban", kw))

    def check(self, agent_did):
        if self._raises is not None:
            raise self._raises
        return self._result


def _write_policy(tmp_path, body):
    path = tmp_path / "trust_policy.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


# ------------------------------------------------------- no policy = allow


def test_no_policy_file_allows_the_write(tmp_path, monkeypatch):
    """An operator with no policy file keeps working exactly as before."""
    monkeypatch.setenv("XRPL_MCP_POLICY", str(tmp_path / "absent.json"))
    out = policy.authorize_write("did:xrpl:1:rTest")
    assert out["allowed"] is True


def test_an_empty_policy_file_allows_the_write(tmp_path, monkeypatch):
    """An empty policy is an absence, not a refusal."""
    path = _write_policy(tmp_path, {})
    monkeypatch.setenv("XRPL_MCP_POLICY", str(path))
    out = policy.authorize_write("did:xrpl:1:rTest")
    assert out["allowed"] is True


# -------------------------------------------------------------- refusals


def test_a_banned_agent_is_refused(tmp_path, monkeypatch):
    path = _write_policy(tmp_path, {"ban": {"agents": ["did:xrpl:1:rBanned"]}})
    monkeypatch.setenv("XRPL_MCP_POLICY", str(path))

    def factory(net):
        return _Registry(_Result(satisfied=False, banned=True))

    out = policy.authorize_write("did:xrpl:1:rBanned", registry_factory=factory)
    assert out["allowed"] is False
    assert out["error"] == "AGENT_BANNED"


def test_an_agent_missing_the_required_credential_is_refused(tmp_path, monkeypatch):
    path = _write_policy(
        tmp_path,
        {"require": {"issuers": ["rTrustedIssuer"], "credential_type": "agent-authorization"}},
    )
    monkeypatch.setenv("XRPL_MCP_POLICY", str(path))

    def factory(net):
        return _Registry(
            _Result(satisfied=False, missing=[("rTrustedIssuer", b"agent-authorization")])
        )

    out = policy.authorize_write("did:xrpl:1:rTest", registry_factory=factory)
    assert out["allowed"] is False
    assert out["error"] == "NOT_AUTHORIZED"
    assert out["missing_required"] == [
        {"issuer": "rTrustedIssuer", "credential_type": "agent-authorization"}
    ]


def test_an_agent_holding_a_denied_credential_is_refused(tmp_path, monkeypatch):
    path = _write_policy(
        tmp_path,
        {"deny": {"issuers": ["rRevokedIssuer"], "credential_type": "agent-authorization"}},
    )
    monkeypatch.setenv("XRPL_MCP_POLICY", str(path))

    def factory(net):
        return _Registry(
            _Result(satisfied=False, denied=[("rRevokedIssuer", b"agent-authorization")])
        )

    out = policy.authorize_write("did:xrpl:1:rTest", registry_factory=factory)
    assert out["allowed"] is False
    assert out["error"] == "NOT_AUTHORIZED"
    assert out["denied_held"] == [
        {"issuer": "rRevokedIssuer", "credential_type": "agent-authorization"}
    ]


# ------------------------------------------------------------ fails closed


def test_an_unreadable_ledger_refuses_the_write(tmp_path, monkeypatch):
    """A policy that cannot be evaluated must not become an allow.

    Otherwise taking the ledger down is a way to switch the policy off.
    """
    path = _write_policy(tmp_path, {"ban": {"agents": ["rBanned"]}})
    monkeypatch.setenv("XRPL_MCP_POLICY", str(path))

    def factory(net):
        return _Registry(raises=RuntimeError("the ledger is unreachable"))

    out = policy.authorize_write("did:xrpl:1:rTest", registry_factory=factory)
    assert out["allowed"] is False
    assert out["error"] == "LEDGER_UNAVAILABLE"


def test_a_malformed_policy_file_refuses_the_write(tmp_path, monkeypatch):
    """A malformed file is an error, not an absence.

    Silently dropping a malformed deny list would quietly remove the one
    property the file exists to provide.
    """
    path = tmp_path / "trust_policy.json"
    path.write_text("{ this is not json", encoding="utf-8")
    monkeypatch.setenv("XRPL_MCP_POLICY", str(path))

    out = policy.authorize_write("did:xrpl:1:rTest")
    assert out["allowed"] is False
    assert out["error"] == "POLICY_INVALID"


# -------------------------------------------- the operator's rules are used


def test_the_operator_rules_are_applied_to_the_registry(tmp_path, monkeypatch):
    """The operator's require/deny/ban reach the registry.

    The caller's own rules are NOT the only ones that count.
    """
    path = _write_policy(
        tmp_path,
        {
            "require": {"issuers": ["rTrustedIssuer"], "credential_type": "agent-authorization"},
            "deny": {"issuers": ["rRevokedIssuer"], "credential_type": "agent-authorization"},
            "ban": {"agents": ["rBanned"]},
        },
    )
    monkeypatch.setenv("XRPL_MCP_POLICY", str(path))

    seen = {}

    def factory(net):
        reg = _Registry(_Result(satisfied=True))
        seen["reg"] = reg
        return reg

    out = policy.authorize_write("did:xrpl:1:rTest", registry_factory=factory)
    assert out["allowed"] is True

    actions = [a for a, _ in seen["reg"].rules]
    assert "ban" in actions
    assert "deny" in actions
    assert "require" in actions


def test_a_caller_cannot_widen_the_operator_rules(tmp_path, monkeypatch):
    """The operator's rules are applied ON TOP of the caller's.

    This is the whole point: a caller that passes a permissive policy
    cannot remove the operator's deny or ban.
    """
    path = _write_policy(tmp_path, {"ban": {"agents": ["rBanned"]}})
    monkeypatch.setenv("XRPL_MCP_POLICY", str(path))

    seen = {}

    def factory(net):
        reg = _Registry(_Result(satisfied=True))
        seen["reg"] = reg
        return reg

    policy.authorize_write("did:xrpl:1:rTest", registry_factory=factory)

    bans = [kw for action, kw in seen["reg"].rules if action == "ban"]
    assert bans, "the operator's ban never reached the registry"
    assert "rBanned" in bans[0]["subject"]
