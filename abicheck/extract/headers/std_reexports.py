# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""The public headers' namespace-scope ``using std::X;`` re-exports.

``namespace lib { using std::abs; }`` makes ``lib::abs`` a name consumer
source can spell; deleting the using-declaration breaks that source even
though nothing exported changes (``std_reexport_removed``). No function or
type record carries it -- the entity it names belongs to ``std`` -- so each
header backend reports it here as ``{declared name: std:: target}``
(``AbiSnapshot.std_reexports``):

* castxml lists a re-exported entity among the declaring namespace's
  ``members`` while the entity's own ``context`` stays in ``std``;
* clang writes a ``UsingDecl`` (with its ``UsingShadowDecl`` targets) in the
  namespace;
* the native GCC plugin reads the namespace's binding table, where a
  using-declaration binds the target declared in ``std``
  (``extract/headers/gcc/projection.py``).

Only a re-export a public header declares is recorded. clang states where
the using-declaration itself is; castxml and GCC do not, so there the
declaring namespace's own declarations stand in for its provenance
(:func:`namespace_origin`): a public one makes its re-exports public, only
non-public ones make them private, and none at all leaves the provenance
unknown -- the whole table is then ``None`` (uncertain), never a table that
silently omits the re-export (which would read as a removal).
The declaring namespace must be a library one: not the global namespace, not
``std``, not a reserved ``_``-prefixed implementation namespace. ``None``
means not modelled or not establishable (no AST, no declared public set, or
a re-export of unknown provenance).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any
from xml.etree.ElementTree import Element

from ...model.vocabulary import ScopeOrigin
from ..declaration_provenance import classify_origin
from .clang.ast_inner import ast_inner

__all__ = [
    "castxml_std_reexports",
    "clang_std_reexports",
    "is_library_namespace",
    "namespace_origin",
    "reexport_entry",
]


def is_library_namespace(segments: Iterable[str]) -> bool:
    """Whether a namespace path can declare a library re-export."""
    segs = tuple(segments)
    return bool(segs) and segs[0] != "std" and not segs[0].startswith("_")


def reexport_entry(namespace: Iterable[str], target: str) -> tuple[str, str] | None:
    """``(declared, target)`` for a re-export of *target* in *namespace*, or
    ``None`` when it is not a library re-export of a ``std::`` entity."""
    segs = tuple(namespace)
    target = target.removeprefix("::")
    if not is_library_namespace(segs) or not target.startswith("std::"):
        return None
    return "::".join((*segs, target.rsplit("::", 1)[-1])), target


def _public(
    header: str | None,
    pub_header_segs: list[tuple[str, ...]],
    pub_dir_segs: list[tuple[str, ...]],
) -> bool:
    return (
        bool(header)
        and classify_origin(header, pub_header_segs, pub_dir_segs, have_public_set=True)
        is ScopeOrigin.PUBLIC_HEADER
    )


def namespace_origin(
    own_files: Iterable[str | None],
    is_public: Callable[[str | None], bool],
) -> bool | None:
    """Whether a namespace whose using-declarations carry no position of
    their own (castxml, GCC) declares public re-exports.

    ``True`` when it holds a declaration of its own from a public header,
    ``False`` when its own declarations are all from non-public headers, and
    ``None`` -- provenance unknown -- when it holds no positioned declaration
    of its own: the re-export's presence must not hinge on an unrelated
    declaration, so the backend's table is then uncertain, not empty.
    """
    files = [f for f in own_files if f]
    if not files:
        return None
    return any(is_public(f) for f in files)


# ---------------------------------------------------------------------------
# castxml
# ---------------------------------------------------------------------------

_CASTXML_ENTITY_TAGS = frozenset(
    {
        "Function",
        "Variable",
        "Struct",
        "Class",
        "Union",
        "Enumeration",
        "Typedef",
    }
)


def castxml_std_reexports(
    root: object,
    pub_header_segs: list[tuple[str, ...]],
    pub_dir_segs: list[tuple[str, ...]],
    have_public_set: bool,
) -> dict[str, str] | None:
    """The re-exports of a castxml XML *root*; ``None`` when not modelled."""
    if not isinstance(root, Element) or not have_public_set:
        return None
    by_id = {el.get("id", ""): el for el in root}
    files = {el.get("id", ""): el.get("name", "") for el in root if el.tag == "File"}

    def path(el: Element) -> tuple[str, ...] | None:
        """The namespace path of a Namespace element; None below a record."""
        out: list[str] = []
        cur: Element | None = el
        while cur is not None and cur.tag == "Namespace":
            name = cur.get("name", "")
            if name == "::":
                return tuple(reversed(out))
            if not name:
                return None  # anonymous namespace: internal linkage
            out.append(name)
            cur = by_id.get(cur.get("context", ""))
        return None

    out: dict[str, str] = {}
    for ns in root:
        if ns.tag != "Namespace":
            continue
        segs = path(ns)
        if segs is None or not is_library_namespace(segs):
            continue
        own_id = ns.get("id", "")
        members = [by_id.get(m) for m in ns.get("members", "").split()]
        entries: list[tuple[str, str]] = []
        for m in members:
            if m is None or m.tag not in _CASTXML_ENTITY_TAGS:
                continue
            if m.get("context") == own_id or not m.get("name"):
                continue
            owner = by_id.get(m.get("context", ""))
            owner_path = path(owner) if owner is not None else None
            if not owner_path or owner_path[0] != "std":
                continue
            entry = reexport_entry(segs, "::".join((*owner_path, m.get("name", ""))))
            if entry is not None:
                entries.append(entry)
        if not entries:
            continue
        origin = namespace_origin(
            (
                files.get(m.get("file", ""))
                for m in members
                if m is not None and m.tag != "Namespace" and m.get("context") == own_id
            ),
            lambda f: _public(f, pub_header_segs, pub_dir_segs),
        )
        if origin is None:
            return None  # the re-exports' provenance cannot be established
        if origin:
            for entry in entries:
                out.setdefault(*entry)
    return dict(sorted(out.items()))


# ---------------------------------------------------------------------------
# clang -ast-dump=json
# ---------------------------------------------------------------------------


def _loc_file(node: dict[str, Any], current: str) -> str:
    """The file clang last wrote, carried forward (clang writes ``file`` only
    where it changes, in ``loc`` and ``range`` alike).

    A macro-expanded location carries ``spellingLoc`` (the macro definition,
    possibly a private header) and ``expansionLoc`` (where the declaration
    lands); clang writes them in that order, and the expansion file is the
    one that decides where the declaration is, so it is read last."""
    for key in ("loc", "range"):
        part = node.get(key)
        if not isinstance(part, dict):
            continue
        spots: list[Any] = (
            [part, part.get("spellingLoc"), part.get("expansionLoc")]
            if key == "loc"
            else [part.get("begin"), part.get("end")]
        )
        for spot in spots:
            if isinstance(spot, dict):
                for sub in (spot, spot.get("spellingLoc"), spot.get("expansionLoc")):
                    if isinstance(sub, dict) and isinstance(sub.get("file"), str):
                        current = sub["file"] or current
    return current


def clang_std_reexports(
    root: object,
    pub_header_segs: list[tuple[str, ...]],
    pub_dir_segs: list[tuple[str, ...]],
    have_public_set: bool,
) -> dict[str, str] | None:
    """The re-exports of a clang JSON AST *root*; ``None`` when not modelled."""
    if not isinstance(root, dict) or not have_public_set:
        return None
    out: dict[str, str] = {}
    file_of_using: Callable[[str | None], bool] = lambda f: _public(  # noqa: E731
        f, pub_header_segs, pub_dir_segs
    )

    def walk(node: Any, scope: tuple[str, ...] | None, file: str) -> str:
        if not isinstance(node, dict):
            return file
        before = file
        file = _loc_file(node, file)
        kind = node.get("kind")
        if kind == "UsingDecl" and scope is not None:
            # The using-declaration's own position: its loc, or, when clang
            # wrote no file of its own, the file in effect before it.
            target = str(node.get("name") or "")
            if file_of_using(file or before):
                entry = reexport_entry(scope, target)
                if entry is not None:
                    out.setdefault(*entry)
        child_scope = scope
        if kind == "NamespaceDecl":
            name = str(node.get("name") or "")
            child_scope = None if not name or scope is None else (*scope, name)
        elif kind != "TranslationUnitDecl" and kind != "LinkageSpecDecl":
            child_scope = None  # below a record or function: not namespace scope
        for child in ast_inner(node):
            file = walk(child, child_scope, file)
        return file

    walk(root, (), "")
    return dict(sorted(out.items()))
