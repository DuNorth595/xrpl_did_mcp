# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.3.0]: https://github.com/DuNorth595/xrpl_did_mcp/releases/tag/v0.3.0
[0.2.0]: https://github.com/DuNorth595/xrpl_did_mcp/releases/tag/v0.2.0
[0.1.1]: https://github.com/DuNorth595/xrpl_did_mcp/releases/tag/v0.1.1
[0.1.0]: https://github.com/DuNorth595/xrpl_did_mcp/releases/tag/v0.1.0
