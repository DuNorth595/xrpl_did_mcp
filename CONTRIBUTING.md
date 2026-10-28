# Contributing to xrpl-did-mcp

Thanks for looking at this. The project is small and new, so there is room
to shape it — see the open issues for where input would help most.

## Getting set up

```bash
git clone https://github.com/DuNorth595/xrpl_did_mcp.git
cd xrpl_did_mcp
pip install -e ".[dev]"
```

`xrpl-agent-id` is pulled from GitHub rather than PyPI, so the editable
install brings it along. If you want to work on both at once, install
`xrpl_agent_id` from its own checkout first.

## Running the tests

```bash
pytest                      # offline tests
pytest -v                   # with names
XRPL_MCP_LIVE=1 pytest      # also runs the ledger-backed test
```

The default suite is fully offline. The one ledger-backed test is skipped
unless `XRPL_MCP_LIVE=1` is set, because CI should not depend on a public
RPC node being up.

## Style

```bash
ruff check .
ruff format .
```

Line length is 100. Type annotations are welcome on new code.

## What a good pull request looks like

- **One thing.** A tool addition, a fix, a doc improvement — not three.
- **A test.** If it changes behaviour, it needs a test that fails without
  the change.
- **A reason.** Say what problem it solves and who it helps. If it came
  from an issue, say which one.
- **No secrets.** Never commit a seed, a token, or a `.pypirc`. There is a
  pre-commit hook that checks for XRPL seed strings.

## Adding a tool

1. Write the function with a docstring — that docstring becomes the tool
   description the agent sees.
2. Register it with `@server.tool(name=..., title=..., description=...)`.
3. Return a dict. Return structured data, not a formatted string; the MCP
   client renders it.
4. Catch exceptions and return `{"error": ..., "message": ...}` rather than
   raising. A raising tool crashes the agent's turn.
5. Ask yourself whether it writes to the ledger. If it does, say so in the
   description, and make sure the caller supplies their own credentials.

## Commits and pull requests

Squash-merge is fine. Write commit messages in the imperative mood and
explain *why* in the body when the reason is not obvious from the diff.

## Reporting bugs

Open an issue. For anything security-related, follow
[SECURITY.md](SECURITY.md) instead — do not open a public issue.

## License

Contributions are accepted under the MIT license in [LICENSE](LICENSE).
