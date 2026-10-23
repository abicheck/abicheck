"""Step preconditions for ``scripts/verify.py``.

Each ``_need_*`` factory returns a zero-argument check that yields ``None``
when its step can run here, or the reason it cannot (a missing tool, module,
platform or ref), which ``verify.py`` records as a skip. Split out of
``verify.py`` to keep that orchestrator under its file-size record; a leaf
module with no import back into it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _which_any(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _module_available(name: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(name) is not None


def _origin_main_available() -> str | None:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "-q", "origin/main"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return (
            "no origin/main ref available locally (shallow clone or detached checkout)"
        )
    return None


def _need_bins(*names: str) -> Callable[[], str | None]:
    def check() -> str | None:
        if _which_any(*names) is None:
            return f"none of {', '.join(names)} found on PATH"
        return None

    return check


def _need_all_bins(*names: str) -> Callable[[], str | None]:
    """Like :func:`_need_bins`, but every name must be present, not just one.

    ``_need_bins``'s "any of" semantics fit a single-tool-with-aliases check
    (``abi-compliance-checker``/``.pl``); a step needing several genuinely
    different tools together (e.g. ``clang``/``clang++``/``g++``) needs
    "all of", not "at least one of".
    """

    def check() -> str | None:
        missing = [n for n in names if shutil.which(n) is None]
        if missing:
            return f"not found on PATH: {', '.join(missing)}"
        return None

    return check


def _need_linux_and_all_bins(*names: str) -> Callable[[], str | None]:
    """Like :func:`_need_all_bins`, but also requires a Linux host.

    A step whose *script* self-restricts to Linux (its own module docstring,
    not something this precondition duplicates) would otherwise still report
    `passed` on a macOS/Windows host that happens to have every named tool on
    PATH — the precondition would pass, the step would run, and the script's
    own internal platform check would exit 0 immediately having measured
    nothing, which `run_step()` cannot tell apart from genuine work (Codex
    review: this is exactly the gap `--json-out`'s `complete` receipt field
    exists to catch, and a step-level false pass defeats it at the source).
    """
    bins_check = _need_all_bins(*names)

    def check() -> str | None:
        if not sys.platform.startswith("linux"):
            return f"Linux only (host platform is {sys.platform!r})"
        return bins_check()

    return check


def _need_castxml_and_system_zlib() -> str | None:
    """castxml plus the distro zlib that scripts/demo_libz.py dumps."""
    if shutil.which("castxml") is None:
        return "castxml not found on PATH"
    if not Path("/usr/include/zlib.h").is_file():
        return "/usr/include/zlib.h not found (install zlib1g-dev)"
    return None


def _need_platform_and_bins(
    per_platform: dict[str, tuple[str, ...]],
) -> Callable[[], str | None]:
    """Run only on the listed `sys.platform` values, each with its own tools."""

    def check() -> str | None:
        bins = per_platform.get(sys.platform)
        if bins is None:
            return f"needs one of {sorted(per_platform)} (host platform is {sys.platform!r})"
        missing = [b for b in bins if shutil.which(b) is None]
        if missing:
            return f"missing tool(s) on PATH: {', '.join(missing)}"
        return None

    return check


def _need_modules(*names: str) -> Callable[[], str | None]:
    def check() -> str | None:
        missing = [n for n in names if not _module_available(n)]
        if missing:
            return f"module(s) not installed: {', '.join(missing)}"
        return None

    return check
