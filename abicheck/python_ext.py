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
:class:`PythonExtMetadata`, which :mod:`abicheck.diff_python` then diffs and the
``abicheck stable-abi`` command audits against :mod:`abicheck.stable_abi`.

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
#: (stable-ABI); Windows uses ``foo.cp311-win_amd64.pyd`` / ``foo.pyd``.
_CPYTHON_TAG_RE = re.compile(r"\.cpython-(\d)(\d+)-")
_CP_WIN_TAG_RE = re.compile(r"\.cp(\d)(\d+)-")
#: ``cpXY-abi3`` — the wheel/SOABI stable-ABI tag that also carries the floor
#: (e.g. ``foo.cp39-abi3-win_amd64.pyd`` → abi3, floor 3.9). Checked before the
#: version-specific ``cpXY`` tag so a stable-ABI Windows artifact is recognised.
_CP_ABI3_RE = re.compile(r"cp(\d)(\d+)-abi3")
#: A bare ``abi3`` token anywhere in the name (``foo.abi3.so``, ``…-abi3-…``).
_ABI3_TAG_RE = re.compile(r"(?:^|[._-])abi3(?:[._-]|$)")

#: ``PyInit_<mod>`` (Py3) / ``init<mod>`` (Py2) module init export.
_PYINIT3_RE = re.compile(r"^PyInit_(?P<mod>[A-Za-z_][A-Za-z0-9_]*)$")
_PYINIT2_RE = re.compile(r"^init(?P<mod>[A-Za-z_][A-Za-z0-9_]*)$")


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
    #: Imported CPython C-API symbols (``Py*`` / ``_Py*``), sorted & de-duped.
    cpython_imports: list[str] = field(default_factory=list)

    @property
    def is_extension(self) -> bool:
        """True when this looks like a genuine CPython extension module."""
        return self.init_symbol is not None or bool(self.cpython_imports)

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


def _iter_imported_names(snap: AbiSnapshot) -> list[str]:
    """All imported (undefined) symbol names across present binary metadata."""
    names: list[str] = []
    if snap.elf is not None:
        names.extend(i.name for i in snap.elf.imports if i.name)
    if snap.pe is not None:
        for funcs in snap.pe.imports.values():
            names.extend(f for f in funcs if f)
    if snap.macho is not None:
        names.extend(getattr(snap.macho, "imported_symbols", []) or [])
    return names


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
) -> tuple[str | None, bool, tuple[int, int] | None]:
    """Parse the filename for an SOABI/abi3 tag.

    Returns ``(soabi_tag, limited_api, declared_abi3)``.

    LIMITATION: the stable-ABI promise lives in the *wheel* tag
    (``…-cp39-abi3-win_amd64.whl``), not always in the extension filename.
    A Windows abi3 module is frequently installed as a bare ``foo.pyd`` with no
    tag, which is indistinguishable from a version-specific build — it cannot be
    recognised as abi3 from the file alone. When the ``cpXY-abi3`` tag *is*
    present in the name (it often is), it is honoured here and its floor
    recovered. For a tagless artifact, run ``stable-abi --abi3 <floor>`` (which
    flags private imports regardless of the limited-api flag) or give the tagged
    filename.
    """
    for candidate in (source_path, library):
        if not candidate:
            continue
        base = candidate.replace("\\", "/").rsplit("/", 1)[-1]
        # `cpXY-abi3` — abi3 promise WITH a declared floor (Windows/wheel tag).
        m = _CP_ABI3_RE.search(base)
        if m:
            return "abi3", True, (int(m.group(1)), int(m.group(2)))
        # A bare `abi3` token — abi3 promise, floor undeclared (`foo.abi3.so`).
        if _ABI3_TAG_RE.search(base):
            return "abi3", True, None
        m = _CPYTHON_TAG_RE.search(base) or _CP_WIN_TAG_RE.search(base)
        if m:
            major, minor = int(m.group(1)), int(m.group(2))
            return f"cpython-{major}{minor}", False, (major, minor)
    return None, False, None


def detect_python_extension(snap: AbiSnapshot) -> PythonExtMetadata | None:
    """Recognise a CPython extension module from *snap*, else ``None``.

    A library qualifies when it either exports a ``PyInit_*`` / ``init*`` module
    init function **or** imports CPython C-API symbols. Both catch the same set
    of Cython/pybind11/nanobind/C extensions from different angles; requiring
    only one keeps stripped-init or statically-linked-libpython edge cases in
    scope while never matching an ordinary C/C++ library (no ``Py*`` imports and
    no init export).
    """
    imported = _iter_imported_names(snap)
    cpython_imports = sorted({n for n in imported if stable_abi.is_cpython_symbol(n)})
    init_symbol, module_name, python_major = _detect_init_export(
        _iter_exported_names(snap)
    )

    if init_symbol is None and not cpython_imports:
        return None

    soabi_tag, limited_api, declared_abi3 = _detect_soabi(
        snap.library, snap.source_path
    )
    return PythonExtMetadata(
        module_name=module_name,
        init_symbol=init_symbol,
        python_major=python_major,
        soabi_tag=soabi_tag,
        limited_api=limited_api,
        declared_abi3=declared_abi3,
        cpython_imports=cpython_imports,
    )
