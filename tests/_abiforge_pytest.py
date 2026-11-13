"""pytest glue for abiforge: the command-line spellings of its toolchain selection.

abiforge itself never imports pytest (``tests/test_abiforge_boundaries.py``);
this module registers ``--edit-corpus-toolchain`` and
``--edit-corpus-consumer-toolchain`` from ``tests/conftest.py`` and exports
them as the environment variables abiforge reads.
"""

from __future__ import annotations

import os

import pytest

#: The environment variables abiforge reads (``abiforge.run.toolchains``).
#: Spelled here so registering the options needs no abiforge import: the
#: wheel smoke lane runs this conftest without the repository root on
#: ``sys.path``. ``tests/test_abiforge_boundaries.py`` pins them to abiforge's.
TOOLCHAIN_ENV = "ABICHECK_EDIT_CORPUS_TOOLCHAIN"
CONSUMER_TOOLCHAIN_ENV = "ABICHECK_EDIT_CORPUS_CONSUMER_TOOLCHAIN"

_OPTIONS = {
    "--edit-corpus-toolchain": TOOLCHAIN_ENV,
    "--edit-corpus-consumer-toolchain": CONSUMER_TOOLCHAIN_ENV,
}


def add_pytest_options(parser: object) -> None:
    """Register the CLI spellings of the two selection variables (conftest)."""
    for opt, var in _OPTIONS.items():
        parser.addoption(  # type: ignore[attr-defined]
            opt,
            default=None,
            metavar="NAMES",
            help=f"Edit corpus: comma-separated toolchains (gcc-14,clang-19,gcc); sets {var}.",
        )


def apply_pytest_options(config: object) -> None:
    """Export the options as the env variables, validated, before collection.

    Runs in every xdist worker too, so workers see the same selection. A
    malformed name fails the session here, not at the first generated cell.
    """
    for opt, var in _OPTIONS.items():
        value = config.getoption(opt, default=None)  # type: ignore[attr-defined]
        if value is not None:
            from abiforge.run.toolchains import (
                ToolchainSelectionError,
                discover,
                select,
            )

            try:
                # Names *and* installation: a missing compiler fails the session.
                select(value, discover(), var)
            except ToolchainSelectionError as exc:
                raise pytest.UsageError(f"{opt}: {exc}") from exc
            os.environ[var] = value
