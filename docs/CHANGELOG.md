# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.6.0] — 2026-10-29

Credential discovery.

v0.5.0 completed the credential lifecycle, and the MCP registry's
completeness review still had one complaint: "the lack of a credential
listing/discovery operation, which could make it harder to enumerate
credentials without already knowing the issuer, subject, and type."

That was fair. `get_credential` needs all three identifiers, so it
cannot answer the question you actually have when you are holding a
credential and not sure what it is.

### Added

- **`list_credentials`** — enumerate every credential involving an
  account: the ones it holds as a subject, and the ones it has issued to
  others. A pure read, needing neither `--allow-writes` nor a seed.

  Each entry carries the issuer, the subject, the credential type, the
  URI, the expiry, and whether the subject has accepted it — the same
  fields `get_credential` returns. The two tools answer the same shape
  of question at different scopes: one credential, or all of them.

  Pass `include_issued=False` to skip the issued side when you only care
  about what the account holds.

### Notes

- The ledger indexes credentials by subject, so there is no direct
  "credentials I issued" query. The issued side walks the issuer's own
  account objects instead, which is why it is a separate walk rather than
  a filter on the held list.
- Acceptance is reported per entry. A credential that exists but was
  never accepted reads as `accepted: false`, because XLS-70 sets that
  flag only when the subject signs.

The server now has twelve tools: seven read-only, five write.

## [0.5.0] — 2026-10-29

The credential lifecycle, end to end.

v0.4.0 could withdraw a credential but not create one. An agent could be
de-authorized but never authorized through this server, which left the
credential surface half a lifecycle. That is the gap the MCP registry's
completeness review flagged: "credential issuance or inspection is
absent".

Three tools close it. `issue_credential` and `accept_credential` are the
two signatures a credential needs; `get_credential` reads its state
without changing anything.

### Added

- **`issue_credential`** — submit a `CredentialCreate`, creating a
  credential for a subject. The subject may be an address or a
  `did:xrpl`.
- **`accept_credential`** — submit a `CredentialAccept` from the signing
  account. A credential created by the issuer is not valid for the
  subject until the subject signs it, so this is the subject's side of
  the same object.
- **`get_credential`** — read one credential's state from the ledger:
  whether it exists, whether the subject has accepted it, its URI, and
  its expiration. A pure read — it needs neither `--allow-writes` nor a
  seed.

### Changed

- The server instructions now describe the credential lifecycle and name
  all five write tools.
- The README's tool table lists eleven tools, and a new section walks
  through the four-step lifecycle.

### Notes

- **Existence is not acceptance.** XLS-70 sets the accepted flag only
  when the subject signs. `get_credential` reports the two states
  separately, and `issue_credential` says in its output that the
  credential still needs accepting.
- **The write gating is unchanged.** `issue_credential` and
  `accept_credential` refuse with `WRITES_DISABLED` unless the server was
  started with `--allow-writes`, and refuse an inline seed with
  `INLINE_SEED_REFUSED` unless it was started with
  `--allow-inline-seed`. The seed comes from the OS keychain, so it never
  passes through the agent's context.
- The URI on a credential is carried by the ledger as hex, not as text.

## [0.4.0] — 2026-10-28

Revocation, and a trust decision the caller cannot widen.

The MCP registry's lifecycle review noted that an agent could be
authorized but never de-authorized. That gap is now closed: a DID Document
can be deleted, a credential can be revoked, and the server enforces a
trust policy that its caller cannot relax.

### Added

- **`revoke_did`** — submit a `DIDDelete`, removing the account's DID
  Document so the DID no longer resolves. `DIDDelete` takes only common
  fields and always targets the signer's own DID, so the tool refuses with
  `ADDRESS_MISMATCH` if the requested address is not the one the seed
  controls — before anything is submitted.
- **`revoke_credential`** — submit a `CredentialDelete`, withdrawing one
  credential. The credential is identified by the triple (issuer, subject,
  credential_type); the subject may be given as an address or a `did:xrpl`.
- **`src/xrpl_did_mcp/policy.py`** — the operator's trust policy, loaded
  from `trust_policy.json` (or the path in `XRPL_MCP_POLICY`). It carries
  `require`, `deny` and `ban` sections.
- **`check_authorization`** gained two optional arguments,
  `denied_issuers` and `banned_agents`, and now reports the operator
  policy it applied.

### Changed

- **The operator's policy is applied on top of the caller's.** A caller
  that passes a permissive policy cannot widen its own permissions: the
  operator's deny and ban rules still apply. A missing policy file means
  the operator has not expressed an opinion, which is not the same as
  allowing everything — it means no operator rules are added.
- **A malformed policy file is surfaced, not ignored.** The tool returns
  `POLICY_INVALID`. Silently dropping the operator's deny rules because of
  a typo would quietly remove the property the file exists to provide.

### Notes

- Deleting a DID Document does **not** delete the account's credentials.
  The two are separate ledger objects: a DID is keyed by its owner
  account, and a credential by (issuer, subject, credential_type).
  rippled's `DIDDelete` erases only the DID object. To withdraw an
  authorization, revoke the credential too.
- All three write tools are refused unless the server was started with
  `--allow-writes`, and all three read their seed from the OS keychain.
- `xrpl-agent-id` is now pinned to `v0.5.3`, which adds
  `AgentIdentity.delete_did()`.

## [0.3.0] — 2026-10-28

Each tool now does exactly one thing, and the names say what they do.

The MCP Registry's Tool Definition Quality Score for this server flagged two
problems, both worth fixing: `ledger_lookup` overlapped with `resolve_did`
(it returned a DID Document under `kind="did"`), and its name did not follow
the verb-first convention the other tools used. Overlapping tools make a
model pick the wrong one, or call both for the same question.

### Changed

- **`ledger_lookup` is replaced by two focused tools.**
  - **`lookup_account`** — an account's balance in drops, owner count, and
    next transaction sequence.
  - **`lookup_transaction`** — a transaction's result code, ledger index,
    and validated status.
- **The `kind` parameter is gone.** Each tool now takes only the argument
  it needs: `address` for `lookup_account`, `tx_hash` for
  `lookup_transaction`. There is no way to ask the wrong question of either
  one.
- **Tool descriptions state their boundaries.** `resolve_did` says it is
  the only tool that returns a DID Document; `lookup_account` says to use
  `resolve_did` for a document; `lookup_transaction` says to use
  `lookup_account` for the account.
- The server instructions now name each tool's single job.

### Removed

- **`ledger_lookup` with `kind="did"`.** `resolve_did` already did this,
  and did it better.

### Migration

| Before | After |
|---|---|
| `ledger_lookup(target=<address>, kind="account")` | `lookup_account(address=<address>)` |
| `ledger_lookup(target=<did>, kind="did")` | `resolve_did(did=<did>)` |
| `ledger_lookup(target=<hash>, kind="tx")` | `lookup_transaction(tx_hash=<hash>)` |

### Added

- `tests/test_server.py` — a regression guard that every tool name starts
  with a verb, and that no tool advertises a capability another tool owns.

## [0.2.0] — 2026-10-28

The seed no longer travels through the agent's context, and the write
tools are opt-in.

### Changed

- **`publish_did_set` takes no `seed` argument.** The seed is read from
  the OS keychain by the server itself, so an agent calling the tool
  never sees it. The keychain service defaults to `xrpl-did-mcp` and is
  configurable with `XRPL_MCP_KEYCHAIN_SERVICE`.
- **Write tools refuse unless the server is started with `--allow-writes`.**
  Without it, `publish_did_set` returns `WRITES_DISABLED` and submits
  nothing.
- **`--allow-inline-seed`** permits a seed passed as the tool's `seed`
  argument. Off by default: a seed in the agent's context is a seed the
  agent can read. It exists for throwaway testnet use. Without the flag
  the argument is refused with `INLINE_SEED_REFUSED`. The keychain is
  always tried first, even when an inline seed is supplied.

There is no environment-variable fallback for the seed. An env var would
put the seed in the environment of a process the agent can influence,
which is the problem the keychain solves.

### Added

- `src/xrpl_did_mcp/credentials.py` — keychain reading, seed resolution
  order, and the setup instructions shown when no seed is available.
- `tests/test_credentials.py` — 9 tests covering write-gating, seed
  resolution order, and the absence of a seed argument.

## [0.1.1] — 2026-10-28

### Fixed

- **`xrpl-agent-id` is now pinned to the `v0.5.1` tag.** It was referenced
  from `main`, so an install could silently change when `main` moved.

### Added

- `SECURITY.md` — reporting path and the security-relevant design decisions
- `CONTRIBUTING.md` — how to contribute and how to add a tool
- `py.typed` — PEP 561 marker, shipped in the wheel

## [0.1.0] — 2026-10-28

First release.

### Added

- **`derive_did`** — turn an XRP address into a `did:xrpl`. Pure
  derivation: no transaction, no registration, no fee.
- **`resolve_did`** — read a DID Document for a `did:xrpl` from the ledger.
  The document is fetched from the ledger itself, so it is verifiable by
  anyone and does not depend on this server.
- **`check_authorization`** — decide whether an agent may act. Reports
  which required credentials are missing and which deny rules matched, so
  the decision is auditable rather than a bare boolean.
- **`ledger_lookup`** — query an account balance, a DID, or a transaction
  result.
- **`publish_did_set`** — publish an agent's DID Document. The only tool
  that writes to the ledger. Requires the caller to supply a funded seed;
  the server holds no credentials of its own.
- stdio and streamable-http transports.
- Per-call network override, defaulting to testnet.

### Notes

- `xrpl-agent-id` is pinned to `v0.5.1`. Neither package is on PyPI yet.
- Eight offline tests; one ledger-backed test is skipped unless
  `XRPL_MCP_LIVE=1` is set.

[0.4.0]: https://github.com/DuNorth595/xrpl_did_mcp/releases/tag/v0.4.0
[0.3.0]: https://github.com/DuNorth595/xrpl_did_mcp/releases/tag/v0.3.0
[0.2.0]: https://github.com/DuNorth595/xrpl_did_mcp/releases/tag/v0.2.0
[0.1.1]: https://github.com/DuNorth595/xrpl_did_mcp/releases/tag/v0.1.1
[0.1.0]: https://github.com/DuNorth595/xrpl_did_mcp/releases/tag/v0.1.0
