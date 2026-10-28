# xrpl-did-mcp

An MCP server for **`did:xrpl`** — W3C-compatible decentralized identifiers for AI agents on the XRP Ledger.

Point any MCP client at it and your agent can derive, resolve, and authorize identities natively.

```bash
# not on PyPI yet -- install from GitHub
pip install "xrpl-did-mcp @ git+https://github.com/DuNorth595/xrpl_did_mcp.git"
```

---

## What it gives an MCP client

| Tool | What it does | Writes to the ledger? |
|---|---|---|
| `derive_did` | Turn an XRP address into a `did:xrpl` | No |
| `resolve_did` | Read a DID Document for a `did:xrpl` | No |
| `check_authorization` | Decide whether an agent may act, with reasons | No |
| `ledger_lookup` | Account balance, DID, or transaction result | No |
| `publish_did_set` | Publish an agent's DID Document | **Yes** — spends XRP |

Four of the five tools are read-only. The one that writes requires a funded seed to be passed in by the caller — **the server never holds credentials of its own**.

---

## Connect it

### Claude Desktop / Claude Code

```json
{
  "mcpServers": {
    "xrpl-did": {
      "command": "xrpl-did-mcp",
      "env": { "XRPL_MCP_NETWORK": "testnet" }
    }
  }
}
```

### Any other MCP client (stdio)

```bash
xrpl-did-mcp                       # stdio, testnet
xrpl-did-mcp --network mainnet     # stdio, mainnet
xrpl-did-mcp --http 8080           # streamable-http
```

---

## What it looks like in a conversation

> **Agent:** Give me a DID for `rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh`.

> **Tool `derive_did`:** `did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh`
> — derived from the address. No registration, no fee, no database row.

> **Agent:** Resolve it.

> **Tool `resolve_did`:** reads the `DIDSet` from the ledger and returns the
> DID Document. Anyone can do this — there is no server of ours in the path.

> **Agent:** Can this agent act?

> **Tool `check_authorization`:** `ALLOW` — or `DENY` with the list of
> missing credentials and the deny rules that fired.

---

## Why a DID and not just an address

An XRP address says *who holds a key*. It does not say *what that key is
allowed to do*, who vouches for it, or whether it was revoked.

`did:xrpl` adds a resolvable DID Document, so an agent's identity can
carry credentials — and the authorization decision becomes a ledger read
rather than a trust-me.

Built on [`xrpl_agent_id`](https://github.com/DuNorth595/xrpl_agent_id),
which implements the XLS-40d DID model and XLS-70d credentials.

---

## Networks

Testnet by default. Mainnet is the same code with `network="mainnet"`.

`xrpl_agent_id` has been validated on **XRPL mainnet** — all six authorization
roles, verified against the public ledger. See the
[mainnet decision record](https://github.com/DuNorth595/xrpl_agent_id/blob/main/docs/MAINNET_DECISION.md).

---

## Development

```bash
git clone https://github.com/DuNorth595/xrpl_did_mcp
cd xrpl_did_mcp
pip install -e ".[dev]"
pytest
```

## License

MIT — see [LICENSE](LICENSE).
