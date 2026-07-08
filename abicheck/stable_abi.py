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

This module classifies a single imported CPython symbol against the stable-ABI
allowlist. The two signals it uses, in order of confidence:

1. **Private-prefix rule (always correct).** Any symbol beginning with ``_Py``
   (or ``_PyRuntime``) is CPython *private* API. It is never part of the
   Limited API and is not guaranteed stable across minor releases. An ``abi3``
   module importing one is a definite violation. This rule needs no allowlist
   and never goes stale.

2. **Allowlist floor (best-effort).** ``LIMITED_API_ADDED`` maps recognised
   stable symbols to the ``(major, minor)`` release that introduced them into
   the Limited API. It is a *curated, refreshable subset* of CPython's
   canonical ``Doc/data/stable_abi.dat`` — enough to compute a useful minimum
   interpreter floor without over-claiming. A public ``Py*`` symbol that is not
   in the map is reported as :data:`StableAbiStatus.UNKNOWN` (no hard verdict),
   so allowlist staleness produces an advisory, never a false break.

To refresh the allowlist against a CPython source tree::

    # Doc/data/stable_abi.dat rows: 'function','PyList_New','3.2',...
    # keep only rows whose feature is in the Limited API and record the version.

Only symbols we are confident about live in the map; leaving one out degrades
gracefully to ``UNKNOWN`` rather than mis-stating a floor.
"""

from __future__ import annotations

from enum import Enum

#: Prefixes that mark a symbol as CPython *private* API — never part of the
#: stable ABI. ``_Py``/``_PyRuntime`` cover the internal C-API; the check is a
#: prefix test so it stays correct as CPython grows new private symbols.
_PRIVATE_PREFIXES: tuple[str, ...] = ("_Py", "_PyRuntime")

#: Prefixes that mark a symbol as belonging to the CPython C-API surface at all
#: (public or private). Anything not matching is "not CPython" and ignored by
#: the stable-ABI check (e.g. libc, libstdc++, or the module's own helpers).
_CPYTHON_PREFIXES: tuple[str, ...] = ("Py", "_Py")


class StableAbiStatus(str, Enum):
    """Classification of one imported CPython symbol against the Limited API."""

    #: In the stable-ABI allowlist and at/under the target floor.
    STABLE = "stable"
    #: Private CPython API (``_Py*``) — never stable. Definite violation.
    PRIVATE = "private"
    #: Recognised stable symbol, but newer than the declared/target floor.
    ABOVE_FLOOR = "above_floor"
    #: Public ``Py*`` symbol not in the curated allowlist — advisory only.
    UNKNOWN = "unknown"
    #: Not a CPython C-API symbol at all (libc, C++ runtime, module-local, …).
    NOT_CPYTHON = "not_cpython"


# ---------------------------------------------------------------------------
# Vendored stable-ABI allowlist (curated subset of Doc/data/stable_abi.dat)
# ---------------------------------------------------------------------------
# Symbol -> (major, minor) release that added it to the Limited API. The bulk
# of the core object/type/number/list/dict/unicode API has been stable since
# 3.2 (PEP 384); a handful of later additions are recorded with their real
# introduction version so the minimum-floor computation is accurate. This is a
# representative subset, not the full ~900-symbol table — unlisted public
# symbols degrade to StableAbiStatus.UNKNOWN (see module docstring).

_V32 = (3, 2)

LIMITED_API_ADDED: dict[str, tuple[int, int]] = {
    # Reference counting / lifecycle
    "Py_IncRef": _V32,
    "Py_DecRef": _V32,
    # Object protocol
    "PyObject_GetAttr": _V32,
    "PyObject_GetAttrString": _V32,
    "PyObject_SetAttr": _V32,
    "PyObject_SetAttrString": _V32,
    "PyObject_Call": _V32,
    "PyObject_CallObject": _V32,
    "PyObject_Repr": _V32,
    "PyObject_Str": _V32,
    "PyObject_IsTrue": _V32,
    "PyObject_RichCompare": _V32,
    "PyObject_Hash": _V32,
    "PyObject_GetItem": _V32,
    "PyObject_SetItem": _V32,
    "PyObject_GetIter": _V32,
    "PyObject_GenericGetAttr": _V32,
    "PyObject_GenericSetAttr": _V32,
    # Number protocol
    "PyNumber_Add": _V32,
    "PyNumber_Subtract": _V32,
    "PyNumber_Multiply": _V32,
    "PyNumber_TrueDivide": _V32,
    "PyNumber_And": _V32,
    "PyNumber_Or": _V32,
    "PyNumber_Xor": _V32,
    "PyNumber_Power": _V32,
    "PyNumber_Long": _V32,
    "PyNumber_Float": _V32,
    # Long / int
    "PyLong_FromLong": _V32,
    "PyLong_FromLongLong": _V32,
    "PyLong_FromUnsignedLong": _V32,
    "PyLong_FromSsize_t": _V32,
    "PyLong_AsLong": _V32,
    "PyLong_AsLongLong": _V32,
    "PyLong_AsSsize_t": _V32,
    # Float
    "PyFloat_FromDouble": _V32,
    "PyFloat_AsDouble": _V32,
    # Unicode / str
    "PyUnicode_FromString": _V32,
    "PyUnicode_FromStringAndSize": _V32,
    "PyUnicode_FromFormat": _V32,
    "PyUnicode_Concat": _V32,
    "PyUnicode_Compare": _V32,
    "PyUnicode_AsUTF8String": _V32,
    "PyUnicode_DecodeUTF8": _V32,
    # Bytes
    "PyBytes_FromString": _V32,
    "PyBytes_FromStringAndSize": _V32,
    "PyBytes_AsString": _V32,
    "PyBytes_Size": _V32,
    # Sequence containers
    "PyList_New": _V32,
    "PyList_Append": _V32,
    "PyList_GetItem": _V32,
    "PyList_SetItem": _V32,
    "PyList_Size": _V32,
    "PyTuple_New": _V32,
    "PyTuple_GetItem": _V32,
    "PyTuple_SetItem": _V32,
    "PyTuple_Size": _V32,
    "PyDict_New": _V32,
    "PyDict_GetItem": _V32,
    "PyDict_GetItemString": _V32,
    "PyDict_SetItem": _V32,
    "PyDict_SetItemString": _V32,
    "PyDict_Next": _V32,
    # Error handling
    "PyErr_SetString": _V32,
    "PyErr_SetObject": _V32,
    "PyErr_Occurred": _V32,
    "PyErr_Clear": _V32,
    "PyErr_Format": _V32,
    "PyErr_NoMemory": _V32,
    # Module / init
    "PyModule_Create2": _V32,
    "PyModule_AddObject": _V32,
    "PyModule_AddIntConstant": _V32,
    "PyModule_AddStringConstant": _V32,
    "PyModule_GetState": _V32,
    "PyArg_ParseTuple": _V32,
    "PyArg_ParseTupleAndKeywords": _V32,
    "PyArg_Parse": _V32,
    "Py_BuildValue": _V32,
    "PyType_Ready": _V32,
    "PyType_GenericNew": _V32,
    "PyCapsule_New": _V32,
    "PyCapsule_GetPointer": _V32,
    # --- genuinely later Limited-API additions (real floors) ---
    "PyObject_GenericSetDict": (3, 3),
    "PyType_GetSlot": (3, 4),
    "PyType_FromSpec": (3, 3),
    "PyType_FromSpecWithBases": (3, 3),
    "PyModule_AddFunctions": (3, 5),
    "PyErr_FormatV": (3, 5),
    "PyMem_Calloc": (3, 5),
    "PyCodec_NameReplaceErrors": (3, 5),
    "PyType_GetFlags": (3, 2),
    "Py_GenericAlias": (3, 9),
    "PyObject_GC_IsTracked": (3, 9),
    "PyObject_CallNoArgs": (3, 10),
    "PyModule_AddType": (3, 10),
    "Py_NewRef": (3, 10),
    "Py_XNewRef": (3, 10),
    "PyType_GetName": (3, 11),
    "PyType_GetQualName": (3, 11),
    "PyFrame_GetVar": (3, 12),
    "PyType_GetModuleByDef": (3, 11),
}


def is_cpython_symbol(name: str) -> bool:
    """True if *name* looks like a CPython C-API symbol (public or private)."""
    return name.startswith(_CPYTHON_PREFIXES)


def is_private_symbol(name: str) -> bool:
    """True if *name* is CPython *private* API (``_Py*``) — never stable."""
    return name.startswith(_PRIVATE_PREFIXES)


def classify(
    name: str, abi3_floor: tuple[int, int] | None = None
) -> tuple[StableAbiStatus, tuple[int, int] | None]:
    """Classify one imported symbol against the stable ABI.

    Returns ``(status, added_version)``. ``added_version`` is the release the
    symbol entered the Limited API when known, else ``None``.

    ``abi3_floor`` is the module's declared / target ``Py_LIMITED_API`` version
    as ``(major, minor)``. When given, a recognised stable symbol newer than the
    floor is reported :data:`StableAbiStatus.ABOVE_FLOOR` (it would be missing on
    an interpreter at the floor).
    """
    if not is_cpython_symbol(name):
        return StableAbiStatus.NOT_CPYTHON, None
    if is_private_symbol(name):
        return StableAbiStatus.PRIVATE, None
    added = LIMITED_API_ADDED.get(name)
    if added is None:
        return StableAbiStatus.UNKNOWN, None
    if abi3_floor is not None and added > abi3_floor:
        return StableAbiStatus.ABOVE_FLOOR, added
    return StableAbiStatus.STABLE, added


def min_required_abi3(names: list[str]) -> tuple[int, int] | None:
    """Minimum Limited-API floor implied by *names*.

    The highest ``added_version`` among the recognised stable imports: an
    ``abi3`` module can only load on interpreters at or above this. Returns
    ``None`` when no import is a recognised stable symbol (nothing to floor on).
    Private (``_Py*``) imports are not floors — they are outright violations —
    so they do not participate here.
    """
    floors = [
        v
        for n in names
        if not is_private_symbol(n)
        for v in (LIMITED_API_ADDED.get(n),)
        if v is not None
    ]
    return max(floors) if floors else None


def format_version(v: tuple[int, int]) -> str:
    """Render a ``(major, minor)`` version tuple as ``"3.9"``."""
    return f"{v[0]}.{v[1]}"


def parse_abi3_version(text: str) -> tuple[int, int] | None:
    """Parse an ``--abi3`` argument like ``"3.9"`` / ``"3"`` into a tuple.

    Returns ``None`` when *text* is not a recognisable ``major[.minor]`` string.
    """
    parts = text.strip().split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return None
    return (major, minor)
