# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Tests for the ``python -m abicheck`` entry point (abicheck/__main__.py)."""

from __future__ import annotations

import runpy
import sys

import pytest


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
