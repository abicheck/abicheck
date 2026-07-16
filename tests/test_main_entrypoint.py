# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Tests for the ``python -m abicheck`` entry point (abicheck/__main__.py)."""

from __future__ import annotations

import runpy
import subprocess
import sys

import pytest


def test_run_as_cli_module_registers_sibling_commands() -> None:
    """``python -m abicheck.cli`` (distinct from the documented ``python -m
    abicheck``, but a common thing to type) must register every
    sibling-module command, not just the ones defined directly in cli.py.

    Every sibling module (cli_scan, cli_buildsource, ...) does ``from .cli
    import main`` for its ``@main.command(...)`` decorators. When cli.py
    itself runs as ``__main__``, that relative import used to re-execute
    cli.py a SECOND time under the real ``abicheck.cli`` sys.modules key,
    producing a second, empty Click group that every decorator attached to
    instead of the one actually running — so ``--help`` silently omitted
    scan/appcompat/... and only showed dump/compare/compat (defined directly
    in cli.py). A real subprocess invocation is used (rather than sys.modules
    manipulation in-process) because the bug is specifically about which
    sys.modules key cli.py's own execution lands under, which an in-process
    trick would not faithfully reproduce (Codex review).
    """
    result = subprocess.run(
        [sys.executable, "-m", "abicheck.cli", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    for cmd in ("scan", "appcompat"):
        assert cmd in result.stdout, (
            f"'{cmd}' missing from `python -m abicheck.cli --help` output"
        )


def test_main_module_reexports_main() -> None:
    import abicheck.__main__ as entry
    from abicheck.cli import main

    assert entry.main is main


def test_run_as_module_invokes_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """Running the module as ``__main__`` calls the Click group.

    ``--help`` makes Click exit cleanly with SystemExit(0), which exercises
    the ``if __name__ == "__main__": main()`` guard.
    """
    monkeypatch.setattr(sys, "argv", ["abicheck", "--help"])
    # Drop the cached submodule so run_module executes it fresh as __main__
    # without the "found in sys.modules" RuntimeWarning.
    monkeypatch.delitem(sys.modules, "abicheck.__main__", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("abicheck.__main__", run_name="__main__")
    assert exc_info.value.code == 0
