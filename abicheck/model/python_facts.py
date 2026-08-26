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

"""Python-extension surface facts as data.

The declared Python API a stub file describes, the CPython extension-module
facts a binary carries, and the NumPy C-API compatibility window an extension
was built against. ``abicheck.python_api``, ``abicheck.python_ext`` and
``abicheck.numpy_capi`` fill these in and re-export them; the shapes
themselves carry no parsing (ADR-061 Phase 5).
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Parameter kinds, mirroring :class:`inspect.Parameter` kinds. Kept as plain
#: strings so the surface serializes to JSON without an enum round-trip.
POSITIONAL_ONLY = "positional_only"
POSITIONAL_OR_KEYWORD = "positional_or_keyword"
VAR_POSITIONAL = "var_positional"  # ``*args``
KEYWORD_ONLY = "keyword_only"
VAR_KEYWORD = "var_keyword"  # ``**kwargs``


#: Descriptor kinds a callable can present. A module-level function is
#: ``FUNCTION``; a class member is one of the others. The distinction matters
#: to callers: a ``PROPERTY`` is accessed as an attribute (not called), a
#: ``STATIC``/``CLASS`` method binds differently from an ``INSTANCE`` method, so
#: a change between them breaks existing call/access sites.
DESC_FUNCTION = "function"
DESC_INSTANCE = "instance"
DESC_STATIC = "static"
DESC_CLASS = "class"
DESC_PROPERTY = "property"


@dataclass
class PyParameter:
    """A single parameter of a Python-level function or method."""

    name: str
    kind: str = POSITIONAL_OR_KEYWORD
    #: True when the parameter has a default (is optional at the call site).
    has_default: bool = False
    #: The PEP 484 annotation as source text (``"int"``, ``"list[str]"``), or
    #: ``None`` when the stub declares the parameter without an annotation.
    annotation: str | None = None

    @property
    def is_positional(self) -> bool:
        """True for the two kinds that can be passed by position."""
        return self.kind in (POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD)

    @property
    def is_variadic(self) -> bool:
        """True for ``*args`` / ``**kwargs`` collectors (not a named argument)."""
        return self.kind in (VAR_POSITIONAL, VAR_KEYWORD)


@dataclass
class PyFunction:
    """A top-level function or a class method recovered from the stub.

    Represents one signature. When a name is ``@overload``-ed the extra
    variants are carried in :attr:`overloads` (each itself a
    :class:`PyFunction`); a plain callable has an empty ``overloads`` list.
    """

    name: str
    parameters: list[PyParameter] = field(default_factory=list)
    #: Return annotation as source text, or ``None`` when unannotated.
    return_annotation: str | None = None
    #: True when the stub declared ``async def`` — a caller must ``await`` the
    #: result, so flipping this is a call-contract break.
    is_async: bool = False
    #: How the callable is bound (:data:`DESC_FUNCTION` for a module function,
    #: else ``instance``/``static``/``class``/``property`` for a class member).
    descriptor: str = DESC_FUNCTION
    #: When the callable is ``@overload``-ed, all signature variants (including
    #: the one this object itself represents), in declaration order. Empty for a
    #: single-signature callable.
    overloads: list[PyFunction] = field(default_factory=list)

    @property
    def named_parameters(self) -> list[PyParameter]:
        """Parameters that name an argument (excludes ``*args`` / ``**kwargs``)."""
        return [p for p in self.parameters if not p.is_variadic]


@dataclass
class PyClass:
    """A top-level class and its public methods."""

    name: str
    #: method name → :class:`PyFunction`
    methods: dict[str, PyFunction] = field(default_factory=dict)


@dataclass
class PythonApiSurface:
    """The Python-visible API surface of an extension module.

    Absent (``AbiSnapshot.python_api is None``) when no Python-level surface
    could be recovered — the common case for a plain C/C++ library, and for an
    extension module that ships no ``.pyi`` stub.
    """

    #: Module name (``foo``), recovered from the extension init export or the
    #: stub filename.
    module_name: str | None = None
    #: Where the surface was recovered from — currently always ``"stub"`` (a
    #: ``.pyi`` file). Reserved for future docstring / runtime sources.
    source: str = "stub"
    #: Path to the artifact the surface was recovered from (the ``.pyi``).
    source_path: str | None = None
    #: top-level function name → :class:`PyFunction`
    functions: dict[str, PyFunction] = field(default_factory=dict)
    #: top-level class name → :class:`PyClass`
    classes: dict[str, PyClass] = field(default_factory=dict)
    #: True when the stub parsed cleanly. ``False`` marks an *unrecoverable*
    #: surface (a syntax error or size limit): the emptiness is a parse failure,
    #: not an intentionally-empty API, so the diff must report the invalid
    #: checked input rather than read every old name as removed.
    parse_ok: bool = True

    @property
    def is_empty(self) -> bool:
        """True when the surface carries no functions and no classes."""
        return not self.functions and not self.classes


#: The ONE version-neutral Windows CPython import library the Stable ABI links
#: against. Every other CPython import DLL — ``python311.dll``, the free-threaded
#: ``python313t.dll``, the debug ``python311_d.dll``, … — is version-specific and
#: pins the module to one interpreter ABI, so for the abi3 contract anything but
#: this exact name is a violation.
_STABLE_PYTHON_DLL = "python3.dll"


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
    def is_version_specific(self) -> bool:
        """True when the SOABI tag pins the module to one interpreter (not abi3).

        A ``foo.cpython-311-…so`` / ``foo.cp311-win_amd64.pyd`` (or a free-threaded
        ``cpython-313t``) carries a version-specific interpreter tag and loads
        only on that one minor — it is not an ``abi3`` build and cannot satisfy a
        ``Py_LIMITED_API`` floor no matter how stable its imported symbol *names*
        are. A bare ``.abi3.`` build (``limited_api``) or a tagless artifact
        (``soabi_tag is None``) is not version-specific.
        """
        return (
            self.soabi_tag is not None
            and self.soabi_tag != "abi3"
            and not self.limited_api
        )

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


@dataclass
class NumPyCapiSurface:
    """One library's NumPy C-API consumption, from binary evidence alone."""

    consumes_array_api: bool = False
    consumes_ufunc_api: bool = False
    #: The minimum NumPy release this module's compiled-in C-API usage
    #: requires (NPY_TARGET_VERSION, or NumPy's own default when the build
    #: didn't set one), e.g. ``"1.23"``. ``None`` when the target-version
    #: string wasn't recoverable (a degraded-coverage case, not "no floor").
    capi_target_version: str | None = None
