# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""MCP server exposing did:xrpl identity operations on the XRP Ledger.

Read-only by default. The write tools refuse unless the server is started
with --allow-writes, and they take their signing seed from the OS
keychain, which the server reads itself -- the seed never passes through
the agent's context.

Trust decisions combine the caller's policy with the server operator's
policy file, so a caller cannot widen its own permissions by passing a
permissive policy. See xrpl_did_mcp.policy.

Run it:
    python -m xrpl_did_mcp                       # stdio, read-only
    python -m xrpl_did_mcp --allow-writes        # stdio, writes enabled
    python -m xrpl_did_mcp --http 8080           # streamable-http
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer
from xrpl_agent_id import (
    NETWORK_IDS,
    TrustRegistry,
    did_from_account,
    get_client,
    parse_did,
    resolve_did,
)
from xrpl_agent_id.tx_submit import submit_with_retry

from xrpl_did_mcp import credentials, policy

__version__ = "0.5.0"

#: Set by --allow-writes at startup. When False, the write tools refuse
#: rather than submitting a transaction.
ALLOW_WRITES = False

# httpx logs every RPC request at INFO. On the stdio transport that lands in
# the MCP client's log for every single ledger call, which is noise. The
# requests still happen; they just stop announcing themselves.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

server = MCPServer(
    name="xrpl-did",
    title="XRP Ledger DID (did:xrpl)",
    version=__version__,
    instructions=(
        "W3C-compatible decentralized identifiers for AI agents on the XRP "
        "Ledger. Derive a did:xrpl from an address, resolve one back to its "
        "DID Document, and evaluate whether an agent is authorized to act. "
        "Each tool has one job: derive_did turns an address into a did:xrpl, "
        "resolve_did returns a DID Document, lookup_account returns an "
        "account's balance and state, and lookup_transaction returns a "
        "transaction's result. get_credential reads a credential's state, and "
        "check_authorization decides whether an agent may act.\n\n"
        "The credential lifecycle is issue_credential (the issuer creates it), "
        "accept_credential (the subject signs it), get_credential (anyone "
        "reads its state) and revoke_credential (the issuer withdraws it).\n\n"
        "The write tools -- publish_did_set, revoke_did, revoke_credential, "
        "issue_credential and accept_credential -- change the ledger and are "
        "refused unless the server was started with --allow-writes."
    ),
)


def _network_default() -> str:
    return os.environ.get("XRPL_MCP_NETWORK", "testnet")


# --------------------------------------------------------------------- tools


@server.tool(
    name="derive_did",
    title="Derive a did:xrpl from an XRP address",
    description=(
        "Derive the W3C-compatible decentralized identifier for an account on "
        "the XRP Ledger. Format: did:xrpl:<network-id>:<address>. This is a "
        "pure derivation -- no transaction, no registration, no fee."
    ),
)
def derive_did(address: str, network: str = "") -> dict[str, Any]:
    """Derive a did:xrpl from a classic XRP address."""
    net = network or _network_default()
    if net not in NETWORK_IDS:
        return {"error": f"unknown network {net!r}", "known": sorted(NETWORK_IDS)}

    did = did_from_account(address, network=net)
    network_id, subject = parse_did(did)
    return {
        "did": did,
        "address": subject,
        "network": net,
        "network_id": network_id,
        "resolution": "ledger lookup -- no server required",
    }


@server.tool(
    name="resolve_did",
    title="Resolve a did:xrpl to its DID Document",
    description=(
        "Resolve a did:xrpl into its DID Document by reading the XRP Ledger. "
        "The document is fetched from the ledger itself, so the result is "
        "verifiable by anyone and does not depend on this server.\n\n"
        "This is the only tool that returns a DID Document. For an account's "
        "balance and other on-ledger state, use lookup_account."
    ),
)
def resolve_did_tool(did: str, network: str = "") -> dict[str, Any]:
    """Resolve a did:xrpl against the ledger."""
    net = network or _network_default()
    try:
        document = resolve_did(did, network=net)
    except Exception as exc:
        return {
            "error": type(exc).__name__,
            "message": str(exc),
            "hint": (
                "A DID Document appears on the ledger once a DIDSet has been "
                "published for the account."
            ),
        }
    return {"did": did, "network": net, "document": document.to_json()}


@server.tool(
    name="check_authorization",
    title="Decide whether an agent is authorized to act",
    description=(
        "Evaluate an agent against a trust policy and return an allow/deny "
        "decision with the reasons. The result reports which required "
        "credentials are missing and which deny rules matched, so the "
        "decision is auditable rather than a bare boolean.\n\n"
        "The caller's policy is combined with the server operator's policy "
        "file, if one is configured. The operator's deny and ban rules are "
        "applied on top of the caller's and cannot be waived by them."
    ),
)
def check_authorization(
    agent_did: str,
    trusted_issuers: list[str],
    credential_type: str,
    network: str = "",
    denied_issuers: list[str] | None = None,
    banned_agents: list[str] | None = None,
) -> dict[str, Any]:
    """Check an agent's credentials against a trust policy."""
    net = network or _network_default()
    registry = TrustRegistry(network=net)
    registry.require(
        issuer=set(trusted_issuers),
        credential_type=credential_type.encode("utf-8"),
        description="Required authorization credential.",
    )
    if denied_issuers:
        registry.deny(
            issuer=set(denied_issuers),
            credential_type=credential_type.encode("utf-8"),
            description="Caller-supplied deny rule.",
        )
    if banned_agents:
        registry.ban(subject=set(banned_agents), description="Caller-supplied ban.")

    # The operator's policy is applied on top of the caller's, so a caller
    # cannot widen its own permissions by passing a permissive policy.
    try:
        operator_policy = policy.load_policy()
    except ValueError as exc:
        return {"error": "POLICY_INVALID", "message": str(exc)}

    try:
        policy.apply_policy(registry, operator_policy)
    except Exception as exc:
        return {"error": "POLICY_INVALID", "message": str(exc)}

    try:
        result = registry.check(agent_did)
    except Exception as exc:
        return {"error": type(exc).__name__, "message": str(exc)}

    return {
        "agent_did": agent_did,
        "network": net,
        "satisfied": result.satisfied,
        "missing_required": [
            {"issuer": issuer, "credential_type": ctype.decode("utf-8", "replace")}
            for issuer, ctype in (result.missing_required or [])
        ],
        "denied_held": [
            {"issuer": issuer, "credential_type": ctype.decode("utf-8", "replace")}
            for issuer, ctype in (result.denied_held or [])
        ],
        "decision": "ALLOW" if result.satisfied else "DENY",
        "operator_policy": operator_policy.summary(),
    }


@server.tool(
    name="publish_did_set",
    title="Publish an agent's DID Document (writes to the ledger)",
    description=(
        "Submit a DIDSet transaction so the agent's DID resolves to a DID "
        "Document. THIS WRITES TO THE LEDGER and spends XRP.\n\n"
        "The signing seed comes from the OS keychain, which the server "
        "reads itself, so the agent never sees the seed. This tool is only "
        "available if the server was started with --allow-writes.\n\n"
        "An optional `seed` argument exists for throwaway testnet use. It is "
        "refused unless the server was started with --allow-inline-seed, "
        "because a seed in the agent's context is a seed the agent can "
        "read.\n\n"
        "Defaults to testnet; pass network='mainnet' explicitly to use "
        "mainnet."
    ),
)
def publish_did_set(
    address: str,
    document_uri: str = "",
    network: str = "",
    seed: str = "",
) -> dict[str, Any]:
    """Publish a DIDSet. Writes to the ledger and spends XRP."""
    net = network or _network_default()

    if not ALLOW_WRITES:
        return {
            "error": "WRITES_DISABLED",
            "message": "This server was started without --allow-writes.",
            "hint": "Restart the server with --allow-writes to enable this tool.",
        }

    from xrpl.models.transactions import DIDSet
    from xrpl.wallet import Wallet

    uri = document_uri or f"https://sdevlabs.dev/did/{address}"
    uri_hex = uri.encode("utf-8").hex().upper()
    if len(uri_hex) > 256:
        return {
            "error": "URI_TOO_LONG",
            "message": f"{len(uri_hex)} hex characters; the cap is 256",
            "hint": "Shorten the URI, or publish the document under a shorter one.",
        }

    # The keychain is the default. An inline seed is used only when the
    # server was started with --allow-inline-seed, which is off by default.
    signing_seed, source = credentials.resolve_seed(seed or None)
    if signing_seed is None:
        if seed:
            return {
                "error": "INLINE_SEED_REFUSED",
                "message": "This server was started without --allow-inline-seed.",
                "hint": (
                    "An inline seed is refused by default, because a seed in "
                    "the agent's context is a seed the agent can read. Use the "
                    "keychain, or start the server with --allow-inline-seed for "
                    "throwaway testnet use."
                ),
            }
        return {
            "error": "NO_SEED",
            "message": "No signing seed available.",
            "hint": credentials.install_keychain_help(),
        }

    try:
        wallet = Wallet.from_seed(signing_seed)
    except Exception as exc:
        return {"error": "INVALID_SEED", "message": str(exc)}

    client = get_client(net)
    did_set = DIDSet(account=address, uri=uri_hex)
    try:
        result = submit_with_retry(did_set, client, wallet) or {}
    except Exception as exc:
        return {"error": type(exc).__name__, "message": str(exc)}

    meta = result.get("meta") or {}
    return {
        "tx_hash": result.get("hash"),
        "tx_result": meta.get("TransactionResult"),
        "ledger_index": result.get("ledger_index"),
        "network": net,
        "did": did_from_account(address, network=net),
        "uri": uri,
        "seed_source": source,
    }


@server.tool(
    name="revoke_did",
    title="Delete an account's DID Document (writes to the ledger)",
    description=(
        "Submit a DIDDelete transaction, removing the account's DID Document "
        "from the ledger so the DID no longer resolves. THIS WRITES TO THE "
        "LEDGER and spends XRP.\n\n"
        "DIDDelete takes only common fields -- it always deletes the "
        "signer's own DID, so the address must match the account the seed "
        "controls.\n\n"
        "This does NOT delete the account's credentials. A DID and a "
        "credential are separate ledger objects, and rippled's DIDDelete "
        "erases only the DID. To withdraw an authorization, revoke the "
        "credential too.\n\n"
        "This tool is only available if the server was started with "
        "--allow-writes. The signing seed comes from the OS keychain, which "
        "the server reads itself, so the seed never passes through the "
        "agent's context."
    ),
)
def revoke_did(
    address: str,
    network: str = "",
    seed: str = "",
) -> dict[str, Any]:
    """Delete an account's DID Document. Writes to the ledger and spends XRP."""
    net = network or _network_default()

    if not ALLOW_WRITES:
        return {
            "error": "WRITES_DISABLED",
            "message": "This server was started without --allow-writes.",
            "hint": "Restart the server with --allow-writes to enable this tool.",
        }

    from xrpl.models.transactions import DIDDelete
    from xrpl.wallet import Wallet

    signing_seed, source = credentials.resolve_seed(seed or None)
    if signing_seed is None:
        if seed:
            return {
                "error": "INLINE_SEED_REFUSED",
                "message": "This server was started without --allow-inline-seed.",
                "hint": (
                    "An inline seed is refused by default, because a seed in "
                    "the agent's context is a seed the agent can read. Use the "
                    "keychain, or start the server with --allow-inline-seed for "
                    "throwaway testnet use."
                ),
            }
        return {
            "error": "NO_SEED",
            "message": "No signing seed available.",
            "hint": credentials.install_keychain_help(),
        }

    try:
        wallet = Wallet.from_seed(signing_seed)
    except Exception as exc:
        return {"error": "INVALID_SEED", "message": str(exc)}

    # DIDDelete always targets the sender's own DID, so a seed that does
    # not control the requested address would delete the wrong DID. Check
    # before spending.
    if wallet.address != address:
        return {
            "error": "ADDRESS_MISMATCH",
            "message": (
                f"The signing seed controls {wallet.address}, not {address}. "
                "DIDDelete can only delete the signer's own DID."
            ),
            "hint": "Pass the address the seed actually controls.",
        }

    client = get_client(net)
    try:
        result = submit_with_retry(DIDDelete(account=address), client, wallet) or {}
    except Exception as exc:
        return {"error": type(exc).__name__, "message": str(exc)}

    meta = result.get("meta") or {}
    return {
        "tx_hash": result.get("hash"),
        "tx_result": meta.get("TransactionResult"),
        "ledger_index": result.get("ledger_index"),
        "network": net,
        "did": did_from_account(address, network=net),
        "seed_source": source,
    }


@server.tool(
    name="revoke_credential",
    title="Revoke a credential (writes to the ledger)",
    description=(
        "Submit a CredentialDelete transaction, withdrawing one credential so "
        "it no longer counts as held. THIS WRITES TO THE LEDGER and spends "
        "XRP.\n\n"
        "A credential is identified by the triple (issuer, subject, "
        "credential_type). The issuer or the subject may sign the delete.\n\n"
        "This tool is only available if the server was started with "
        "--allow-writes. The signing seed comes from the OS keychain, which "
        "the server reads itself, so the seed never passes through the "
        "agent's context."
    ),
)
def revoke_credential(
    subject: str,
    credential_type: str,
    network: str = "",
    seed: str = "",
) -> dict[str, Any]:
    """Revoke a credential. Writes to the ledger and spends XRP."""
    net = network or _network_default()

    if not ALLOW_WRITES:
        return {
            "error": "WRITES_DISABLED",
            "message": "This server was started without --allow-writes.",
            "hint": "Restart the server with --allow-writes to enable this tool.",
        }

    from xrpl.models.transactions import CredentialDelete
    from xrpl.wallet import Wallet

    subject_addr = subject
    if subject.startswith("did:xrpl:"):
        try:
            _, subject_addr = parse_did(subject)
        except Exception as exc:
            return {"error": "INVALID_DID", "message": str(exc)}

    signing_seed, source = credentials.resolve_seed(seed or None)
    if signing_seed is None:
        if seed:
            return {
                "error": "INLINE_SEED_REFUSED",
                "message": "This server was started without --allow-inline-seed.",
                "hint": (
                    "An inline seed is refused by default, because a seed in "
                    "the agent's context is a seed the agent can read. Use the "
                    "keychain, or start the server with --allow-inline-seed for "
                    "throwaway testnet use."
                ),
            }
        return {
            "error": "NO_SEED",
            "message": "No signing seed available.",
            "hint": credentials.install_keychain_help(),
        }

    try:
        wallet = Wallet.from_seed(signing_seed)
    except Exception as exc:
        return {"error": "INVALID_SEED", "message": str(exc)}

    try:
        cred_type = credential_type.encode("utf-8")
    except Exception as exc:
        return {"error": "INVALID_CREDENTIAL_TYPE", "message": str(exc)}

    client = get_client(net)
    tx = CredentialDelete(
        account=wallet.address,
        subject=subject_addr,
        credential_type=cred_type.hex().upper(),
    )
    try:
        result = submit_with_retry(tx, client, wallet) or {}
    except Exception as exc:
        return {"error": type(exc).__name__, "message": str(exc)}

    meta = result.get("meta") or {}
    return {
        "tx_hash": result.get("hash"),
        "tx_result": meta.get("TransactionResult"),
        "ledger_index": result.get("ledger_index"),
        "network": net,
        "issuer": wallet.address,
        "subject": subject_addr,
        "credential_type": credential_type,
        "seed_source": source,
    }


@server.tool(
    name="issue_credential",
    title="Issue a credential to a subject (writes to the ledger)",
    description=(
        "Create a credential on the XRP Ledger, so a subject can be authorised "
        "to act. THIS WRITES TO THE LEDGER and spends XRP.\n\n"
        "The signing seed comes from the OS keychain, which the server reads "
        "itself, so the agent never sees the seed. This tool is only "
        "available if the server was started with --allow-writes.\n\n"
        "An optional `seed` argument exists for throwaway testnet use. It is "
        "refused unless the server was started with --allow-inline-seed, "
        "because a seed in the agent's context is a seed the agent can read.\n\n"
        "The credential is created but not yet valid for the subject until "
        "the subject accepts it with accept_credential. To withdraw a "
        "credential, use revoke_credential instead. To read a credential's "
        "state without changing anything, use get_credential."
    ),
)
def issue_credential(
    subject: str,
    credential_type: str,
    network: str = "",
    uri: str = "",
    expiration: int | None = None,
    seed: str = "",
) -> dict[str, Any]:
    """Issue a credential to a subject's DID or address."""
    net = network or _network_default()

    if not ALLOW_WRITES:
        return {
            "error": "WRITES_DISABLED",
            "message": "This server was started without --allow-writes.",
            "hint": "Restart the server with --allow-writes to enable this tool.",
        }

    from xrpl.models.transactions import CredentialCreate
    from xrpl.wallet import Wallet

    subject_addr = subject
    if subject.startswith("did:xrpl:"):
        try:
            _, subject_addr = parse_did(subject)
        except Exception as exc:
            return {"error": "INVALID_DID", "message": str(exc)}

    signing_seed, source = credentials.resolve_seed(seed or None)
    if signing_seed is None:
        if seed:
            return {
                "error": "INLINE_SEED_REFUSED",
                "message": "This server was started without --allow-inline-seed.",
                "hint": (
                    "An inline seed is refused by default, because a seed in "
                    "the agent's context is a seed the agent can read. Use the "
                    "keychain, or start the server with --allow-inline-seed for "
                    "throwaway testnet use."
                ),
            }
        return {
            "error": "NO_SEED",
            "message": "No signing seed available.",
            "hint": credentials.install_keychain_help(),
        }

    try:
        wallet = Wallet.from_seed(signing_seed)
    except Exception as exc:
        return {"error": "INVALID_SEED", "message": str(exc)}

    try:
        cred_type = credential_type.encode("utf-8")
    except Exception as exc:
        return {"error": "INVALID_CREDENTIAL_TYPE", "message": str(exc)}

    client = get_client(net)
    # The ledger carries the URI as hex, not as text.
    tx = CredentialCreate(
        account=wallet.address,
        subject=subject_addr,
        credential_type=cred_type.hex().upper(),
        uri=uri.encode("utf-8").hex().upper() if uri else None,
        expiration=expiration,
    )
    try:
        result = submit_with_retry(tx, client, wallet) or {}
    except Exception as exc:
        return {"error": type(exc).__name__, "message": str(exc)}

    meta = result.get("meta") or {}
    return {
        "tx_hash": result.get("hash"),
        "tx_result": meta.get("TransactionResult"),
        "ledger_index": result.get("ledger_index"),
        "network": net,
        "issuer": wallet.address,
        "subject": subject_addr,
        "credential_type": credential_type,
        "uri": uri or None,
        "expiration": expiration,
        "accepted": False,
        "next_step": (
            "The credential exists but is not yet valid for the subject. "
            "The subject must call accept_credential."
        ),
        "seed_source": source,
    }


@server.tool(
    name="accept_credential",
    title="Accept a credential issued to this account (writes to the ledger)",
    description=(
        "Accept a credential that was issued to this account, making it "
        "valid for the subject. THIS WRITES TO THE LEDGER and spends XRP.\n\n"
        "A credential created with issue_credential is not valid for the "
        "subject until the subject signs it. This tool is the subject's side "
        "of that: it submits the CredentialAccept.\n\n"
        "The signing seed comes from the OS keychain, which the server reads "
        "itself, so the agent never sees the seed. This tool is only "
        "available if the server was started with --allow-writes.\n\n"
        "An optional `seed` argument exists for throwaway testnet use. It is "
        "refused unless the server was started with --allow-inline-seed.\n\n"
        "To read a credential's state without changing anything, use "
        "get_credential. To withdraw a credential, use revoke_credential."
    ),
)
def accept_credential(
    issuer: str,
    credential_type: str,
    network: str = "",
    seed: str = "",
) -> dict[str, Any]:
    """Accept a credential issued to the signing account."""
    net = network or _network_default()

    if not ALLOW_WRITES:
        return {
            "error": "WRITES_DISABLED",
            "message": "This server was started without --allow-writes.",
            "hint": "Restart the server with --allow-writes to enable this tool.",
        }

    from xrpl.models.transactions import CredentialAccept
    from xrpl.wallet import Wallet

    issuer_addr = issuer
    if issuer.startswith("did:xrpl:"):
        try:
            _, issuer_addr = parse_did(issuer)
        except Exception as exc:
            return {"error": "INVALID_DID", "message": str(exc)}

    signing_seed, source = credentials.resolve_seed(seed or None)
    if signing_seed is None:
        if seed:
            return {
                "error": "INLINE_SEED_REFUSED",
                "message": "This server was started without --allow-inline-seed.",
                "hint": (
                    "An inline seed is refused by default, because a seed in "
                    "the agent's context is a seed the agent can read. Use the "
                    "keychain, or start the server with --allow-inline-seed for "
                    "throwaway testnet use."
                ),
            }
        return {
            "error": "NO_SEED",
            "message": "No signing seed available.",
            "hint": credentials.install_keychain_help(),
        }

    try:
        wallet = Wallet.from_seed(signing_seed)
    except Exception as exc:
        return {"error": "INVALID_SEED", "message": str(exc)}

    try:
        cred_type = credential_type.encode("utf-8")
    except Exception as exc:
        return {"error": "INVALID_CREDENTIAL_TYPE", "message": str(exc)}

    client = get_client(net)
    tx = CredentialAccept(
        account=wallet.address,
        issuer=issuer_addr,
        credential_type=cred_type.hex().upper(),
    )
    try:
        result = submit_with_retry(tx, client, wallet) or {}
    except Exception as exc:
        return {"error": type(exc).__name__, "message": str(exc)}

    meta = result.get("meta") or {}
    return {
        "tx_hash": result.get("hash"),
        "tx_result": meta.get("TransactionResult"),
        "ledger_index": result.get("ledger_index"),
        "network": net,
        "issuer": issuer_addr,
        "subject": wallet.address,
        "credential_type": credential_type,
        "accepted": True,
        "seed_source": source,
    }


@server.tool(
    name="get_credential",
    title="Read a credential's state on the ledger",
    description=(
        "Read one credential's state from the XRP Ledger: whether it exists, "
        "whether the subject has accepted it, its URI, and its expiration.\n\n"
        "This is a pure read. It submits no transaction, spends nothing, and "
        "does not need --allow-writes.\n\n"
        "Existence is not acceptance: a credential issued with "
        "issue_credential does not become valid for the subject until the "
        "subject accepts it. This tool reports which of those is true.\n\n"
        "For an account's balance and other state, use lookup_account. To "
        "accept a credential, use accept_credential."
    ),
)
def get_credential(
    issuer: str,
    subject: str,
    credential_type: str,
    network: str = "",
) -> dict[str, Any]:
    """Read a credential's state from the ledger."""
    net = network or _network_default()

    issuer_addr = issuer
    if issuer.startswith("did:xrpl:"):
        try:
            _, issuer_addr = parse_did(issuer)
        except Exception as exc:
            return {"error": "INVALID_DID", "message": str(exc)}

    subject_addr = subject
    if subject.startswith("did:xrpl:"):
        try:
            _, subject_addr = parse_did(subject)
        except Exception as exc:
            return {"error": "INVALID_DID", "message": str(exc)}

    try:
        cred_type = credential_type.encode("utf-8")
    except Exception as exc:
        return {"error": "INVALID_CREDENTIAL_TYPE", "message": str(exc)}

    from xrpl.models.requests import LedgerEntry
    from xrpl.models.requests.ledger_entry import Credential as LedgerCredential

    client = get_client(net)
    try:
        response = client.request(
            LedgerEntry(
                credential=LedgerCredential(
                    subject=subject_addr,
                    issuer=issuer_addr,
                    credential_type=cred_type.hex().upper(),
                )
            )
        )
    except Exception as exc:
        return {"error": type(exc).__name__, "message": str(exc)}

    node = (getattr(response, "result", None) or {}).get("node")
    if node is None:
        return {
            "found": False,
            "network": net,
            "issuer": issuer_addr,
            "subject": subject_addr,
            "credential_type": credential_type,
            "accepted": False,
            "message": "No such credential on the ledger.",
        }

    from xrpl_agent_id.credential import credential_is_accepted

    return {
        "found": True,
        "network": net,
        "issuer": issuer_addr,
        "subject": subject_addr,
        "credential_type": credential_type,
        "accepted": credential_is_accepted(node),
        "uri": node.get("uri"),
        "expiration": node.get("expiration"),
    }


@server.tool(
    name="lookup_account",
    title="Look up an account's balance and state on the ledger",
    description=(
        "Read an XRP Ledger account's on-ledger state: its balance in drops, "
        "the number of objects it owns, and its next transaction sequence.\n\n"
        "For an account's DID Document, use resolve_did instead -- that tool "
        "returns the document itself, not the account's other state."
    ),
)
def lookup_account(address: str, network: str = "") -> dict[str, Any]:
    """Look up an account's balance and on-ledger state."""
    from xrpl.models.requests import AccountInfo

    net = network or _network_default()
    client = get_client(net)
    try:
        info = client.request(AccountInfo(account=address))
    except Exception as exc:
        return {"error": type(exc).__name__, "message": str(exc)}

    data = (info.result or {}).get("account_data") or {}
    return {
        "address": address,
        "network": net,
        "balance_drops": data.get("Balance"),
        "owner_count": data.get("OwnerCount"),
        "sequence": data.get("Sequence"),
    }


@server.tool(
    name="lookup_transaction",
    title="Look up a transaction's result on the ledger",
    description=(
        "Read a transaction's outcome from the XRP Ledger by its hash: the "
        "result code (for example tesSUCCESS for a successful transaction, or "
        "tecUNFUNDED_PAYMENT for one that failed), the ledger index it was "
        "included in, and whether it has been validated.\n\n"
        "For the account that sent or received it, use lookup_account."
    ),
)
def lookup_transaction(tx_hash: str, network: str = "") -> dict[str, Any]:
    """Look up a transaction's result and inclusion status."""
    from xrpl.models.requests import Tx

    net = network or _network_default()
    client = get_client(net)
    try:
        tx = client.request(Tx(transaction=tx_hash))
    except Exception as exc:
        return {"error": type(exc).__name__, "message": str(exc)}

    result = tx.result or {}
    meta = result.get("meta") or {}
    return {
        "hash": tx_hash,
        "network": net,
        "result": meta.get("TransactionResult"),
        "ledger_index": result.get("ledger_index"),
        "validated": result.get("validated"),
    }


# ------------------------------------------------------------------- runner


def main() -> None:
    parser = argparse.ArgumentParser(description="did:xrpl MCP server")
    parser.add_argument(
        "--http",
        type=int,
        default=0,
        help="Serve over streamable-http on this port instead of stdio.",
    )
    parser.add_argument(
        "--network",
        default="",
        help="Default network (testnet or mainnet). Overridable per call.",
    )
    parser.add_argument(
        "--allow-writes",
        action="store_true",
        help=(
            "Enable the write tools (publish_did_set). Without this flag "
            "they refuse. Writes spend XRP."
        ),
    )
    parser.add_argument(
        "--allow-inline-seed",
        action="store_true",
        help=(
            "Permit a seed passed as a tool argument. Off by default: a "
            "seed in the agent's context is a seed the agent can read."
        ),
    )
    args = parser.parse_args()

    global ALLOW_WRITES
    ALLOW_WRITES = args.allow_writes
    credentials.configure(allow_inline_seed=args.allow_inline_seed)

    if args.network:
        os.environ["XRPL_MCP_NETWORK"] = args.network

    if args.allow_writes:
        print(
            "  writes ENABLED -- publish_did_set will submit transactions",
            file=sys.stderr,
        )
    else:
        print(
            "  writes disabled (pass --allow-writes to enable publish_did_set)",
            file=sys.stderr,
        )

    if args.http:
        server.run(transport="streamable-http", port=args.http)
    else:
        server.run(transport="stdio")


if __name__ == "__main__":
    main()
