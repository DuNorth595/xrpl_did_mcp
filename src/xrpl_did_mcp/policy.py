# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Operator-side trust policy for the did:xrpl MCP server.

The caller of a tool is not necessarily the operator of the server. An agent
that can choose its own trust policy can always choose a permissive one, so
the server keeps its own policy on disk and applies it on top of whatever
the caller passes.

The policy file is JSON:

    {
      "require": {
        "issuers": ["rIssuer..."],
        "credential_type": "agent-authorization"
      },
      "deny": {
        "issuers": ["rRevokedIssuer..."],
        "credential_type": "agent-authorization"
      },
      "ban": {
        "agents": ["did:xrpl:1:rBanned...", "rBanned..."]
      }
    }

Every section is optional. An empty or missing file means "the operator has
not expressed an opinion", which is not the same as "allow everything" --
it means no operator rules are added to the caller's.

The file path comes from XRPL_MCP_POLICY, defaulting to trust_policy.json
next to the server's working directory. If the file does not exist, the
server runs with caller-supplied policy only.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Environment variable that points at the operator policy file.
POLICY_ENV = "XRPL_MCP_POLICY"

#: Default file name, looked for in the current working directory.
DEFAULT_POLICY_FILE = "trust_policy.json"


def policy_path() -> Path:
    """Return the path of the operator policy file."""
    configured = os.environ.get(POLICY_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.cwd() / DEFAULT_POLICY_FILE


@dataclass
class OperatorPolicy:
    """The operator's trust rules, loaded from disk.

    Attributes:
        require_issuers: Issuers whose credential must be held. Empty means
            the operator does not require anything.
        require_credential_type: The credential type the operator requires.
        deny_issuers: Issuers whose credential causes a denial.
        deny_credential_type: The credential type that causes a denial.
        ban_agents: Agents denied outright, whatever they hold.
        source: Where the policy was loaded from, for reporting.
        loaded: Whether a policy file was actually found and parsed.
    """

    require_issuers: set[str] = field(default_factory=set)
    require_credential_type: str = ""
    deny_issuers: set[str] = field(default_factory=set)
    deny_credential_type: str = ""
    ban_agents: set[str] = field(default_factory=set)
    source: str = ""
    loaded: bool = False

    @property
    def has_rules(self) -> bool:
        """Whether this policy contributes anything to a decision."""
        return bool(
            self.require_issuers
            or self.require_credential_type
            or self.deny_issuers
            or self.deny_credential_type
            or self.ban_agents
        )

    def summary(self) -> dict[str, Any]:
        """A JSON-safe view of the policy, for inclusion in tool output."""
        return {
            "loaded": self.loaded,
            "source": self.source,
            "require_issuers": sorted(self.require_issuers),
            "require_credential_type": self.require_credential_type,
            "deny_issuers": sorted(self.deny_issuers),
            "deny_credential_type": self.deny_credential_type,
            "ban_agents": sorted(self.ban_agents),
        }


def load_policy(path: Path | None = None) -> OperatorPolicy:
    """Load the operator policy from disk.

    A missing file is not an error -- it means the operator has not written
    one, and the server applies caller-supplied rules only. A file that
    exists but cannot be parsed IS an error worth surfacing, because
    silently ignoring a malformed policy would quietly drop the operator's
    deny rules.
    """
    target = path or policy_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return OperatorPolicy(source=str(target), loaded=False)
    except OSError as exc:
        raise ValueError(f"cannot read operator policy {target}: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"operator policy {target} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"operator policy {target} must be a JSON object")

    require = data.get("require") or {}
    deny = data.get("deny") or {}
    ban = data.get("ban") or {}
    if not isinstance(require, dict) or not isinstance(deny, dict) or not isinstance(ban, dict):
        raise ValueError(
            f"operator policy {target}: 'require', 'deny' and 'ban' must each be objects"
        )

    return OperatorPolicy(
        require_issuers=set(require.get("issuers") or []),
        require_credential_type=str(require.get("credential_type") or ""),
        deny_issuers=set(deny.get("issuers") or []),
        deny_credential_type=str(deny.get("credential_type") or ""),
        ban_agents=set(ban.get("agents") or []),
        source=str(target),
        loaded=True,
    )


def apply_policy(registry: Any, policy: OperatorPolicy) -> None:
    """Add the operator's rules to a TrustRegistry.

    The operator's rules are added on top of the caller's, never instead of
    them. A caller's request for a permissive policy cannot remove the
    operator's deny or ban rules.
    """
    if policy.ban_agents:
        registry.ban(subject=set(policy.ban_agents), description="Operator ban policy.")
    if policy.deny_issuers or policy.deny_credential_type:
        registry.deny(
            issuer=set(policy.deny_issuers) if policy.deny_issuers else None,
            credential_type=(
                policy.deny_credential_type.encode("utf-8") if policy.deny_credential_type else None
            ),
            description="Operator deny policy.",
        )
    if policy.require_issuers or policy.require_credential_type:
        registry.require(
            issuer=set(policy.require_issuers) if policy.require_issuers else None,
            credential_type=(
                policy.require_credential_type.encode("utf-8")
                if policy.require_credential_type
                else None
            ),
            description="Operator required credential.",
        )
