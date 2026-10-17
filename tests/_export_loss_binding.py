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

"""Shared oracle for the export-loss binding contract (non-test helper).

Used by ``test_export_loss_binding_invariant.py`` (real binaries, JSON reports)
and ``test_export_loss_binding_unit.py`` (constructed snapshots, ``Change``
objects). The contract is stated against the old library's ELF table, not
against the emitter that built the finding.
"""

from __future__ import annotations

from collections.abc import Iterable

from abicheck.elf_metadata import ElfMetadata
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.policy.selectors import EXPORT_LOSS_KINDS


def is_export_loss(kind_value: str) -> bool:
    return ChangeKind(kind_value) in EXPORT_LOSS_KINDS


def names_for_finding(symbol: str, removed_mangled: Iterable[str] = ()) -> list[str]:
    """The old-side ELF names a finding is about.

    A finding that lists removed instantiations (``internal_template_leaks_via_public_api``)
    is about those mangled names: its ``symbol`` is the template stem, which is not
    an ELF symbol, so looking it up would skip the check and pass vacuously.
    Every other finding is about its own symbol.
    """
    listed = list(removed_mangled)
    return listed if listed else [symbol]


def assert_binding_matches_old_elf(
    kind: str,
    symbol: str,
    symbol_binding: str | None,
    names: Iterable[str],
    old_elf: ElfMetadata,
) -> bool:
    """Assert one export-loss finding's binding against the old ELF table.

    *names* are the old-side mangled names the finding is about: its own symbol
    when that is a mangled name, plus each removed instantiation. When every
    name has an unambiguous entry, *symbol_binding* must be one of those
    entries' bindings (a mixed removed set reports the strongest, which is
    still one of them). Otherwise *symbol_binding* may be ``None``.

    Returns True when the contract applied (every name was determinable).
    """
    symbols = [old_elf.unambiguous_symbol(n) for n in names]
    if any(sym is None for sym in symbols):
        return False
    bindings = {sym.binding.value for sym in symbols if sym is not None}
    assert symbol_binding in bindings, (kind, symbol, symbol_binding, sorted(bindings))
    return True
