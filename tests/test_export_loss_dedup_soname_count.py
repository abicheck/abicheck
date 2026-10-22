# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit oracles for the two halves of the duplicate-export-loss fix.

* ``deduplicate_cross_detector`` drops an ELF-only removal whose symbol a
  visibility finding already reports, and nothing else.
* ``check_soname_bump_policy`` counts distinct breaking symbols, so two
  findings on one symbol cannot inflate the advisory.

Expected values are written out by hand. End-to-end coverage over real
binaries lives in ``test_single_symbol_removal_invariant.py``.
"""

from __future__ import annotations

import pytest

from abicheck.compare.finding_dedup import deduplicate_cross_detector
from abicheck.elf_metadata import ElfMetadata
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind as K
from abicheck.policy.soname_bump_policy import check_soname_bump_policy


def _c(kind: K, symbol: str) -> Change:
    return Change(kind=kind, symbol=symbol, description=f"{kind.value}: {symbol}")


@pytest.mark.parametrize(
    ("changes", "expected_kinds"),
    [
        pytest.param(
            [
                _c(K.FUNC_VISIBILITY_CHANGED, "add"),
                _c(K.FUNC_REMOVED_ELF_ONLY, "add"),
            ],
            [K.FUNC_VISIBILITY_CHANGED],
            id="func-visibility-then-elf-only-same-symbol",
        ),
        pytest.param(
            [
                _c(K.VAR_VISIBILITY_CHANGED, "counter"),
                _c(K.VAR_REMOVED_ELF_ONLY, "counter"),
            ],
            [K.VAR_VISIBILITY_CHANGED],
            id="var-visibility-then-elf-only-same-symbol",
        ),
        pytest.param(
            [
                _c(K.FUNC_VISIBILITY_CHANGED, "add"),
                _c(K.FUNC_REMOVED_ELF_ONLY, "sub"),
            ],
            [K.FUNC_VISIBILITY_CHANGED, K.FUNC_REMOVED_ELF_ONLY],
            id="different-symbols-both-kept",
        ),
        pytest.param(
            [
                _c(K.VAR_VISIBILITY_CHANGED, "add"),
                _c(K.FUNC_REMOVED_ELF_ONLY, "add"),
            ],
            [K.VAR_VISIBILITY_CHANGED, K.FUNC_REMOVED_ELF_ONLY],
            id="variable-visibility-never-covers-function-removal",
        ),
        pytest.param(
            [_c(K.FUNC_REMOVED_ELF_ONLY, "add")],
            [K.FUNC_REMOVED_ELF_ONLY],
            id="elf-only-alone-kept",
        ),
        pytest.param(
            [_c(K.FUNC_VISIBILITY_CHANGED, "add")],
            [K.FUNC_VISIBILITY_CHANGED],
            id="visibility-alone-kept",
        ),
    ],
)
def test_elf_only_removal_dropped_only_when_visibility_covers_it(
    changes: list[Change], expected_kinds: list[K]
) -> None:
    kept = deduplicate_cross_detector(changes)
    assert [c.kind for c in kept] == expected_kinds


def _soname_advisories(changes: list[Change]) -> list[str]:
    old = ElfMetadata(soname="libx.so.1")
    new = ElfMetadata(soname="libx.so.1")
    result = check_soname_bump_policy(changes, old, new)
    return [c.description for c in result if c.kind is K.SONAME_BUMP_RECOMMENDED]


@pytest.mark.parametrize(
    ("changes", "expected_count"),
    [
        pytest.param(
            [
                _c(K.FUNC_VISIBILITY_CHANGED, "add"),
                _c(K.FUNC_REMOVED_ELF_ONLY, "add"),
            ],
            1,
            id="two-findings-one-symbol-counts-once",
        ),
        pytest.param(
            [
                _c(K.FUNC_REMOVED, "add"),
                _c(K.FUNC_REMOVED, "sub"),
            ],
            2,
            id="two-symbols-count-twice",
        ),
        pytest.param(
            [
                _c(K.FUNC_PARAMS_CHANGED, "add"),
                _c(K.FUNC_RETURN_CHANGED, "add"),
                _c(K.FUNC_REMOVED, "sub"),
            ],
            2,
            id="several-changes-on-one-function-count-as-one-symbol",
        ),
        pytest.param(
            [
                _c(K.FUNC_REMOVED_ELF_ONLY, "add"),
                _c(K.EXPORTED_NOT_PUBLIC, "add"),
            ],
            1,
            id="non-breaking-finding-on-same-symbol-not-counted",
        ),
        pytest.param(
            [_c(K.FUNC_REMOVED, ""), _c(K.FUNC_REMOVED, "")],
            2,
            id="symbol-less-changes-each-count",
        ),
    ],
)
def test_soname_advisory_counts_distinct_breaking_symbols(
    changes: list[Change], expected_count: int
) -> None:
    advisories = _soname_advisories(changes)
    assert len(advisories) == 1
    assert advisories[0].startswith(f"{expected_count} binary-incompatible change(s)")
