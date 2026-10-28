# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | Yes |

## Reporting a vulnerability

**Do not open a public issue for a security problem.**

Email **S_DevLabs@outlook.com** with:

- what the issue is, in one or two sentences
- how to reproduce it
- what you expect versus what happens

You will get an acknowledgement. Fixes for confirmed issues ship in a patch
release, and you are credited in the release notes unless you would rather
not be.

## Scope

### In scope

- The seed-handling path in `publish_did_set`
- Any way a tool could be made to sign or publish something the caller did
  not intend
- Credential or key material leaking into logs, errors, or tool output
- The `publish_did_set` URI validation

### Out of scope

- Anything the caller does with a seed they supply themselves
- The security of XRPL itself, or of `xrpl-py`
- Denial of service from a caller deliberately hammering the public RPC
- Reports from automated scanners with no demonstrated impact

## Security-relevant design decisions

**This server holds no credentials of its own.** It has no keychain entry,
no environment variable holding a seed, and no key file on disk. Every tool
that needs a funded wallet takes the seed as an explicit argument from the
caller.

**Four of the five tools are read-only.** `derive_did`, `resolve_did`,
`check_authorization`, and `ledger_lookup` never submit a transaction.
`publish_did_set` is the only tool that writes, and it costs XRP.

**The URI length is validated before submission.** XLS-40d caps
`DIDSet.URI` at 256 hex characters; the tool rejects a longer one rather
than letting the ledger reject it after paying a fee.

**Transaction submission uses `submit_with_retry`** from
`xrpl_agent_id`, which retries the transient ledger-close race rather than
failing a transaction that was in fact accepted.

**Dependencies are pinned to a release tag.** `xrpl-agent-id` is installed
from `v0.5.1`, not from a moving branch, so an install cannot silently
change underneath you.

## Running it safely

The server talks to a public XRPL RPC node. If you are handling anything
sensitive, point it at your own node and keep the seed out of the agent's
context entirely.

## Found a problem in a dependency?

`xrpl_agent_id` and `xrpl_agent_log` have their own security policies —
check those repositories. For problems in the XRPL protocol itself, report
to the XRPL Foundation.
