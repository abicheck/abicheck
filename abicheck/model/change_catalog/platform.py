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


"""ADR-061 D9 taxonomy: platform/binary-format-level ChangeKind entries.

ELF/PE/Mach-O container facts, DWARF/debug-info presence, symbol-table
*representation* (binding, ELF visibility, versioning), hardening flags
(RELRO, stack canary, CET/BTI, PIE), toolchain-mode ABI traits
(exceptions/RTTI/TLS model, calling convention, vector ABI), symbol
versioning, kernel ABI (kABI) facts, and the SYCL plugin-interface ABI --
everything that is a fact about the *binary artifact's* format or the
platform ABI it targets, as opposed to a fact about the source-level
declaration that produced it.

Categorized by which detector module actually produces each kind (verified
against the real ``ChangeKind.X`` construction sites in ``diff_platform.py``
and its siblings -- ``diff_platform_elf_dynamic.py``,
``diff_platform_elf_symbols.py``, ``diff_versioning.py``,
``versioned_symbol_scheme.py``, ``diff_kabi.py``, ``diff_sycl.py``,
``stack_binding_diff.py`` -- not by which flat ``change_registry_*.py``
sibling an entry happened to live in for pure line-count reasons before
this migration.
"""

from __future__ import annotations

from .platform_1 import PLATFORM_ENTRIES_1
from .platform_2 import PLATFORM_ENTRIES_2
from .registry import ChangeKindMeta

#: Assembled from the numbered parts above; split purely to keep each
#: file under ADR-061's 800-line ceiling, exactly as
#: ``kind_names_{1,2,3}.py`` already is. The public name is unchanged.
PLATFORM_ENTRIES: list[ChangeKindMeta] = PLATFORM_ENTRIES_1 + PLATFORM_ENTRIES_2

__all__ = ["PLATFORM_ENTRIES"]
