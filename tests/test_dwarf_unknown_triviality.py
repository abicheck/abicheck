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

"""An aggregate whose definition a unit does not carry has *unknown*
triviality, and unknown is never evidence of a value-ABI change.

Bug class: missing evidence read as a fact. clang writes ``std::string`` as a
declaration-only DIE at ``-O0`` (type homing over libstdc++'s ``extern
template``) and as a full definition at ``-O2``; reading the former as
"trivial" reported ``struct_return_convention_changed`` (BREAKING) on every
function returning ``std::string`` after a rebuild of unchanged sources.

Checked exhaustively over two slots x {trivial, nontrivial, unknown} on each
side, against the oracle "a finding exists iff some slot is known on both
sides and differs; it is the return-convention kind iff that slot is ``ret``".
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.dwarf_advanced import AdvancedDwarfMetadata, diff_advanced_dwarf
from abicheck.extract.dwarf_value_abi_trait import VALUE_ABI_TRAIT_UNKNOWN

_VALUES = ("trivial", "nontrivial", VALUE_ABI_TRAIT_UNKNOWN)
_SLOTS = ("ret", "p0")


def _meta(trait: str) -> AdvancedDwarfMetadata:
    return AdvancedDwarfMetadata(
        has_dwarf=True, target_arch="x86_64", value_abi_traits={"f": trait}
    )


def _trait(values: tuple[str, ...]) -> str:
    return "|".join(f"{s}:{v}" for s, v in zip(_SLOTS, values, strict=True))


@pytest.mark.parametrize(
    ("old", "new"),
    list(itertools.product(itertools.product(_VALUES, repeat=2), repeat=2)),
)
def test_only_a_known_slot_that_differs_is_a_finding(old, new):
    changed = {
        slot
        for slot, a, b in zip(_SLOTS, old, new, strict=True)
        if VALUE_ABI_TRAIT_UNKNOWN not in (a, b) and a != b
    }
    kinds = {
        kind
        for kind, *_ in diff_advanced_dwarf(_meta(_trait(old)), _meta(_trait(new)))
        if kind in ("struct_return_convention_changed", "value_abi_trait_changed")
    }
    if not changed:
        assert kinds == set()
    elif "ret" in changed:
        assert kinds == {"struct_return_convention_changed"}
    else:
        assert kinds == {"value_abi_trait_changed"}
