# xrpl-did-mcp

An MCP server for **`did:xrpl`** — W3C-compatible decentralized identifiers for AI agents on the XRP Ledger.

Point any MCP client at it and your agent can derive, resolve, and authorize identities natively.

```bash
# not on PyPI yet -- install from GitHub, pinned to the release tag
pip install "xrpl-did-mcp @ git+https://github.com/DuNorth595/xrpl_did_mcp.git@v0.2.0"
```

That one command pulls in `xrpl-agent-id` and `xrpl-py` as well. Both are
pinned to release tags, so an install cannot silently change underneath
you.

---

## What it gives an MCP client

| Tool | What it does | Writes to the ledger? |
|---|---|---|
| `derive_did` | Turn an XRP address into a `did:xrpl` | No |
| `resolve_did` | Read a DID Document for a `did:xrpl` | No |
| `check_authorization` | Decide whether an agent may act, with reasons | No |
| `ledger_lookup` | Account balance, DID, or transaction result | No |
| `publish_did_set` | Publish an agent's DID Document | **Yes** — spends XRP |

Four of the five tools are read-only. The one that writes:

- **Refuses by default.** It returns `WRITES_DISABLED` unless the server was
  started with `--allow-writes`.
- **Takes no seed argument.** The signing seed comes from the OS keychain,
  which the server reads itself, so the seed never passes through the
  agent's context.

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
xrpl-did-mcp                       # stdio, testnet, read-only
xrpl-did-mcp --network mainnet     # stdio, mainnet, read-only
xrpl-did-mcp --allow-writes        # enable publish_did_set
xrpl-did-mcp --http 8080           # streamable-http
```

### Enabling the write tool

`publish_did_set` needs two things: the server started with
`--allow-writes`, and a signing seed in the OS keychain.

```bash
# Store a seed (macOS). The server reads it; the agent never sees it.
security add-generic-password -a "$USER" -s xrpl-did-mcp -w '<your seed>'
```

The keychain service defaults to `xrpl-did-mcp`; set
`XRPL_MCP_KEYCHAIN_SERVICE` to change it.

If the keychain has no entry, the tool returns `NO_SEED` with the setup
instructions. It never falls back to something weaker.

For throwaway testnet use where a seed in the conversation is harmless,
`--allow-inline-seed` permits passing one directly. It is off by default,
because a seed in the agent's context is a seed the agent can read.

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

The default suite is fully offline. One ledger-backed test is skipped unless
`XRPL_MCP_LIVE=1` is set.

## Documentation

| Document | What it covers |
|---|---|
| [SECURITY.md](SECURITY.md) | Reporting a vulnerability, and the security-relevant design decisions |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute, and how to add a tool |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | Release history |

## License

MIT — see [LICENSE](LICENSE).
