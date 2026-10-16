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

"""Python-only twin of ``test_export_loss_binding_invariant.py``.

Same contract (see that module's docstring), exercised on constructed
snapshots and the detector entry points directly, so it runs in the fast
lane without a compiler. Covers each emitter path the integration guard uses:
the undeclared ELF-only removal, the template-instantiation loss, the
internal-namespace template leak, and the instantiation-missing finding.
"""

from __future__ import annotations

import pytest

from abicheck.compare.undeclared_exports import _diff_undeclared_exports
from abicheck.diff_templates import (
    detect_internal_template_leaks,
    detect_missing_instantiations,
)
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot
from abicheck.model.change import Change
from abicheck.model.declarations import Function, Visibility
from abicheck.model.elf_facts import SymbolBinding, SymbolType
from abicheck.policy.selectors import EXPORT_LOSS_KINDS
from tests._export_loss_binding import assert_binding_matches_old_elf, is_export_loss

BINDINGS = [SymbolBinding.WEAK, SymbolBinding.GLOBAL, SymbolBinding.UNIQUE]

# The kinds the issue names, plus the ELF-only sibling of each removal and the
# deleted-from-ELF fallback. Pinned so a removal from the set is a visible edit.
_EXPECTED_MEMBERS = {
    "func_removed",
    "func_removed_elf_only",
    "func_deleted_elf_fallback",
    "var_removed",
    "var_removed_elf_only",
    "func_visibility_changed",
    "var_visibility_changed",
    "instantiation_missing_from_binary",
    "internal_template_leaks_via_public_api",
}


def test_export_loss_kinds_membership_is_pinned() -> None:
    assert {k.value for k in EXPORT_LOSS_KINDS} == _EXPECTED_MEMBERS


def _elf(*symbols: tuple[str, SymbolBinding, SymbolType]) -> ElfMetadata:
    return ElfMetadata(
        machine="x86-64",
        symbols=[ElfSymbol(name=n, binding=b, sym_type=t) for n, b, t in symbols],
    )


def _snap(
    funcs: list[Function], elf: ElfMetadata | None = None, *, from_headers: bool = True
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libx.so",
        version="1",
        functions=funcs,
        elf=elf,
        from_headers=from_headers,
    )


def _fn(name: str, binding: SymbolBinding | None) -> Function:
    # Non-Itanium mangled names keep identity on the declared name on every
    # lane, the same reason test_template_finding_binding.py uses them.
    return Function(
        name=name,
        mangled=f"sym:{name}",
        return_type="void",
        visibility=Visibility.PUBLIC,
        elf_binding=binding,
    )


def _check(change: Change, old_elf: ElfMetadata) -> None:
    assert is_export_loss(change.kind.value), change.kind
    names = [change.symbol] + [r.mangled for r in (change.removed_instantiations or [])]
    assert_binding_matches_old_elf(
        change.kind.value,
        change.symbol,
        change.symbol_binding,
        names,
        old_elf,
    )


@pytest.mark.parametrize("binding", BINDINGS)
def test_undeclared_removal_matches_old_elf_binding(binding: SymbolBinding) -> None:
    name = "_ZN2ns15weak_undeclaredEv"
    old_elf = _elf((name, binding, SymbolType.FUNC))
    old = _snap([], old_elf, from_headers=True)
    new = _snap([], _elf(), from_headers=True)
    changes = _diff_undeclared_exports(old, new)
    assert changes, "removal not reported"
    for change in changes:
        _check(change, old_elf)


def test_template_leak_aggregate_matches_old_elf_for_every_weak_removed_set() -> None:
    # Every removed instantiation is WEAK in the old ELF table: the leak finding
    # must say "weak", and the contract holds over the whole removed set.
    stem = "ns::detail::walk"
    removed = ["int", "float", "double"]
    survivor = _fn(f"{stem}<int>", SymbolBinding.GLOBAL)
    old_funcs = [survivor] + [
        _fn(f"{stem}<{a}>", SymbolBinding.WEAK) for a in removed[1:]
    ]
    old_elf = _elf(
        *[
            (f"sym:{stem}<{a}>", SymbolBinding.WEAK, SymbolType.FUNC)
            for a in removed[1:]
        ],
        (f"sym:{stem}<int>", SymbolBinding.GLOBAL, SymbolType.FUNC),
    )
    old = _snap(old_funcs, old_elf)
    new = _snap([_fn(f"{stem}<int>", SymbolBinding.GLOBAL)], _elf())
    changes = detect_internal_template_leaks(old, new)
    assert len(changes) == 1
    _check(changes[0], old_elf)
    assert changes[0].symbol_binding == "weak"


@pytest.mark.parametrize("dropped", BINDINGS)
def test_instantiation_missing_matches_old_elf_binding(dropped: SymbolBinding) -> None:
    stem = "ns::detail::descriptor"
    old_elf = _elf(
        (f"sym:{stem}<float>::get", SymbolBinding.GLOBAL, SymbolType.FUNC),
        (f"sym:{stem}<double>::get", dropped, SymbolType.FUNC),
    )
    old = _snap(
        [
            _fn(f"{stem}<float>::get", SymbolBinding.GLOBAL),
            _fn(f"{stem}<double>::get", dropped),
        ],
        old_elf,
    )
    new = _snap([_fn(f"{stem}<float>::get", SymbolBinding.GLOBAL)], _elf())
    changes = detect_missing_instantiations(old, new)
    assert changes, "instantiation loss not reported"
    for change in changes:
        _check(change, old_elf)
