# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""MCP server exposing did:xrpl identity operations on the XRP Ledger.

Read-only by default. The write tools refuse unless the server is started
with --allow-writes, and they take their signing seed from the OS
keychain, which the server reads itself -- the seed never passes through
the agent's context.

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

from xrpl_did_mcp import credentials

__version__ = "0.2.0"

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
        "Read-only unless a caller supplies a funded seed explicitly."
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
        "verifiable by anyone and does not depend on this server."
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
        "decision is auditable rather than a bare boolean."
    ),
)
def check_authorization(
    agent_did: str,
    trusted_issuers: list[str],
    credential_type: str,
    network: str = "",
) -> dict[str, Any]:
    """Check an agent's credentials against a trust policy."""
    net = network or _network_default()
    registry = TrustRegistry(network=net)
    registry.require(
        issuer=set(trusted_issuers),
        credential_type=credential_type.encode("utf-8"),
        description="Required authorization credential.",
    )
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
        "Defaults to testnet; pass network='mainnet' explicitly to use "
        "mainnet."
    ),
)
def publish_did_set(
    address: str,
    document_uri: str = "",
    network: str = "",
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

    seed, source = credentials.resolve_seed()
    if seed is None:
        return {
            "error": "NO_SEED",
            "message": "No signing seed available.",
            "hint": credentials.install_keychain_help(),
        }

    try:
        wallet = Wallet.from_seed(seed)
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
    name="ledger_lookup",
    title="Look up an account, DID, or transaction on the ledger",
    description=(
        "Query the XRP Ledger for an account's balance, the DID Document "
        "attached to an account, or the result of a transaction by hash."
    ),
)
def ledger_lookup(
    target: str,
    kind: str = "account",
    network: str = "",
) -> dict[str, Any]:
    """Look up an account, DID, or transaction hash on the ledger."""
    net = network or _network_default()
    client = get_client(net)

    try:
        if kind == "account":
            info = client.request_account_info(target)
            data = (info.result or {}).get("account_data") or {}
            return {
                "kind": "account",
                "address": target,
                "balance_drops": data.get("Balance"),
                "owner_count": data.get("OwnerCount"),
                "sequence": data.get("Sequence"),
            }
        if kind == "did":
            document = resolve_did(target, network=net)
            return {"kind": "did", "did": target, "document": document.to_json()}
        if kind == "tx":
            tx = client.request_tx(target)
            result = tx.result or {}
            meta = result.get("meta") or {}
            return {
                "kind": "tx",
                "hash": target,
                "result": meta.get("TransactionResult"),
                "ledger_index": result.get("ledger_index"),
                "validated": result.get("validated"),
            }
    except Exception as exc:
        return {"error": type(exc).__name__, "message": str(exc)}

    return {"error": "UNKNOWN_KIND", "kind": kind, "known": ["account", "did", "tx"]}


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
