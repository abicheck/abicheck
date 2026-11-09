# SPDX-License-Identifier: Apache-2.0
"""Join the facts castxml's XML cannot carry from the clang AST of the same header.

castxml 0.7 (``--castxml-output=1``) writes nothing for two C++ facts:

* a function's ``noexcept`` exception specification (only a dynamic
  ``throw()`` becomes a ``throw`` attribute), so every castxml function read
  ``is_noexcept=False`` and adding ``noexcept`` to a virtual -- which makes an
  overrider without it ill-formed -- read ``NO_CHANGE``;
* an uninstantiated (constrained) template and a concept definition: castxml
  emits no element for either, so a new ``requires``-clause read
  ``NO_CHANGE``.

The clang ``-ast-dump=json`` of the same translation unit records both. This
module runs that parse only when a header can spell one of them, and carries
what it read on the castxml root as one JSON attribute (:data:`SEMANTICS_ATTR`):

* ``noexcept``: ``{mangled name: bool}`` from each clang function's own
  qualifiers (the reading the clang backend uses), or ``null`` when no clang
  parse is available;
* ``mention_files``: the local header files (a given header, or one under a
  given header's directory) whose text spells ``noexcept``/``throw``/
  ``nothrow`` in any letter case. A function declared in one of them that the
  ``noexcept`` map does not cover has an *unknown* specification
  (``Function.is_noexcept_fact`` ``NOT_COLLECTED``), never a guessed ``False``;
* ``concepts``: the clang concept collector's unfiltered entries
  (``clang/concept_constraints.concept_constraint_entries``) or ``null``. The
  castxml parser filters them by its own public set.

The clang result is a sidecar in the AST cache under the castxml cache key,
like :mod:`.vector_join`; a missing or malformed sidecar is a miss. A clang
failure (no clang, a parse error) is logged and leaves both maps ``null``, so
the facts read unknown and the assurance rollup names them.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element

from ....errors import SnapshotError
from ....model.execution_cache import memoized
from ....storage.ast_cache_location import ast_cache_store
from ... import cache_dep_manifest
from ..clang.ast_inner import ast_inner
from ..clang.backend import clang_header_dump
from ..clang.concept_constraints import concept_constraint_entries
from ..clang.functions import _function_qualifiers
from ..clang.qualifiers import is_noexcept_qualifier
from ..concept_scan import concept_header_scan
from .clang_semantics import SEMANTICS_ATTR

log = logging.getLogger(__name__)

__all__ = [
    "SEMANTICS_SIDECAR_BACKEND",
    "clang_noexcept_map",
    "join_clang_semantics",
    "mentions_exception_spec",
    "special_member_key",
]

#: The AST-cache namespace of the sidecar, distinct from the castxml XML entry.
SEMANTICS_SIDECAR_BACKEND = "castxml-clang-semantics"

#: Text that can introduce an exception specification, directly or through a
#: macro named after it (``noexcept``, ``_NOEXCEPT``, ``Q_DECL_NOTHROW``,
#: glibc's ``__THROW``, a dynamic ``throw()``).
_EXCEPTION_SPEC_TEXT = re.compile(rb"noexcept|nothrow|throw", re.IGNORECASE)

_FUNCTION_KINDS = frozenset(
    {
        "FunctionDecl",
        "CXXMethodDecl",
        "CXXConstructorDecl",
        "CXXDestructorDecl",
        "CXXConversionDecl",
    }
)


def _file_stamp(path: str) -> tuple[int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


@memoized(maxsize=4096, witness=_file_stamp)
def _file_mentions_exception_spec(path: str) -> bool:
    try:
        return _EXCEPTION_SPEC_TEXT.search(Path(path).read_bytes()) is not None
    except OSError:
        return True  # unreadable: cannot rule a specification out


def mentions_exception_spec(path: str) -> bool:
    """Whether *path*'s text can spell an exception specification (an
    unreadable file counts: the gate may only skip what it can rule out)."""
    return _file_mentions_exception_spec(path)


def _local_roots(headers: Iterable[Path]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    files: list[str] = []
    dirs: list[str] = []
    for header in headers:
        resolved = os.path.realpath(header)
        if os.path.isdir(resolved):
            dirs.append(resolved.rstrip(os.sep) + os.sep)
        else:
            files.append(resolved)
            dirs.append(os.path.dirname(resolved).rstrip(os.sep) + os.sep)
    return tuple(files), tuple(dirs)


def _mention_files(root: Element, headers: Iterable[Path]) -> frozenset[str]:
    """The castxml ``File`` names that are local to *headers* and spell an
    exception specification."""
    files, dirs = _local_roots(headers)
    out: set[str] = set()
    for el in root.iter("File"):
        name = el.get("name") or ""
        if not name or name.startswith("<"):
            continue
        real = os.path.realpath(name)
        local = real in files or any(real.startswith(d) for d in dirs)
        if local and mentions_exception_spec(real):
            out.add(name)
    return frozenset(out)


def special_member_key(tag: str, class_name: str, params: Iterable[object]) -> str:
    """The join key of a constructor or destructor, which castxml writes with
    no ``mangled`` attribute: ``ctor:<class>#<arity>`` / ``dtor:<class>``
    (``""`` for any other element)."""
    if tag == "Destructor" and class_name:
        return f"dtor:{class_name}"
    if tag == "Constructor" and class_name:
        return f"ctor:{class_name}#{sum(1 for _ in params)}"
    return ""


_SCOPES = frozenset({"NamespaceDecl", "CXXRecordDecl", "ClassTemplateDecl"})


def _clang_special_key(node: dict[str, Any], scope: tuple[str, ...]) -> str:
    kind = node.get("kind")
    owner = "::".join(scope)
    if kind == "CXXDestructorDecl":
        return f"dtor:{owner}" if owner else ""
    if kind == "CXXConstructorDecl" and owner:
        arity = sum(
            1
            for c in ast_inner(node)
            if isinstance(c, dict) and c.get("kind") == "ParmVarDecl"
        )
        return f"ctor:{owner}#{arity}"
    return ""


def clang_noexcept_map(clang_root: dict[str, Any]) -> dict[str, bool]:
    """``{key: is_noexcept}`` for every explicit clang function declaration,
    keyed by its mangled name and, for a constructor or destructor, also by
    :func:`special_member_key`. A key two declarations read differently (two
    same-arity constructor overloads, say) is left out: unknown."""
    out: dict[str, bool] = {}
    conflicting: set[str] = set()

    def record(key: str, value: bool) -> None:
        if out.get(key, value) != value:
            conflicting.add(key)
        out[key] = value

    def walk(node: Any, scope: tuple[str, ...]) -> None:
        if isinstance(node, list):
            for child in node:
                walk(child, scope)
            return
        if not isinstance(node, dict):
            return
        kind = node.get("kind")
        if kind in _FUNCTION_KINDS and not node.get("isImplicit"):
            quals = _function_qualifiers(
                str((node.get("type") or {}).get("qualType", ""))
            )
            value = is_noexcept_qualifier(quals)
            mangled = node.get("mangledName")
            if isinstance(mangled, str) and mangled:
                record(mangled, value)
            special = _clang_special_key(node, scope)
            if special:
                record(special, value)
            return
        child_scope = scope
        name = str(node.get("name") or "")
        if kind in _SCOPES and name:
            # A template's own pattern record repeats the template's name.
            if not (kind == "CXXRecordDecl" and scope and scope[-1] == name):
                child_scope = (*scope, name)
        walk(ast_inner(node), child_scope)

    walk(ast_inner(clang_root), ())
    for name in conflicting:
        out.pop(name, None)
    return out


def _valid_sidecar(
    payload: Any,
) -> tuple[dict[str, bool], list[tuple[str, str, str]]] | None:
    if not isinstance(payload, dict):
        return None
    noexcept, concepts = payload.get("noexcept"), payload.get("concepts")
    if not isinstance(noexcept, dict) or not all(
        isinstance(k, str) and isinstance(v, bool) for k, v in noexcept.items()
    ):
        return None
    if not isinstance(concepts, list) or not all(
        isinstance(e, list) and len(e) == 3 and all(isinstance(x, str) for x in e)
        for e in concepts
    ):
        return None
    return noexcept, [(e[0], e[1], e[2]) for e in concepts]


def _with_semantics(
    root: Element,
    noexcept: dict[str, bool] | None,
    mention_files: frozenset[str],
    concepts: list[tuple[str, str, str]] | None,
) -> Element:
    carried = Element(root.tag, dict(root.attrib))
    carried.set(
        SEMANTICS_ATTR,
        json.dumps(
            {
                "noexcept": noexcept,
                "mention_files": sorted(mention_files),
                "concepts": None if concepts is None else [list(e) for e in concepts],
            },
            sort_keys=True,
        ),
    )
    carried.extend(list(root))
    carried.text = root.text
    carried.tail = root.tail
    return carried


_TEMPLATE_TEXT = re.compile(rb"\b(?:template|auto)\b")


def _spells_template(headers: Iterable[Path]) -> bool:
    """Whether a given header's text spells ``template`` or ``auto`` (an
    abbreviated function template has no ``template`` keyword); unreadable
    counts."""
    for header in headers:
        try:
            if _TEMPLATE_TEXT.search(Path(header).read_bytes()):
                return True
        except OSError:
            return True
    return False


def _is_cxx_root(root: Element) -> bool:
    """Whether castxml parsed C++ (any C++-only element or a mangled name)."""
    for el in root:
        if el.tag in ("Namespace", "Class", "Method", "Constructor", "Destructor"):
            if el.tag != "Namespace" or el.get("name") not in ("::", None):
                return True
        mangled = el.get("mangled") or ""
        if mangled.startswith(("_Z", "?")):
            return True
    return False


def join_clang_semantics(
    root: Element,
    key: str,
    *,
    headers: list[Path],
    extra_includes: list[Path],
    compiler: str,
    gcc_path: str | None,
    gcc_prefix: str | None,
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
    sysroot: Path | None,
    nostdinc: bool,
    lang: str | None,
    extra_hash_dirs: tuple[Path, ...],
    exported_symbols: frozenset[str],
) -> Element:
    """Return *root* carrying clang's ``noexcept`` and concept facts.

    A C parse, or a C++ parse none of whose local headers spells an exception
    specification, a concept or a ``requires``-clause, is returned untouched
    and costs nothing: castxml's values stand.
    """
    if lang == "c" or not _is_cxx_root(root):
        return root
    mention_files = _mention_files(root, headers)
    # A template with no constraint is recorded too (the empty set), so the
    # baseline of a requires-clause added later is modelled: any template
    # (abbreviated ones included) counts, not only a constrained one.
    wants_concepts = concept_header_scan(headers) or _spells_template(headers)
    if not mention_files and not wants_concepts:
        return root
    store = ast_cache_store(key, SEMANTICS_SIDECAR_BACKEND)
    entry = store.path(key)
    cached = (
        _valid_sidecar(store.lookup(key))
        if cache_dep_manifest.entry_valid(entry)
        else None
    )
    if cached is not None:
        cache_dep_manifest.note(cache_dep_manifest.recall(str(entry), entry))
        noexcept, concepts = cached
        return _with_semantics(root, noexcept, mention_files, concepts)
    try:
        with cache_dep_manifest.recording() as clang_parse:
            clang_root, _kind, _force_cpp = clang_header_dump(
                headers,
                extra_includes,
                compiler,
                gcc_path=gcc_path,
                gcc_prefix=gcc_prefix,
                gcc_options=gcc_options,
                gcc_option_tokens=gcc_option_tokens,
                sysroot=sysroot,
                nostdinc=nostdinc,
                lang=lang,
                extra_hash_dirs=extra_hash_dirs,
                exported_symbols=exported_symbols,
            )
    except (SnapshotError, OSError) as exc:
        log.warning(
            "clang could not parse the header(s) for noexcept specifications and "
            "C++20 constraints (%s); castxml records neither, so they are reported "
            "as not observed",
            exc,
        )
        return _with_semantics(root, None, mention_files, None)
    noexcept = clang_noexcept_map(clang_root)
    concepts = concept_constraint_entries(clang_root)
    deps = clang_parse.manifest()
    if deps is not None and cache_dep_manifest.write_sidecar(entry, deps):
        store.publish(
            key, {"noexcept": noexcept, "concepts": [list(e) for e in concepts]}, ()
        )
    return _with_semantics(root, noexcept, mention_files, concepts)
