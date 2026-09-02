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

"""CPython Limited-API / ``abi3`` stable-ABI classification (G14).

A CPython extension module (Cython, pybind11, or a hand-written C extension)
is a shared library whose *real* compatibility contract is not its export
table — that is essentially just ``PyInit_<mod>`` — but the set of CPython
C-API symbols it **imports** from ``libpython``. An ``abi3`` wheel promises it
uses only the **stable** subset of that API (``Py_LIMITED_API``), so one binary
runs on every interpreter at or above its declared floor. If the module imports
a symbol outside the stable set (or one newer than its declared floor), it fails
to load on an older interpreter with an ``undefined symbol`` error — a break the
export-table view is blind to.

Classification is driven by the **authoritative** vendored membership set
:data:`~abicheck.stable_abi_data.STABLE_ABI_SYMBOLS` (generated from CPython's
``Misc/stable_abi.toml``), NOT a name-prefix heuristic. This matters because the
Limited-API headers deliberately route public macros to underscore-prefixed
``abi_only`` symbols — ``Py_DECREF`` → ``_Py_Dealloc``, ``PyObject_GC_New`` →
``_PyObject_GC_New``, ``PyArg_ParseTuple`` (with ``PY_SSIZE_T_CLEAN``) →
``_PyArg_ParseTuple_SizeT``, ``Py_None`` → ``&_Py_NoneStruct`` — so a ``_Py``
prefix does NOT imply private. Membership decides:

* a symbol **in** the set is stable (at its recorded floor);
* a ``_Py``-prefixed symbol **not** in the set is genuinely internal/private —
  a definite ``abi3`` violation (the module reached outside the Limited API);
* a public ``Py``-prefixed symbol **not** in the set is
  :data:`StableAbiStatus.UNKNOWN` (advisory — likely a newer symbol than the
  vendored data, or a typo), never a hard verdict.

Refresh the data by running ``scripts/gen_stable_abi_data.py`` over a newer
``Misc/stable_abi.toml`` (see :mod:`abicheck.stable_abi_data`).
"""

from __future__ import annotations

from enum import Enum

# The value parser moved to `model/abi3_version.py` (Codex review, PR #994):
# classifying this whole module `model` so a frontend could reach it would
# have dragged `classify`/`is_private_symbol` -- real compatibility policy --
# into the innermost ring. Re-exported so every existing caller is unaffected.
from .model.abi3_version import (
    _MAX_ABI3_MINOR as _MAX_ABI3_MINOR,
    format_version as format_version,
    parse_abi3_version as parse_abi3_version,
)
from .stable_abi_data import STABLE_ABI_SYMBOLS

#: Prefixes that mark a symbol as outside the stable ABI *unless* it is in the
#: authoritative stable set. ``_Py`` is CPython's private/internal C-API (the
#: ``abi_only`` symbols the Limited-API macros route to are ``_Py``-prefixed but
#: appear in the stable set, so membership wins). ``PyUnstable`` is CPython's
#: explicitly *unstable* API tier (PEP 689) — public, but documented as
#: changeable every minor release, so it is never part of the Limited API and an
#: ``abi3`` module importing it is a definite violation.
_PRIVATE_PREFIXES: tuple[str, ...] = ("_Py", "PyUnstable")

#: Prefixes that mark a symbol as belonging to the CPython C-API surface at all
#: (public or private). Anything not matching is "not CPython" and ignored by
#: the stable-ABI check (e.g. libc, libstdc++, or the module's own helpers).
_CPYTHON_PREFIXES: tuple[str, ...] = ("Py", "_Py")

#: Symbol -> ``(major, minor)`` release it entered the Limited API. Alias of the
#: authoritative vendored table; kept as a module name for back-compat.
LIMITED_API_ADDED: dict[str, tuple[int, int]] = STABLE_ABI_SYMBOLS

#: Newest CPython 3.x minor the vendored stable-ABI data tracks. Derived from the
#: data so it advances automatically on a refresh.
_MAX_KNOWN_MINOR: int = max(minor for _major, minor in STABLE_ABI_SYMBOLS.values())


class StableAbiStatus(str, Enum):
    """Classification of one imported CPython symbol against the Limited API."""

    #: In the stable-ABI set and at/under the target floor.
    STABLE = "stable"
    #: Outside the stable ABI by construction — a violation: CPython private
    #: (``_Py*`` not in the stable set) or unstable (``PyUnstable_*``) API.
    PRIVATE = "private"
    #: Recognised stable symbol, but newer than the declared/target floor.
    ABOVE_FLOOR = "above_floor"
    #: Public ``Py*`` symbol not in the vendored set — advisory only.
    UNKNOWN = "unknown"
    #: Not a CPython C-API symbol at all (libc, C++ runtime, module-local, …).
    NOT_CPYTHON = "not_cpython"


def is_cpython_symbol(name: str) -> bool:
    """True if *name* looks like a CPython C-API symbol (public or private)."""
    return name.startswith(_CPYTHON_PREFIXES)


def is_private_symbol(name: str) -> bool:
    """True if *name* is outside the stable ABI by construction — a violation.

    Covers CPython *private* (``_Py*``) and *unstable* (``PyUnstable_*``, PEP
    689) API. A ``_Py``-prefixed symbol counts ONLY when absent from the
    authoritative stable set: the ``abi_only`` symbols the Limited-API macros
    route to (``_Py_Dealloc``, ``_PyObject_GC_New``, ``_PyArg_*_SizeT``,
    ``_Py_NoneStruct``, …) are ``_Py``-prefixed but stable, so they are not
    flagged. ``PyUnstable_*`` is never in the stable set.
    """
    return name.startswith(_PRIVATE_PREFIXES) and name not in STABLE_ABI_SYMBOLS


def is_nonstable_cpython_import(name: str) -> bool:
    """True if *name* is a CPython symbol that is NOT in the Stable ABI.

    The vendored set is authoritative for CPython ≤ its data version, so a
    CPython symbol absent from it is outside the Limited API — whether an
    internal ``_Py*``/``PyUnstable_*`` symbol or a public ``Py*`` function that
    was simply never added to the Limited API (e.g. ``PyUnicode_AsUTF8``). For an
    ``abi3`` module all of these are violations. (A symbol newer than the
    vendored CPython release is the one benign case — refresh the data.)
    """
    return is_cpython_symbol(name) and name not in STABLE_ABI_SYMBOLS


def classify(
    name: str, abi3_floor: tuple[int, int] | None = None
) -> tuple[StableAbiStatus, tuple[int, int] | None]:
    """Classify one imported symbol against the stable ABI.

    Returns ``(status, added_version)``. ``added_version`` is the release the
    symbol entered the Limited API when it is a stable symbol, else ``None``.

    ``abi3_floor`` is the module's declared / target ``Py_LIMITED_API`` version
    as ``(major, minor)``. When given, a stable symbol newer than the floor is
    reported :data:`StableAbiStatus.ABOVE_FLOOR` (it would be missing on an
    interpreter at the floor).
    """
    if not is_cpython_symbol(name):
        return StableAbiStatus.NOT_CPYTHON, None
    added = STABLE_ABI_SYMBOLS.get(name)
    if added is None:
        # Not a stable symbol: a `_Py*` name is genuinely private (violation);
        # a public `Py*` name is unknown-to-the-vendored-data (advisory).
        if name.startswith(_PRIVATE_PREFIXES):
            return StableAbiStatus.PRIVATE, None
        return StableAbiStatus.UNKNOWN, None
    if abi3_floor is not None and added > abi3_floor:
        return StableAbiStatus.ABOVE_FLOOR, added
    return StableAbiStatus.STABLE, added


