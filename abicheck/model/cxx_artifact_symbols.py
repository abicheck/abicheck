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

"""Recognising a compiler-emitted C++ class artifact by its symbol name.

Its own leaf module rather than part of ``mangled_name.py``, which is
already at the new-file line ceiling: this is a small, self-contained
predicate over a symbol *spelling*, with no dependency on that module's
Itanium component parser.

Two consumers must agree on this answer or they contradict each other --
see :func:`is_cxx_class_artifact_symbol`.
"""

from __future__ import annotations

import re

#: Compiler-generated C++ ABI artifacts that belong to a *class* rather than
#: to a free function or variable: Itanium vtables, typeinfo, typeinfo names,
#: VTTs, construction vtables and thunks, plus MSVC's ``??_`` vftable/vbtable/
#: RTTI/deleting-destructor names.
CXX_ARTIFACT_PREFIXES: tuple[str, ...] = (
    "_ZTV",
    "_ZTI",
    "_ZTS",
    "_ZTT",
    "_ZTC",
    "_ZTh",
    "_ZTv",
    "_ZTc",
    "??_",
)

#: Itanium constructor (``C1``/``C2``/``C3``) and destructor
#: (``D0``/``D1``/``D2``) encodings.
_STRUCTOR_RE = re.compile(r"_ZN.*?[CD][0-4]E")


def is_cxx_class_artifact_symbol(symbol: str) -> bool:
    """Whether *symbol* is a compiler-emitted artifact of a C++ class.

    Constructors/destructors (which a header backend routinely leaves
    unmangled, so they never match a class's own parsed declarations) and
    the vtable/typeinfo/VTT/thunk family, which a header backend records as
    a ``RecordType`` and never as a ``Function``/``Variable``.

    The one shared answer, because two consumers must agree on it or they
    contradict each other. ``exported_not_public`` exempts these symbols --
    a class artifact is not an *undocumented* export, its documentation is
    its class's declaration. Public-surface scoping
    (``policy.public_surface_closure._seed_undeclared_exports``) must make
    the same exemption for the same reason: treating ``_ZTVN3foo3BarE`` as
    provably-undocumented demoted a real vtable loss on a publicly declared
    class, so the identical binary pair reported BREAKING without ``-H`` and
    clean with it (caught by
    ``tests/test_export_reconciliation_and_obligations.py``, which asserts
    exactly that adding headers never subtracts a break).
    """
    if symbol.startswith(CXX_ARTIFACT_PREFIXES):
        return True
    if symbol.startswith("_ZN") and _STRUCTOR_RE.match(symbol):
        return True
    return symbol.startswith(("??0", "??1"))
