"""Credential handling for the write path.

The seed never travels through the agent's context.

Resolution order for a write:

  1. The OS keychain. The server reads the seed itself, so the agent
     never sees it. This is the default and the recommended path.
  2. An explicit `seed` argument to the tool. This exists for
     throwaway testnet use, where a seed in the conversation is
     harmless. It is refused unless the server was started with
     `--allow-inline-seed`.

There is no environment-variable fallback. An env var would put the seed
in the environment of a process the agent can influence, which is the
same problem the keychain solves, just quieter about it.

If the keychain holds no seed, the write path fails with a message
saying exactly what to do. It never falls back to something weaker.
"""

from __future__ import annotations

import os
import subprocess

#: The keychain service that holds the signing seed.
KEYCHAIN_SERVICE = os.environ.get("XRPL_MCP_KEYCHAIN_SERVICE", "xrpl-did-mcp")

#: Set by --allow-inline-seed at startup.
ALLOW_INLINE_SEED = False


def configure(*, allow_inline_seed: bool = False) -> None:
    """Set the module-level policy. Called once at startup."""
    global ALLOW_INLINE_SEED
    ALLOW_INLINE_SEED = allow_inline_seed


def keychain_service() -> str:
    """The keychain service name this server reads."""
    return KEYCHAIN_SERVICE


def seed_from_keychain() -> str | None:
    """Read the signing seed from the OS keychain.

    Returns None if the entry is absent or the platform has no keychain
    (the `security` command is macOS-only). Never raises for a missing
    entry -- the caller decides what to do about it.
    """
    if not os.path.exists("/usr/bin/security"):
        return None
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    seed = (proc.stdout or "").strip()
    return seed or None


def resolve_seed(inline_seed: str | None = None) -> tuple[str | None, str | None]:
    """Resolve the signing seed and say where it came from.

    Returns `(seed, source)` where source is one of:
      "keychain"   -- read from the OS keychain
      "inline"     -- supplied by the caller
      None         -- no seed could be found

    An inline seed is refused unless the server was started with
    `--allow-inline-seed`.
    """
    seed = seed_from_keychain()
    if seed:
        return seed, "keychain"

    if inline_seed:
        if not ALLOW_INLINE_SEED:
            return None, "inline-refused"
        return inline_seed, "inline"

    return None, None


def install_keychain_help() -> str:
    """The message shown when no seed could be found."""
    return (
        "No signing seed available.\n\n"
        f"Store one in the macOS keychain under service {KEYCHAIN_SERVICE!r}:\n\n"
        "    security add-generic-password -a \"$USER\" "
        f"-s {KEYCHAIN_SERVICE} -w '<your seed>'\n\n"
        "Then restart the server. The agent never sees the seed -- the "
        "server reads it from the keychain itself."
    )
