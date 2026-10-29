# xrpl-did-mcp

An MCP server for **`did:xrpl`** — W3C-compatible decentralized identifiers for AI agents on the XRP Ledger.

Point any MCP client at it and your agent can derive, resolve, and authorize identities natively.

```bash
# not on PyPI yet -- install from GitHub, pinned to the release tag
pip install "xrpl-did-mcp @ git+https://github.com/DuNorth595/xrpl_did_mcp.git@v0.5.0"
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
| `lookup_account` | Read an account's balance and on-ledger state | No |
| `lookup_transaction` | Read a transaction's result and inclusion status | No |
| `get_credential` | Read a credential's state — exists, accepted, URI, expiry | No |
| `issue_credential` | Create a credential for a subject | **Yes** — spends XRP |
| `accept_credential` | Accept a credential issued to the signing account | **Yes** — spends XRP |
| `publish_did_set` | Publish an agent's DID Document | **Yes** — spends XRP |
| `revoke_did` | Delete an account's DID Document | **Yes** — spends XRP |
| `revoke_credential` | Withdraw one credential | **Yes** — spends XRP |

Six of the eleven tools are read-only. The five that write:

- **Refuse by default.** Each returns `WRITES_DISABLED` unless the server
  was started with `--allow-writes`.
- **Take no seed argument.** The signing seed comes from the OS keychain,
  which the server reads itself, so the seed never passes through the
  agent's context.

### Revocation

`revoke_did` submits a `DIDDelete`, so the DID no longer resolves.
`DIDDelete` always targets the signer's own DID, so the tool refuses with
`ADDRESS_MISMATCH` if the address is not the one the seed controls —
before anything is submitted.

`revoke_credential` submits a `CredentialDelete`, withdrawing one
credential. A credential is identified by the triple (issuer, subject,
credential_type); the subject may be an address or a `did:xrpl`.

**Deleting a DID does not delete the account's credentials.** A DID and a
credential are separate ledger objects — a DID is keyed by its owner
account, a credential by (issuer, subject, credential_type). rippled's
`DIDDelete` erases only the DID. To withdraw an authorization, revoke the
credential too.

### The credential lifecycle

A credential is identified by the triple (issuer, subject,
credential_type), and it takes two signatures before it is valid for the
subject:

| Step | Tool | Who signs |
|---|---|---|
| 1. Create | `issue_credential` | the issuer |
| 2. Accept | `accept_credential` | the subject |
| 3. Read | `get_credential` | nobody — it is a read |
| 4. Withdraw | `revoke_credential` | the issuer |

`issue_credential` submits a `CredentialCreate`. The credential exists on
the ledger at that point, but XLS-70 only sets the accepted flag when the
subject signs — so until `accept_credential` runs, the credential does not
count as held. `get_credential` reports the two states separately, because
existence is not acceptance.

The subject and issuer may each be an address or a `did:xrpl`; a
`did:xrpl` is reduced to its address before the transaction is built.

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
xrpl-did-mcp --allow-writes        # enable the write tools
xrpl-did-mcp --http 8080           # streamable-http
```

### Enabling the write tools

The write tools — `publish_did_set`, `revoke_did`, `revoke_credential`,
`issue_credential` and `accept_credential` — need two things: the server
started with `--allow-writes`, and a signing seed in the OS keychain.

```bash
# Store a seed (macOS). The server reads it; the agent never sees it.
security add-generic-password -a "$USER" -s xrpl-did-mcp -w '<your seed>'
```

The keychain service defaults to `xrpl-did-mcp`; set
`XRPL_MCP_KEYCHAIN_SERVICE` to change it.

If the keychain has no entry, the tool returns `NO_SEED` with the setup
instructions. It never falls back to something weaker.

For throwaway testnet use where a seed in the conversation is harmless,
`--allow-inline-seed` permits passing one directly as the tool's `seed`
argument. It is off by default, because a seed in the agent's context is a
seed the agent can read.

Without that flag the `seed` argument is refused: the tool returns
`INLINE_SEED_REFUSED` and falls back to the keychain. The keychain is
always tried first, even when an inline seed is supplied.

---

## Operator trust policy

The caller of a tool is not necessarily the operator of the server. An
agent that chooses its own trust policy can always choose a permissive
one, so the server keeps its own policy on disk and applies it **on top of**
whatever the caller passes.

Put a `trust_policy.json` in the server's working directory, or point
`XRPL_MCP_POLICY` at one:

```json
{
  "require": {
    "issuers": ["rTrustedIssuer..."],
    "credential_type": "agent-authorization"
  },
  "deny": {
    "issuers": ["rRevokedIssuer..."],
    "credential_type": "agent-authorization"
  },
  "ban": {
    "agents": ["did:xrpl:1:rBanned...", "rBannedPlain..."]
  }
}
```

Every section is optional.

- **`require`** — the agent must hold this credential.
- **`deny`** — holding this credential is grounds for refusal, whatever
  else it holds.
- **`ban`** — the agent is refused outright.

The operator's rules cannot be waived by the caller. A missing policy file
means the operator has not expressed an opinion — it does **not** mean
allow everything; it means no operator rules are added to the caller's.

A policy file that exists but cannot be parsed is surfaced as
`POLICY_INVALID` rather than ignored, because silently dropping the
operator's deny rules would quietly remove the property the file exists to
provide.

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

The default suite is fully offline. Two ledger-backed tests are skipped unless
`XRPL_MCP_LIVE=1` is set.

## Documentation

| Document | What it covers |
|---|---|
| [SECURITY.md](SECURITY.md) | Reporting a vulnerability, and the security-relevant design decisions |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute, and how to add a tool |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | Release history |

## License

MIT — see [LICENSE](LICENSE).
