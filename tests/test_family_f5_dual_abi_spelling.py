# SPDX-License-Identifier: Apache-2.0
"""H5 for the dual-ABI spelling normaliser (family F5): memoised vs reference.

``normalize_dual_abi_spelling`` is an R site (``release.memo``): the release
corpus carries a dual-ABI flip, so the release cell reaches it. This test pins
the memoised answer to the reference-mode answer over sibling spellings.
"""

from __future__ import annotations

import importlib

import pytest
from _family_f5_support import (
    ReferenceMode,
    cache_info_totals,
    clear_functools,
    functools_objects,
    scan_optimization_sites,
)

from tests._family_f5_dual_abi import dual_abi_spellings

_KEY = "abicheck.compare.glibcxx_dual_abi::memoized::normalize_dual_abi_spelling"
_SITES = scan_optimization_sites()


def test_dual_abi_spelling_memo_matches_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sites = [s for s in _SITES if s.key == _KEY]
    assert sites, f"the H5 scan no longer finds {_KEY}"
    importlib.import_module(sites[0].module)
    from abicheck.compare.glibcxx_dual_abi import normalize_dual_abi_spelling

    objs = functools_objects(sites)
    spellings = dual_abi_spellings()
    clear_functools(objs)
    optimized = [normalize_dual_abi_spelling(x) for x in spellings]
    # Repeats must hit the memo: the optimized run really consulted a cache.
    assert sum(h for h, _ in cache_info_totals(objs).values()) > 0
    ref = ReferenceMode()
    ref.install(monkeypatch, sites)
    assert not ref.unpatchable, ref.unpatchable
    reference = [normalize_dual_abi_spelling(x) for x in spellings]
    assert ref.calls.get(_KEY, 0) > 0, "the reference run never bypassed the memo"
    assert optimized == reference, (
        "memoised spelling normalisation differs from reference"
    )
