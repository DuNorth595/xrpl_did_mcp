# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.0]: https://github.com/DuNorth595/xrpl_did_mcp/releases/tag/v0.1.0
