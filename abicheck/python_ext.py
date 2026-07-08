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

"""CPython extension-module recognition and metadata (G14).

A CPython extension module — whether produced by **Cython**, **pybind11**,
**nanobind**, or a hand-written C extension — is an ordinary shared library
(``.so`` / ``.pyd`` / ``.dylib``) with two tells:

* it **exports** a module init function, ``PyInit_<name>`` (Python 3) or
  ``init<name>`` (Python 2); and
* it **imports** CPython C-API symbols (``Py*`` / ``_Py*``) from ``libpython``.

For such a module the compatibility surface that matters is not the export
table (essentially just the init function) but the *imported* CPython symbols
plus whether the module is an ``abi3`` / ``Py_LIMITED_API`` build. This module
recognises extension modules from a snapshot and captures that surface into
:class:`PythonExtMetadata`, which :mod:`abicheck.diff_python` then diffs and
``abicheck scan --abi3`` audits against :mod:`abicheck.stable_abi`.

The recognition is uniform across builders: Cython/pybind11/nanobind/C all land
here because they all emit the same ``PyInit_*`` export and link ``libpython``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import stable_abi

if TYPE_CHECKING:
    from .model import AbiSnapshot

#: SOABI / extension-suffix patterns. CPython names extension modules
#: ``foo.cpython-311-x86_64-linux-gnu.so`` (version-specific) or ``foo.abi3.so``
#: (stable-ABI); Windows uses ``foo.cp311-win_amd64.pyd`` / ``foo.pyd``. A
#: free-threaded (PEP 703, ``Py_GIL_DISABLED``) build carries a ``t`` right after
#: the minor: ``foo.cpython-313t-…so`` / ``foo.cp313t-win_amd64.pyd``. The
#: optional ``t`` group is captured so the free-threaded ABI is recognised.
_CPYTHON_TAG_RE = re.compile(r"\.cpython-(\d)(\d+)(t?)-")
_CP_WIN_TAG_RE = re.compile(r"\.cp(\d)(\d+)(t?)-")
#: ``cpXY-abi3`` — the wheel/SOABI stable-ABI tag that also carries the floor
#: (e.g. ``foo.cp39-abi3-win_amd64.pyd`` → abi3, floor 3.9). Checked before the
#: version-specific ``cpXY`` tag so a stable-ABI Windows artifact is recognised.
_CP_ABI3_RE = re.compile(r"cp(\d)(\d+)-abi3")
#: A bare ``abi3`` token anywhere in the name (``foo.abi3.so``, ``…-abi3-…``).
_ABI3_TAG_RE = re.compile(r"(?:^|[._-])abi3(?:[._-]|$)")

#: ``PyInit_<mod>`` (Py3) / ``init<mod>`` (Py2) module init export.
_PYINIT3_RE = re.compile(r"^PyInit_(?P<mod>[A-Za-z_][A-Za-z0-9_]*)$")
_PYINIT2_RE = re.compile(r"^init(?P<mod>[A-Za-z_][A-Za-z0-9_]*)$")

#: The ONE version-neutral Windows CPython import library the Stable ABI links
#: against. Every other CPython import DLL — ``python311.dll``, the free-threaded
#: ``python313t.dll``, the debug ``python311_d.dll``, … — is version-specific and
#: pins the module to one interpreter ABI, so for the abi3 contract anything but
#: this exact name is a violation.
_STABLE_PYTHON_DLL = "python3.dll"

#: A CPython runtime import DLL, by name: ``python3.dll`` / ``python311.dll`` /
#: ``python313t.dll`` / ``python311_d.dll``. Requires a digit right after
#: ``python`` so a third-party library using the ``Py`` C-API convention
#: (``numpy.dll`` exporting ``PyArray_*``, a companion ``pythonmagic.dll``) is
#: NOT mistaken for the interpreter runtime. On Windows we therefore only treat
#: ``Py*`` symbols imported *from such a DLL* as CPython C-API imports.
_CPYTHON_DLL_RE = re.compile(r"^python\d\w*\.dll$", re.IGNORECASE)


def _is_cpython_dll(name: str) -> bool:
    """True if *name* is a CPython runtime import DLL (not a third-party ``Py*`` lib)."""
    return bool(_CPYTHON_DLL_RE.match(name))


@dataclass
class PythonExtMetadata:
    """CPython extension-module facts extracted from a snapshot.

    Absent (``AbiSnapshot.python_ext is None``) when the library is not a
    recognised extension module — the common case for a plain C/C++ library.
    """

    #: Module name recovered from the init export (``PyInit_foo`` → ``foo``).
    module_name: str | None = None
    #: The init export itself, e.g. ``PyInit_foo`` / ``initfoo``.
    init_symbol: str | None = None
    #: Python major the init export implies (3 for ``PyInit_*``, 2 for ``init*``).
    python_major: int | None = None
    #: Raw SOABI / suffix tag from the filename, e.g. ``cpython-311`` / ``abi3``.
    soabi_tag: str | None = None
    #: True when the module is a stable-ABI (``abi3`` / ``Py_LIMITED_API``) build
    #: — inferred from an ``abi3`` token in the filename (``.abi3.`` or a
    #: ``cpXY-abi3`` wheel tag). Such a module promises it uses only the Limited
    #: API and must load on every interpreter at/above its floor. A tagless
    #: ``foo.pyd`` cannot be recognised as abi3 from the file alone (see
    #: :func:`_detect_soabi`).
    limited_api: bool = False
    #: Declared / inferred ``Py_LIMITED_API`` floor as ``(major, minor)`` when
    #: known (e.g. an ``abi3`` tag pins the module to that minor). ``None`` when
    #: undeclared.
    declared_abi3: tuple[int, int] | None = None
    #: True when this is a **free-threaded** (PEP 703, ``Py_GIL_DISABLED``) build
    #: — a ``t``-suffixed interpreter tag (``cpython-313t`` / ``cp313t``). A
    #: free-threaded build targets a *different* CPython ABI than the regular
    #: (GIL) build of the same minor: the two are not interchangeable, and a
    #: free-threaded build **cannot** be ``abi3`` (``Py_LIMITED_API`` is
    #: incompatible with ``Py_GIL_DISABLED`` as of CPython 3.13–3.15), so
    #: :attr:`limited_api` is always ``False`` when this is set.
    free_threaded: bool = False
    #: Imported CPython C-API symbols (``Py*`` / ``_Py*``), sorted & de-duped.
    cpython_imports: list[str] = field(default_factory=list)
    #: Windows import DLL(s) that provide the CPython C-API imports, e.g.
    #: ``["python3.dll"]`` (Stable-ABI forwarder) or ``["python311.dll"]``
    #: (version-specific). Populated from the PE import table only — ELF/Mach-O
    #: resolve ``libpython`` at load time, not via a named import library — so it
    #: is empty on those platforms. Lets the ``abi3`` check catch a PE module that
    #: imports stable *symbol names* but links a version-specific ``pythonXY.dll``
    #: (which would not load on another interpreter minor).
    cpython_dlls: list[str] = field(default_factory=list)

    @property
    def is_extension(self) -> bool:
        """True when this looks like a genuine CPython extension module."""
        return self.init_symbol is not None or bool(self.cpython_imports)

    @property
    def version_specific_python_dlls(self) -> list[str]:
        """CPython import DLLs that pin the module to one interpreter ABI.

        The Stable ABI links against exactly ``python3.dll`` (the version-neutral
        forwarder). Every other CPython import DLL is version-specific — a
        numbered ``python311.dll``, the free-threaded ``python313t.dll``, the
        debug ``python311_d.dll``, … — so any provider DLL whose name is not
        exactly ``python3.dll`` is a violation for an ``abi3`` module: it cannot
        load on another interpreter regardless of which symbol *names* it imports.
        """
        return [d for d in self.cpython_dlls if d.lower() != _STABLE_PYTHON_DLL]

    @property
    def private_imports(self) -> list[str]:
        """CPython *private* (``_Py*``) imports — never part of the stable ABI."""
        return [s for s in self.cpython_imports if stable_abi.is_private_symbol(s)]

    def min_required_abi3(self) -> tuple[int, int] | None:
        """Minimum Limited-API floor implied by the recognised stable imports."""
        return stable_abi.min_required_abi3(self.cpython_imports)


def _iter_exported_names(snap: AbiSnapshot) -> list[str]:
    """All exported symbol names across whichever binary metadata is present."""
    names: list[str] = []
    if snap.elf is not None:
        names.extend(s.name for s in snap.elf.symbols if s.name)
    if snap.pe is not None:
        names.extend(e.name for e in snap.pe.exports if e.name)
    if snap.macho is not None:
        names.extend(e.name for e in snap.macho.exports if e.name)
    return names


def _collect_cpython_imports(snap: AbiSnapshot) -> list[str]:
    """Imported CPython C-API symbols (``Py*`` / ``_Py*``), sorted & de-duped.

    On **PE** the provider DLL is known, so only ``Py*`` symbols imported from an
    actual CPython runtime DLL (:func:`_is_cpython_dll`) are counted — a
    third-party library that follows the ``Py`` C-API convention (``numpy.dll``
    exporting ``PyArray_*``, a companion ``PyFoo_*`` lib) is excluded, so it never
    produces a false stable-ABI violation.

    On **ELF/Mach-O** the undefined-symbol table carries no per-symbol provider
    (``libpython`` is resolved at load time), so recognition falls back to the
    CPython ``Py``/``_Py`` naming convention. In practice the dominant
    third-party C-API (NumPy) is exposed through a runtime *capsule*, not direct
    symbol linkage, so those names do not appear as undefined imports; a
    companion library that *directly* exports ``Py*`` symbols and is linked
    remains a known edge case on these platforms.
    """
    names: list[str] = []
    if snap.elf is not None:
        names.extend(
            i.name for i in snap.elf.imports if i.name and stable_abi.is_cpython_symbol(i.name)
        )
    if snap.macho is not None:
        names.extend(
            n
            for n in (getattr(snap.macho, "imported_symbols", []) or [])
            if stable_abi.is_cpython_symbol(n)
        )
    if snap.pe is not None:
        for dll_name, funcs in snap.pe.imports.items():
            if _is_cpython_dll(dll_name):
                names.extend(
                    f for f in funcs if f and stable_abi.is_cpython_symbol(f)
                )
    return sorted(set(names))


def _iter_cpython_dlls(snap: AbiSnapshot) -> list[str]:
    """Windows CPython runtime import DLL(s) the module links (PE only).

    Identified by DLL *name* (:func:`_is_cpython_dll`), NOT by whether the DLL
    happens to export a ``Py*`` symbol — otherwise a third-party ``numpy.dll``
    (which exports ``PyArray_*``) would be misread as the interpreter runtime.
    Empty for ELF/Mach-O, whose ``libpython`` dependency is not a named import
    library in the same way.
    """
    if snap.pe is None:
        return []
    return sorted({d for d in snap.pe.imports if d and _is_cpython_dll(d)})


def _detect_init_export(names: list[str]) -> tuple[str | None, str | None, int | None]:
    """Find a module-init export → ``(init_symbol, module_name, python_major)``."""
    for name in names:
        m = _PYINIT3_RE.match(name)
        if m:
            return name, m.group("mod"), 3
    for name in names:
        m = _PYINIT2_RE.match(name)
        if m:
            return name, m.group("mod"), 2
    return None, None, None


def _detect_soabi(
    library: str | None, source_path: str | None
) -> tuple[str | None, bool, tuple[int, int] | None, bool]:
    """Parse the filename for an SOABI/abi3 tag.

    Returns ``(soabi_tag, limited_api, declared_abi3, free_threaded)``.

    LIMITATION: the stable-ABI promise lives in the *wheel* tag
    (``…-cp39-abi3-win_amd64.whl``), not always in the extension filename.
    A Windows abi3 module is frequently installed as a bare ``foo.pyd`` with no
    tag, which is indistinguishable from a version-specific build — it cannot be
    recognised as abi3 from the file alone. When the ``cpXY-abi3`` tag *is*
    present in the name (it often is), it is honoured here and its floor
    recovered. For a tagless artifact, run ``scan --abi3 <floor>`` (which flags
    private imports regardless of the limited-api flag) or give the tagged
    filename.

    A free-threaded (PEP 703) build carries a ``t`` after the minor
    (``cpython-313t`` / ``cp313t``); it is recognised and reported via the
    fourth return value. Such a build is never ``abi3`` — ``Py_LIMITED_API`` and
    ``Py_GIL_DISABLED`` are mutually exclusive — so the abi3 branches never carry
    a ``t`` and ``limited_api`` stays ``False`` for it.
    """
    for candidate in (source_path, library):
        if not candidate:
            continue
        base = candidate.replace("\\", "/").rsplit("/", 1)[-1]
        # `cpXY-abi3` — abi3 promise WITH a declared floor (Windows/wheel tag).
        m = _CP_ABI3_RE.search(base)
        if m:
            return "abi3", True, (int(m.group(1)), int(m.group(2))), False
        # A bare `abi3` token — abi3 promise, floor undeclared (`foo.abi3.so`).
        if _ABI3_TAG_RE.search(base):
            return "abi3", True, None, False
        m = _CPYTHON_TAG_RE.search(base) or _CP_WIN_TAG_RE.search(base)
        if m:
            major, minor = int(m.group(1)), int(m.group(2))
            free_threaded = m.group(3) == "t"
            tag = f"cpython-{major}{minor}{'t' if free_threaded else ''}"
            return tag, False, (major, minor), free_threaded
    return None, False, None, False


def detect_python_extension(snap: AbiSnapshot) -> PythonExtMetadata | None:
    """Recognise a CPython extension module from *snap*, else ``None``.

    A library qualifies when it exports the **unambiguous** Python-3
    ``PyInit_*`` init function **or** imports CPython C-API symbols. The
    Python-2 ``init<mod>`` pattern is deliberately NOT sufficient on its own: it
    is far too broad — an ordinary C library exporting ``initialize`` /
    ``init_foo`` would match — so it only counts when corroborated by actual
    ``Py*`` imports. This keeps stripped-init (Py3) and statically-linked
    extensions in scope while never matching a non-Python library that merely
    has an ``init`` export and no CPython imports.
    """
    cpython_imports = _collect_cpython_imports(snap)
    init_symbol, module_name, python_major = _detect_init_export(
        _iter_exported_names(snap)
    )

    # Py3 `PyInit_*` (python_major == 3) is unambiguous and qualifies alone. The
    # broad Py2 `init*` pattern (python_major == 2) and the no-init case both
    # require CPython imports as corroboration.
    if python_major != 3 and not cpython_imports:
        return None

    soabi_tag, limited_api, declared_abi3, free_threaded = _detect_soabi(
        snap.library, snap.source_path
    )
    return PythonExtMetadata(
        module_name=module_name,
        init_symbol=init_symbol,
        python_major=python_major,
        soabi_tag=soabi_tag,
        limited_api=limited_api,
        declared_abi3=declared_abi3,
        free_threaded=free_threaded,
        cpython_imports=cpython_imports,
        cpython_dlls=_iter_cpython_dlls(snap),
    )
