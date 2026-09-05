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
from pathlib import Path
from typing import TYPE_CHECKING

from . import stable_abi

# Fact dataclasses live in the model package (ADR-061 Phase 5): this module
# detects them and re-exports them so the historical
# ``from abicheck.python_ext import PythonExtMetadata`` spelling keeps resolving.
from .model.export_index import all_export_names, build_raw_export_index
from .model.python_facts import (
    PythonExtMetadata as PythonExtMetadata,
)

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

#: ``PyInit_<mod>`` — the Python 3 module init export.
_PYINIT3_RE = re.compile(r"^PyInit_(?P<mod>[A-Za-z_][A-Za-z0-9_]*)$")

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




def _iter_exported_names(snap: AbiSnapshot) -> list[str]:
    """All exported symbol names across whichever binary metadata is present.

    ADR-063 T7: sources its raw names from
    ``model.export_index.build_raw_export_index`` + ``all_export_names`` —
    deliberately unfiltered by ELF default-version status, matching this
    function's original "every raw table entry" contract (only a
    ``PyInit_*``/``init<mod>`` pattern match on the result matters to its
    caller, so no per-alias distinction is needed here). Sorted rather than
    left in raw table order: ``all_export_names`` returns a ``frozenset``,
    whose iteration order depends on ``PYTHONHASHSEED``, and
    ``_detect_init_export`` (this function's one caller) returns the *first*
    pattern match — when a shared object exports more than one ``PyInit_*``
    function, an unsorted order would record a different ``module_name``/
    ``init_symbol`` across runs of the same binary (Codex review).
    """
    index = build_raw_export_index(snap)
    if index is None:
        return []
    return sorted(all_export_names(index))


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


#: ``init<mod>`` — the legacy Python 2 module init export.
_PYINIT2_RE = re.compile(r"^init(?P<mod>[A-Za-z_][A-Za-z0-9_]*)$")


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


def abi3_precondition_message(abi3_floor: tuple[int, int], binary_name: str) -> str:
    """The "not a recognisable extension module" message ``scan --abi3``'s
    real precondition failure reports (:func:`abicheck.scan_engine.
    _run_abi3_audit`'s ``_EvidenceContractError``) and both dry-run previews
    of the identical precondition state -- one shared spelling so all three
    callers describe the same failure identically rather than three
    independently-drifting copies of the same sentence.
    """
    return (
        f"--abi3 {abi3_floor[0]}.{abi3_floor[1]} was given but "
        f"'{binary_name}' is not a recognisable CPython extension module "
        "(no PyInit_* export and no CPython C-API imports). The stable-ABI "
        "audit applies only to extension modules (Cython/pybind11/"
        "nanobind/C)."
    )


def detect_python_extension_from_binary(path: Path) -> PythonExtMetadata | None:
    """Cheap, binary-container-only extension recognition for dry-run previews.

    ``scan --abi3``'s real run requires the candidate to be a recognisable
    CPython extension module (:func:`detect_python_extension` against the
    real dump's snapshot) -- but neither ``scan --dry-run`` nor
    ``scan --artifact-set --dry-run`` builds a snapshot at all, since a dry
    run promises no compiler/frontend invocation. This applies the identical
    recognition logic to a snapshot built from *only* the container facts a
    plain binary read supplies (the export table on ELF/Mach-O, the export/
    import directory on PE) -- no DWARF, no header/build parse -- which is
    the same "binary export table parse" the L0_binary dry-run row already
    prices as within the dry-run contract. ``None`` for an unrecognised
    format or a binary that does not parse (mirrors the real parsers' own
    "empty metadata on any parse error" contract), same as a genuine
    non-extension library.

    Binary-container recognition only -- deliberately does not fall back to
    loading *path* as a serialized snapshot (a real, supported `scan
    ARTIFACT` input shape too): that fallback needs `serialization.
    load_snapshot`, which itself imports this module (for
    `PythonExtMetadata`/`detect_python_extension`), so adding the reverse
    edge here would create a real two-module import cycle (AI-readiness
    `import-cycle-growth`, fresh evidence). See
    :mod:`abicheck.scan_abi3_resolve`'s own resolver for the snapshot-aware
    orchestration that combines this function with that fallback from a
    module that can safely depend on both.
    """
    from . import binary_utils
    from .model import AbiSnapshot

    # A GNU ld linker script (a dev symlink stand-in like `libfoo.so` ->
    # `libfoo.so.1`) is itself plain text with no container magic bytes --
    # the real run follows it via `service.resolve_input`'s own recursive
    # resolution, so this probe must too, or a script pointing at a genuine
    # extension module misreports "not an extension" (Codex review). A no-op
    # for every other input (including a JSON snapshot, whose content never
    # matches the linker-script regex).
    path = binary_utils.resolve_linker_script_chain(Path(path))
    fmt = binary_utils.detect_binary_format(path)
    snap = AbiSnapshot(library=Path(path).name, version="", source_path=str(path))
    if fmt == "elf":
        from .elf_metadata import parse_elf_metadata

        snap.elf = parse_elf_metadata(Path(path))
    elif fmt == "pe":
        from .pe_metadata import parse_pe_metadata

        snap.pe = parse_pe_metadata(Path(path))
    elif fmt == "macho":
        from .macho_metadata import parse_macho_metadata

        snap.macho = parse_macho_metadata(Path(path))
    else:
        return None
    return detect_python_extension(snap)


