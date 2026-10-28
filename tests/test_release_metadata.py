"""The published version and the docs must agree with the tag.

A release that says one version in its metadata and another in its README
is a release nobody can install correctly.
"""

from __future__ import annotations

import re
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def _server_version() -> str:
    source = (ROOT / "src" / "xrpl_did_mcp" / "server.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', source)
    assert match, "server.py has no __version__"
    return match.group(1)


def test_pyproject_and_server_versions_agree():
    assert _pyproject_version() == _server_version()


def test_readme_install_command_matches_the_version():
    version = _pyproject_version()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"xrpl_did_mcp.git@v{version}" in readme, (
        f"README install command does not reference v{version}"
    )


def test_changelog_has_an_entry_for_this_version():
    version = _pyproject_version()
    changelog = (ROOT / "docs" / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{version}]" in changelog, f"CHANGELOG has no entry for {version}"


def test_dependencies_are_pinned_to_a_tag():
    """A git dependency on a moving branch can change under an install."""
    with (ROOT / "pyproject.toml").open("rb") as fh:
        deps = tomllib.load(fh)["project"]["dependencies"]
    for dep in deps:
        if "git+" in dep:
            assert "@v" in dep, f"git dependency is not pinned to a tag: {dep}"


def test_production_surfaces_exist():
    for name in (
        "SECURITY.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "docs/CHANGELOG.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
        "src/xrpl_did_mcp/py.typed",
    ):
        assert (ROOT / name).exists(), f"missing {name}"
