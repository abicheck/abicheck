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

"""Which C++ standard library a binary was built against: the evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .snapshot import AbiSnapshot

__all__ = ["stdlib_evidence_symbols"]


def stdlib_evidence_symbols(snapshot: AbiSnapshot) -> list[str]:
    """The mangled names that say which C++ standard library a binary was
    built against.

    The binary's own exported symbols when it has a symbol table: the
    ``std::`` instantiations it emits name its runtime (``std::__1::`` for
    libc++, plain ``std::`` for libstdc++). A header-parsed declaration's
    mangling reflects the header parser's standard library instead (castxml
    reads libstdc++ headers for a library built with ``-stdlib=libc++``), so
    declarations are only the fallback for a snapshot without one. Imports
    are not evidence: libc++ itself exports its exception classes in plain
    ``std::`` (``std::logic_error``), which would read as libstdc++.
    """
    elf = snapshot.elf
    if elf is not None and elf.symbols:
        return [s.name for s in elf.symbols]
    decls = snapshot.declarations
    return [f.mangled for f in decls.functions if f.mangled] + [
        v.mangled for v in decls.variables if v.mangled
    ]
