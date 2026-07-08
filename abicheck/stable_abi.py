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

Refresh the data by re-running the extraction over a newer
``Misc/stable_abi.toml`` (see :mod:`abicheck.stable_abi_data`).
"""

from __future__ import annotations

from enum import Enum

from .stable_abi_data import STABLE_ABI_SYMBOLS

#: Prefix that marks a symbol as CPython private/internal API *unless* it is in
#: the authoritative stable set (the ``abi_only`` symbols the Limited-API macros
#: route to are ``_Py``-prefixed but stable — see module docstring).
_PRIVATE_PREFIXES: tuple[str, ...] = ("_Py",)

#: Prefixes that mark a symbol as belonging to the CPython C-API surface at all
#: (public or private). Anything not matching is "not CPython" and ignored by
#: the stable-ABI check (e.g. libc, libstdc++, or the module's own helpers).
_CPYTHON_PREFIXES: tuple[str, ...] = ("Py", "_Py")

#: Symbol -> ``(major, minor)`` release it entered the Limited API. Alias of the
#: authoritative vendored table; kept as a module name for back-compat.
LIMITED_API_ADDED: dict[str, tuple[int, int]] = STABLE_ABI_SYMBOLS


class StableAbiStatus(str, Enum):
    """Classification of one imported CPython symbol against the Limited API."""

    #: In the stable-ABI set and at/under the target floor.
    STABLE = "stable"
    #: CPython private/internal API (``_Py*`` not in the stable set) — a violation.
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
    """True if *name* is CPython private/internal API — never part of the ABI.

    A ``_Py``-prefixed symbol is private ONLY when it is absent from the
    authoritative stable set: the ``abi_only`` symbols the Limited-API macros
    route to (``_Py_Dealloc``, ``_PyObject_GC_New``, ``_PyArg_*_SizeT``,
    ``_Py_NoneStruct``, …) are ``_Py``-prefixed but stable, so they are not
    private.
    """
    return name.startswith(_PRIVATE_PREFIXES) and name not in STABLE_ABI_SYMBOLS


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


def min_required_abi3(names: list[str]) -> tuple[int, int] | None:
    """Minimum Limited-API floor implied by *names*.

    The highest ``added_version`` among the stable imports: an ``abi3`` module
    can only load on interpreters at or above this. Returns ``None`` when no
    import is a stable symbol (nothing to floor on). Private imports are not
    floors — they are outright violations — so they do not participate here.
    """
    floors = [
        v
        for n in names
        if not is_private_symbol(n)
        for v in (STABLE_ABI_SYMBOLS.get(n),)
        if v is not None
    ]
    return max(floors) if floors else None


def format_version(v: tuple[int, int]) -> str:
    """Render a ``(major, minor)`` version tuple as ``"3.9"``."""
    return f"{v[0]}.{v[1]}"


def parse_abi3_version(text: str) -> tuple[int, int] | None:
    """Parse an ``--abi3`` argument like ``"3.9"`` / ``"3"`` into a tuple.

    Returns ``None`` when *text* is not a recognisable ``major[.minor]`` string.

    The bare-major form ``"3"`` is the documented ``Py_LIMITED_API=3`` spelling,
    which CPython treats as the **3.2** Stable-ABI baseline (the Limited API did
    not exist before 3.2). We therefore normalise ``3`` — and any ``3.0``/``3.1``
    — to ``(3, 2)`` so ordinary stable symbols (``PyList_New`` etc., floor 3.2)
    are not wrongly reported as above-floor.
    """
    parts = text.strip().split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return None
    if major == 3 and minor < 2:
        # Py_LIMITED_API=3 (or 3.0/3.1) → the 3.2 Limited-API baseline.
        minor = 2
    return (major, minor)
