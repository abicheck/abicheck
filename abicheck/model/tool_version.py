# SPDX-License-Identifier: Apache-2.0
"""Which abicheck build is running: the release number, plus the checkout commit.

``pyproject.toml`` carries a hand-bumped release number, so every commit after
a release reported that release (``0.7.0`` on main, months later) and a field
report could not name the code it ran. :func:`build_version` appends
``+g<sha9>`` when the package was imported from an abicheck source checkout.
``--version`` and the SARIF ``tool.driver.version`` read it.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path

__all__ = ["build_version", "checkout_commit"]

#: The directory holding the ``abicheck`` package (a checkout's root).
_CHECKOUT_ROOT = Path(__file__).resolve().parent.parent.parent


def build_version() -> str:
    """The installed release number, plus ``+g<sha9>`` from an abicheck checkout.

    Only the checkout this package was imported from counts -- its root
    holding both ``.git`` and abicheck's own ``pyproject.toml`` -- so a wheel
    installed inside some other repository never borrows that repository's
    commit. A few small file reads, so it is called uncached.
    """
    try:
        release = _pkg_version("abicheck")
    except PackageNotFoundError:
        release = "0.0.0.dev0"  # running from source without install
    sha = checkout_commit(_CHECKOUT_ROOT)
    return f"{release}+g{sha[:9]}" if sha else release


def checkout_commit(root: Path) -> str | None:
    """The commit *root* (an abicheck source checkout) has checked out, or ``None``.

    Read from ``.git`` directly, without a ``git`` subprocess: loose refs,
    ``packed-refs``, a detached HEAD and a linked worktree's ``gitdir:`` /
    ``commondir`` indirection. Any unreadable or undecodable file (git allows
    non-UTF-8 ref names) yields ``None`` -- the plain release number -- never
    an exception, since the CLI resolves this while it loads.
    """
    try:
        if 'name = "abicheck"' not in (root / "pyproject.toml").read_text("utf-8"):
            return None
        git = root / ".git"
        if git.is_file():  # a worktree: "gitdir: <path>"
            git = (root / git.read_text("utf-8").split(":", 1)[1].strip()).resolve()
        head = (git / "HEAD").read_text("utf-8").strip()
        if not head.startswith("ref: "):
            return head or None  # detached HEAD
        ref = head[5:]
        common = git
        if (git / "commondir").is_file():  # worktree refs live in the main repo
            common = (git / (git / "commondir").read_text("utf-8").strip()).resolve()
        for base in (git, common):
            if (base / ref).is_file():
                return (base / ref).read_text("utf-8").strip() or None
        packed = (
            (common / "packed-refs").read_bytes().decode("utf-8", "surrogateescape")
        )
        for line in packed.splitlines():
            if line.endswith(" " + ref):
                return line.split(" ", 1)[0]
    except (OSError, ValueError, IndexError):  # ValueError: UnicodeDecodeError
        return None
    return None
