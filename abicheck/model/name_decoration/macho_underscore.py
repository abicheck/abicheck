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

"""Mach-O's leading-underscore decoration codec.

Darwin's linker prepends one ``_`` to every global symbol. For an Itanium
name the decorated form ``__Z...`` is unambiguous on its own (no real
Itanium mangling begins with two underscores), so :func:`decode_itanium`
needs no further evidence. For a C-linkage name ``_foo`` is
indistinguishable from a real, distinct ``asm("_foo")`` label, so
:func:`decode_c` is only correct for a caller that already knows the
declaration is ``extern "C"`` on a Darwin target.

**Known gap** (carried from the strip this module replaced): the
"unambiguous" claim holds for a *compiler-produced* name, not for an explicit
GNU ``asm("__Zfake")`` label, which clang reports verbatim. A caller joining
against a symbol table should try the exact spelling first
(``buildsource.template_graph._resolve_emitted_symbol`` does).
"""

from __future__ import annotations

__all__ = ["decode_c", "decode_itanium", "encode", "shifted_spellings"]


def encode(name: str) -> str:
    """The Darwin linker spelling of *name*: one leading ``_``."""
    return "_" + name


def decode_itanium(symbol: str) -> str:
    """``__Z...`` -> ``_Z...``; every other spelling is returned unchanged
    (an undecorated Itanium name, a C name, anything else)."""
    return symbol[1:] if symbol.startswith("__Z") else symbol


def decode_c(symbol: str) -> str | None:
    """The C name a Darwin-decorated ``_name`` spells, or ``None`` when
    *symbol* carries no leading ``_``. Only valid for a known
    ``extern "C"`` declaration on a Darwin target (see the module
    docstring)."""
    return symbol[1:] if symbol.startswith("_") and len(symbol) > 1 else None


def shifted_spellings(spelling: str) -> tuple[str, ...]:
    """The two spellings one Mach-O decoration step away from *spelling*:
    one leading ``_`` fewer (its :func:`decode_c`) and one more (its
    :func:`encode`). A join that cannot tell which side the trie or the
    producer already stripped tries both, refusing any spelling another
    declaration owns exactly."""
    shorter = decode_c(spelling)
    return tuple(c for c in (shorter, encode(spelling)) if c)
