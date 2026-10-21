# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the typed compare fold in ``workflows.compare.configuration``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from abicheck.workflows.compare.configuration import (
    fold_compare_collect_inputs,
    split_compile_context_frontend,
)

HEADERS = (Path("a.h"),)
OLD_ONLY = (Path("old.h"),)
NEW_ONLY = (Path("new.h"),)


def _fold(depth: str | None) -> tuple[str, tuple, tuple, tuple]:
    return fold_compare_collect_inputs(
        depth=depth,
        source_method=None,
        headers=HEADERS,
        old_headers_only=OLD_ONLY,
        new_headers_only=NEW_ONLY,
        old_sources=None,
        new_sources=None,
        old_build_info=None,
        new_build_info=None,
    )


def test_binary_depth_clears_every_header_root() -> None:
    _, headers, old_only, new_only = _fold("binary")
    assert (headers, old_only, new_only) == ((), (), ())


@pytest.mark.parametrize("depth", [None, "headers", "full"])
def test_non_binary_depth_keeps_header_roots(depth: str | None) -> None:
    _, headers, old_only, new_only = _fold(depth)
    assert (headers, old_only, new_only) == (HEADERS, OLD_ONLY, NEW_ONLY)


@dataclass(frozen=True)
class _Ctx:
    frontend: str
    includes: tuple[str, ...]


@pytest.mark.parametrize("frontend", ["castxml", "clang", "auto"])
def test_side_context_is_neutralized_and_backend_is_preserved(frontend: str) -> None:
    ctx = _Ctx(frontend=frontend, includes=("-Iinc",))
    backend, side = split_compile_context_frontend(ctx)
    assert backend == frontend
    assert side.frontend == "auto"
    # Only the frontend is neutralized; every other field survives unchanged.
    assert side.includes == ("-Iinc",)
    assert ctx.frontend == frontend
