# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Tests for the operator trust policy.

The operator policy is what stops a caller from choosing its own
permissive trust rules. These tests cover loading it, applying it, and
the failure modes that matter: a missing file is fine, a malformed one is
not.
"""

from __future__ import annotations

import json

import pytest

from xrpl_did_mcp import policy


def write_policy(tmp_path, payload) -> str:
    path = tmp_path / "trust_policy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_missing_policy_file_is_not_an_error(tmp_path):
    """No operator policy means no operator rules -- not an allow-all."""
    p = policy.load_policy(tmp_path / "absent.json")
    assert p.loaded is False
    assert p.has_rules is False
    assert p.ban_agents == set()


def test_empty_policy_file_loads_with_no_rules(tmp_path):
    path = write_policy(tmp_path, {})
    p = policy.load_policy(tmp_path / path)
    # The path argument wins over the written path; load the real one.
    p = policy.load_policy(tmp_path / "trust_policy.json")
    assert p.loaded is True
    assert p.has_rules is False


def test_policy_is_parsed(tmp_path):
    path = write_policy(
        tmp_path,
        {
            "require": {
                "issuers": ["rIssuerA", "rIssuerB"],
                "credential_type": "agent-authorization",
            },
            "deny": {"issuers": ["rBadIssuer"], "credential_type": "agent-authorization"},
            "ban": {"agents": ["did:xrpl:1:rBanned", "rBannedPlain"]},
        },
    )
    p = policy.load_policy(tmp_path / path)
    assert p.loaded is True
    assert p.has_rules is True
    assert p.require_issuers == {"rIssuerA", "rIssuerB"}
    assert p.require_credential_type == "agent-authorization"
    assert p.deny_issuers == {"rBadIssuer"}
    assert p.ban_agents == {"did:xrpl:1:rBanned", "rBannedPlain"}


def test_malformed_policy_is_an_error(tmp_path):
    """A policy that exists but cannot be parsed must not be silently ignored.

    Dropping the operator's deny rules because of a typo would quietly
    remove the safety property the file exists to provide.
    """
    path = tmp_path / "trust_policy.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        policy.load_policy(path)


def test_non_object_policy_is_an_error(tmp_path):
    path = tmp_path / "trust_policy.json"
    path.write_text('["a", "b"]', encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        policy.load_policy(path)


def test_malformed_section_is_an_error(tmp_path):
    path = tmp_path / "trust_policy.json"
    path.write_text('{"require": "not-an-object"}', encoding="utf-8")
    with pytest.raises(ValueError, match="must each be objects"):
        policy.load_policy(path)


def test_apply_policy_adds_ban_deny_and_require():
    """The operator's rules reach the registry."""
    from xrpl_agent_id import TrustRegistry

    registry = TrustRegistry(network="testnet")
    p = policy.OperatorPolicy(
        require_issuers={"rIssuer"},
        require_credential_type="agent-authorization",
        deny_issuers={"rBad"},
        deny_credential_type="agent-authorization",
        ban_agents={"rBanned"},
        loaded=True,
    )
    policy.apply_policy(registry, p)

    actions = [rule.action for rule in registry._rules]
    assert "ban" in actions
    assert "deny" in actions
    # The registry started empty; the operator contributed exactly one require.
    assert actions.count("require") == 1


def test_empty_policy_adds_nothing():
    """A policy with no rules must not add empty rules that match everything."""
    from xrpl_agent_id import TrustRegistry

    registry = TrustRegistry(network="testnet")
    before = len(registry._rules)
    policy.apply_policy(registry, policy.OperatorPolicy())
    assert len(registry._rules) == before


def test_policy_path_reads_the_environment_variable(monkeypatch, tmp_path):
    target = tmp_path / "custom.json"
    monkeypatch.setenv(policy.POLICY_ENV, str(target))
    assert policy.policy_path() == target


def test_policy_path_defaults_to_cwd(monkeypatch):
    monkeypatch.delenv(policy.POLICY_ENV, raising=False)
    assert policy.policy_path().name == policy.DEFAULT_POLICY_FILE


def test_summary_is_json_safe(tmp_path):
    path = write_policy(tmp_path, {"ban": {"agents": ["rBanned"]}})
    p = policy.load_policy(tmp_path / path)
    summary = p.summary()
    # Must survive a JSON round trip -- it goes into tool output.
    assert json.loads(json.dumps(summary)) == summary
    assert summary["ban_agents"] == ["rBanned"]
