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

"""The time64 detector follows typedef chains, whichever backend spelled them.

glibc spells ``time_t`` as an alias chain (``time_t`` -> ``__time_t`` ->
``long`` on 32-bit without ``_TIME_BITS``, and ``time_t`` -> ``__time64_t`` ->
``__int64_t`` with it). GCC's header backend records the flattened underlying
type; clang's records each link. The oracle is the expected ChangeKind under
the flattened spelling, which the detector already handled, so the chained
spelling must reach the same finding.
"""

from __future__ import annotations

import pytest

from abicheck.diff_time64 import _diff_time64_abi, _fundamental_of
from abicheck.model import AbiSnapshot
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.elf_facts import ElfMetadata


def _snap(typedefs: dict[str, str], *, bits: int = 32) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtime.so",
        version="1",
        elf=ElfMetadata(elf_class=bits, pointer_size=bits // 8),
        typedefs=typedefs,
    )


# Each row is the (old, new) underlying types for ``time_t`` as one backend
# spells them. The flattened and chained spellings must agree.
_FLAT = ({"time_t": "long"}, {"time_t": "long long"})
_CHAINED = (
    {"time_t": "__time_t", "__time_t": "long"},
    {"time_t": "__time64_t", "__time64_t": "__int64_t"},
)


@pytest.mark.parametrize("spelling", [_FLAT, _CHAINED], ids=["flat", "chained"])
def test_time_t_widening_is_one_time64_finding_in_either_spelling(
    spelling: tuple[dict[str, str], dict[str, str]],
) -> None:
    old_t, new_t = spelling
    # The public surface must reference ``time_t`` for the finding to count.
    old = _snap(old_t)
    new = _snap(new_t)
    _use_time_t_in_signature(old)
    _use_time_t_in_signature(new)
    changes = _diff_time64_abi(old, new)
    assert [c.kind for c in changes] == [ChangeKind.TIME64_ABI_CHANGED]


@pytest.mark.parametrize(
    ("chain", "expected"),
    [
        ({"time_t": "__time_t", "__time_t": "long"}, "long"),
        ({"time_t": "__time64_t", "__time64_t": "__int64_t"}, "__int64_t"),
        ({"time_t": "long long"}, "long long"),
        ({"time_t": "long"}, "long"),
    ],
)
def test_fundamental_is_reached_through_every_link(
    chain: dict[str, str], expected: str
) -> None:
    assert _fundamental_of("time_t", chain) == expected


def test_a_cyclic_alias_terminates_and_resolves_to_no_fundamental() -> None:
    # A typedef cycle must not loop; the chain stops at a bounded depth.
    cyclic = {"a": "b", "b": "a"}
    assert _fundamental_of("a", cyclic) in {"a", "b"}


def test_a_64bit_target_keeps_the_time_t_family_silent() -> None:
    # On a 64-bit target both spellings are already 64-bit: no flip.
    old = _snap({"time_t": "__time_t", "__time_t": "long"}, bits=64)
    new = _snap({"time_t": "__time64_t", "__time64_t": "__int64_t"}, bits=64)
    _use_time_t_in_signature(old)
    _use_time_t_in_signature(new)
    assert _diff_time64_abi(old, new) == []


def _use_time_t_in_signature(snap: AbiSnapshot) -> None:
    """Make ``time_t`` reachable from the public surface of *snap*."""
    from abicheck.model import Function, Param, ParamKind, Visibility

    snap.declarations.functions = [
        Function(
            name="when_next",
            mangled="when_next",
            return_type="time_t",
            params=[Param(name="t", type="time_t", kind=ParamKind.VALUE)],
            visibility=Visibility.PUBLIC,
        )
    ]
