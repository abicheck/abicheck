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

"""Python-level API surface of a CPython extension module (G23).

G14 (:mod:`abicheck.python_ext`) checks the **native C-ABI** contract of an
extension module — the ``Py*`` symbols it imports and its ``abi3`` conformance,
i.e. whether the compiled ``.so`` / ``.pyd`` *loads*. That is not the contract
most consumers depend on. What they ``import`` is the **Python-level API**: the
module's top-level functions, classes, methods, and their signatures (parameter
names, kinds, defaults, and annotations). Two builds can be C-ABI-identical yet
break every caller::

    # v1
    def transform(data, *, encoding="utf-8"): ...
    # v2 — same PyInit_, same imported Py* surface, same abi3 tag…
    def transform(data, codec): ...   # renamed kwarg, dropped default

``compare`` / ``scan --abi3`` see nothing here: the export table is still one
``PyInit_`` symbol and the imported C-API is unchanged. The break lives in the
Python signatures, which are not in the binary's ABI surface at all.

**There are no clean public C headers to lean on** for an extension module
(Cython emits internal CPython API; pybind11/nanobind headers describe the
framework, not the module). So the Python surface must be recovered from
*Python-world* artifacts. This module recovers it — statically, without
importing or executing the module — from the richest safe source: a **PEP 484
type stub** (``.pyi``) shipped alongside the binary, parsed with :mod:`ast`.
It is the analog of C-header diffing for Python.

The recovered :class:`PythonApiSurface` is attached to :class:`AbiSnapshot`
(like ``python_ext``); :mod:`abicheck.diff_python_api` diffs two surfaces and
emits Python-level ``ChangeKind``s through the existing reporter/verdict
machinery. When no stub can be found the surface is simply absent (``None``) and
the detector is skipped — the check degrades honestly rather than
false-negating silently.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .model import AbiSnapshot

_log = logging.getLogger(__name__)

#: Parameter kinds, mirroring :class:`inspect.Parameter` kinds. Kept as plain
#: strings so the surface serializes to JSON without an enum round-trip.
POSITIONAL_ONLY = "positional_only"
POSITIONAL_OR_KEYWORD = "positional_or_keyword"
VAR_POSITIONAL = "var_positional"  # ``*args``
KEYWORD_ONLY = "keyword_only"
VAR_KEYWORD = "var_keyword"  # ``**kwargs``


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
    #: surface (a syntax error): the emptiness is a parse failure, not an
    #: intentionally-empty API, so the diff must skip it rather than read every
    #: old name as removed.
    parse_ok: bool = True

    @property
    def is_empty(self) -> bool:
        """True when the surface carries no functions and no classes."""
        return not self.functions and not self.classes


# ---------------------------------------------------------------------------
# Public-name policy
# ---------------------------------------------------------------------------


def _is_public_name(name: str) -> bool:
    """True when *name* is part of the public API surface.

    A leading-underscore name (``_helper``, ``__mangled``) is private by
    convention and excluded. A dunder (``__init__``, ``__call__``) is public —
    constructors and operator hooks are very much part of the callable contract.
    """
    if name.startswith("_"):
        return name.startswith("__") and name.endswith("__") and len(name) > 4
    return True


# ---------------------------------------------------------------------------
# ast → surface extraction
# ---------------------------------------------------------------------------


def _annotation_text(node: ast.expr | None) -> str | None:
    """Render an annotation AST node back to source text, or ``None``."""
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001 — never let an odd node abort extraction
        return None


def _params_from_arguments(args: ast.arguments) -> list[PyParameter]:
    """Build the ordered parameter list from an ``ast.arguments`` node.

    Defaults are aligned CPython-style: ``args.defaults`` binds to the *tail* of
    ``posonlyargs + args``; ``args.kw_defaults`` binds positionally to
    ``kwonlyargs`` with ``None`` marking a required keyword-only argument.
    """
    params: list[PyParameter] = []

    positional = list(args.posonlyargs) + list(args.args)
    n_defaults = len(args.defaults)
    n_required = len(positional) - n_defaults
    posonly_count = len(args.posonlyargs)
    for i, a in enumerate(positional):
        kind = POSITIONAL_ONLY if i < posonly_count else POSITIONAL_OR_KEYWORD
        params.append(
            PyParameter(
                name=a.arg,
                kind=kind,
                has_default=i >= n_required,
                annotation=_annotation_text(a.annotation),
            )
        )

    if args.vararg is not None:
        params.append(
            PyParameter(
                name=args.vararg.arg,
                kind=VAR_POSITIONAL,
                annotation=_annotation_text(args.vararg.annotation),
            )
        )

    for a, default in zip(args.kwonlyargs, args.kw_defaults):
        params.append(
            PyParameter(
                name=a.arg,
                kind=KEYWORD_ONLY,
                has_default=default is not None,
                annotation=_annotation_text(a.annotation),
            )
        )

    if args.kwarg is not None:
        params.append(
            PyParameter(
                name=args.kwarg.arg,
                kind=VAR_KEYWORD,
                annotation=_annotation_text(args.kwarg.annotation),
            )
        )

    return params


def _decorator_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """The bare decorator names on a function/method (``staticmethod`` etc.)."""
    names: set[str] = set()
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name):
            names.add(dec.id)
        elif isinstance(dec, ast.Attribute):
            names.add(dec.attr)
    return names


def _descriptor_kind(decorators: set[str], *, in_class: bool) -> str:
    """Classify a callable's descriptor kind from its decorator names."""
    if not in_class:
        return DESC_FUNCTION
    if "property" in decorators or decorators & {"setter", "getter", "deleter"}:
        return DESC_PROPERTY
    if "staticmethod" in decorators:
        return DESC_STATIC
    if "classmethod" in decorators:
        return DESC_CLASS
    return DESC_INSTANCE


def _function_from_def(
    node: ast.FunctionDef | ast.AsyncFunctionDef, *, in_class: bool
) -> PyFunction:
    """Build a :class:`PyFunction` from a def node.

    For a *bound* method (instance/class/property — not a ``staticmethod`` or a
    module function) the leading ``self`` / ``cls`` positional parameter is
    dropped: it is bound by the descriptor protocol, not passed by callers, so
    renaming it does not break anyone. ``async`` and descriptor kind are
    recorded so a ``def``↔``async def`` or method↔property change is diffable.
    """
    descriptor = _descriptor_kind(_decorator_names(node), in_class=in_class)
    params = _params_from_arguments(node.args)
    if (
        descriptor in (DESC_INSTANCE, DESC_CLASS, DESC_PROPERTY)
        and params
        and params[0].name in ("self", "cls")
        and params[0].is_positional
    ):
        params = params[1:]
    return PyFunction(
        name=node.name,
        parameters=params,
        return_annotation=_annotation_text(node.returns),
        is_async=isinstance(node, ast.AsyncFunctionDef),
        descriptor=descriptor,
    )


def _fold_callables(body: list[ast.stmt], *, in_class: bool) -> dict[str, PyFunction]:
    """Collect public function/method defs, folding ``@overload`` variants.

    A name declared more than once (``@overload``, or a property getter/setter
    pair) keeps every variant in the primary :class:`PyFunction`'s ``overloads``
    list, in declaration order, so a dropped overload is diffable rather than
    silently collapsed to the last definition.
    """
    order: list[str] = []
    variants: dict[str, list[PyFunction]] = {}
    for node in body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _is_public_name(node.name):
            continue
        fn = _function_from_def(node, in_class=in_class)
        if node.name not in variants:
            variants[node.name] = []
            order.append(node.name)
        variants[node.name].append(fn)
    result: dict[str, PyFunction] = {}
    for name in order:
        vs = variants[name]
        # For an overloaded name, the primary is a *distinct* object carrying the
        # full variant list; the variants themselves keep an empty ``overloads``
        # so the primary is never contained in its own list (which would make an
        # asdict()/serialization cycle).
        primary = replace(vs[-1], overloads=list(vs)) if len(vs) > 1 else vs[-1]
        result[name] = primary
    return result


def _class_from_def(node: ast.ClassDef) -> PyClass:
    """Build a :class:`PyClass`, collecting its public methods."""
    return PyClass(name=node.name, methods=_fold_callables(node.body, in_class=True))


def surface_from_stub_source(
    source: str, *, module_name: str | None = None, source_path: str | None = None
) -> PythonApiSurface:
    """Parse ``.pyi`` *source* text into a :class:`PythonApiSurface`.

    Extraction is static (:func:`ast.parse`, never imported/executed). Only
    top-level public functions and classes are collected; private
    (leading-underscore) names are excluded. A syntax error yields an empty
    surface rather than raising, so a malformed stub degrades to "nothing
    recovered".
    """
    surface = PythonApiSurface(
        module_name=module_name, source="stub", source_path=source_path
    )
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        _log.debug("python_api: could not parse stub %s: %s", source_path, exc)
        surface.parse_ok = False
        return surface

    surface.functions = _fold_callables(tree.body, in_class=False)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and _is_public_name(node.name):
            surface.classes[node.name] = _class_from_def(node)
    return surface


def surface_from_stub_file(
    path: Path, *, module_name: str | None = None
) -> PythonApiSurface:
    """Read and parse a ``.pyi`` file into a :class:`PythonApiSurface`."""
    text = path.read_text(encoding="utf-8", errors="replace")
    return surface_from_stub_source(
        text, module_name=module_name, source_path=str(path)
    )


# ---------------------------------------------------------------------------
# Stub discovery + snapshot attach
# ---------------------------------------------------------------------------


def _module_base_name(filename: str, module_name: str | None) -> str:
    """The extension module's base name for stub lookup.

    ``foo.cpython-311-x86_64-linux-gnu.so`` → ``foo``;
    ``foo.abi3.so`` → ``foo``; ``foo.pyd`` → ``foo``. Prefers the init-export
    module name when available (it is authoritative), else the filename stem
    before the first dot.
    """
    if module_name:
        return module_name
    return filename.split(".", 1)[0]


def _find_stub(binary_path: str, module_name: str | None) -> Path | None:
    """Locate a ``.pyi`` type stub for the extension at *binary_path*.

    Checks, in order, next to the binary: ``<base>.pyi``, ``<base>.pyi`` in a
    sibling ``<base>-stubs`` dir, and ``<base>/__init__.pyi`` (a package stub).
    Returns the first that exists, else ``None``.
    """
    p = Path(binary_path)
    directory = p.parent
    base = _module_base_name(p.name, module_name)

    candidates = [
        directory / f"{base}.pyi",
        directory / f"{base}-stubs" / "__init__.pyi",
        directory / base / "__init__.pyi",
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def detect_python_api(snap: AbiSnapshot) -> PythonApiSurface | None:
    """Recover the Python-level API surface for *snap*, else ``None``.

    Requires (a) the snapshot to be a **recognised CPython extension module**
    (``snap.python_ext`` present and ``is_extension``), (b) an on-disk
    ``source_path``, and (c) a ``.pyi`` stub alongside it. The extension gate is
    essential: without it a plain native library (``libfoo.so``) that merely
    happens to have an unrelated ``libfoo.pyi`` sibling would be handed a Python
    API surface and later produce spurious ``python_api_*`` findings. Returns
    ``None`` — the honest "nothing recovered" answer — only when a precondition
    is unmet (not an extension, no ``source_path``, or no stub found); a stub
    that is *present but empty* still yields a (possibly empty) surface so a
    later removal of its last public name is diffable.
    """
    if snap.python_ext is None or not snap.python_ext.is_extension:
        return None
    if not snap.source_path:
        return None
    module_name = snap.python_ext.module_name
    stub = _find_stub(snap.source_path, module_name)
    if stub is None:
        return None
    # A *present, cleanly-parsed* stub yields a surface even when it is empty
    # (all public names removed, or only private helpers remain): that empty
    # surface is meaningful, so a version deleting its last public function is
    # still diffed as a removal. ``None`` is reserved for "no stub / not an
    # extension / unrecoverable". A malformed stub (``parse_ok`` False) is
    # unrecoverable — returning its empty surface would make the diff read every
    # old name as removed, so skip it.
    surface = surface_from_stub_file(stub, module_name=module_name)
    if not surface.parse_ok:
        return None
    return surface
