# SPDX-License-Identifier: Apache-2.0
"""A retained-table hit must still offer the AST cache entry to a derived consumer.

Regression for the clang header-graph attach never warming (or reading) its
projection sidecar: inside an acquisition scope the attach's
``_clang_header_dump`` call is served from the request table, which bypasses
``load_cached_ast`` -- the only place a cache entry used to be offered.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck import dumper_cache
from abicheck.dumper_cache import (
    ast_acquisition_scope,
    run_ast_acquisition_offering_entry,
)
from abicheck.storage.derived_ast import derived_ast_scope

ENTRY = Path("/nonexistent/entry.json")


def _tree() -> tuple[dict[str, object], str, bool]:
    return ({"kind": "TranslationUnitDecl"}, "ctx", True)


@pytest.mark.parametrize("sidecar", [None, "projection"])
def test_retained_hit_offers_entry_with_tree_in_hand(sidecar: str | None) -> None:
    offers: list[tuple[Path, bool]] = []

    def loader(path: Path, *, tree_in_hand: bool = False) -> str | None:
        offers.append((path, tree_in_hand))
        return sidecar

    with ast_acquisition_scope():
        first = run_ast_acquisition_offering_entry("clang", "k", ENTRY, _tree)
        with derived_ast_scope(loader) as art:
            second = run_ast_acquisition_offering_entry(
                "clang", "k", ENTRY, lambda: pytest.fail("producer re-ran")
            )
    assert offers == [(ENTRY, True)]
    assert art.cache_path == ENTRY
    assert second[1:] == first[1:]
    if sidecar is None:
        assert not art.used and second[0] is first[0]
    else:
        assert art.used and art.value == sidecar and second[0] is not first[0]


def test_producer_run_makes_no_second_offer() -> None:
    """When the producer runs, ``load_cached_ast`` owns the offer -- never twice."""
    offers: list[Path] = []

    def loader(path: Path, *, tree_in_hand: bool = False) -> None:
        offers.append(path)

    for scoped in (True, False):
        offers.clear()
        with derived_ast_scope(loader):
            if scoped:
                with ast_acquisition_scope():
                    out = run_ast_acquisition_offering_entry("clang", "k", ENTRY, _tree)
            else:
                assert not dumper_cache.ast_acquisition_active()
                out = run_ast_acquisition_offering_entry("clang", "k", ENTRY, _tree)
        assert offers == [] and out == _tree()


def test_no_derived_scope_returns_retained_tree_unchanged() -> None:
    with ast_acquisition_scope():
        a = run_ast_acquisition_offering_entry("clang", "k", ENTRY, _tree)
        b = run_ast_acquisition_offering_entry("clang", "k", ENTRY, _tree)
    assert a is b


def test_entry_path_can_depend_on_the_result() -> None:
    """clang's C->C++ self-heal caches under the retry key: the offer must
    name the entry the producer actually wrote, which only the result tells."""
    offers: list[Path] = []
    retry_entry = Path("/nonexistent/retry.json")

    def loader(path: Path, *, tree_in_hand: bool = False) -> None:
        offers.append(path)

    def resolve(result: tuple[object, str, bool]) -> Path:
        return retry_entry if result[2] else ENTRY

    with ast_acquisition_scope():
        run_ast_acquisition_offering_entry("clang", "k", resolve, _tree)
        with derived_ast_scope(loader) as art:
            run_ast_acquisition_offering_entry("clang", "k", resolve, _tree)
    assert offers == [retry_entry] and art.cache_path == retry_entry
