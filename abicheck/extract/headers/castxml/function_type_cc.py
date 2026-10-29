# SPDX-License-Identifier: Apache-2.0
"""Calling conventions of function types, joined from the clang AST.

castxml 0.7 writes a function type's calling convention into its
``attributes`` string only for the x86-32 conventions (``stdcall``,
``fastcall``, ``thiscall``). A GNU x86-64 selection such as
``__attribute__((ms_abi))`` or ``__attribute__((sysv_abi))`` is dropped from
the XML altogether, so ``void (__attribute__((ms_abi)) *p)(int)`` and
``void (*p)(int)`` are the same ``FunctionType`` to the castxml backend and
a header-only comparison of the two reads ``NO_CHANGE`` (the calling
convention decides the argument registers: ``%rcx`` against ``%rdi``).

The clang AST of the same header keeps the convention: its ``qualType`` of
the declaration ends in ``__attribute__((ms_abi))``. This module keys each
castxml ``FunctionType`` node by the declaration that reaches it -- a field
(``field:``), a typedef (``typedef:``) or a parameter (``param:``), in the
same key grammar :mod:`.vector_spellings` uses -- and reads the matching
convention from the clang declaration. The mapping is node id -> convention
name and is carried on the parsed root, exactly like the vector spellings.

A pointer reached through a typedef is keyed by the typedef: its convention
is read from the typedef's own qualType, so the parameter that names the typedef
is not keyed again (the walk stops at a ``Typedef``).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element

from ....model.cc_attributes import CC_ATTRIBUTE_BASES
from ....model.execution_cache import memoized
from ..clang.ast_inner import ast_inner
from .vector_spellings import (
    _castxml_function_key,
    _castxml_scope,
    _clang_qualified,
)

__all__ = [
    "FUNCTION_CC_ATTR",
    "castxml_function_type_keys",
    "clang_function_type_cc",
    "has_function_type_nodes",
    "load_function_type_cc",
    "resolve_function_type_cc",
    "trailing_calling_convention",
    "with_function_type_cc",
]

#: Root attribute :func:`with_function_type_cc` carries the node-id -> name map
#: under. The parsed tree itself is never written.
FUNCTION_CC_ATTR = "abicheck-function-type-cc"

#: castxml's declarator wrappers around a ``FunctionType`` (the chain a
#: declaration's type walks before it reaches the function type).
_DECLARATOR_WRAPPERS = frozenset(
    {
        "PointerType",
        "ReferenceType",
        "RValueReferenceType",
        "CvQualifiedType",
        "ArrayType",
    }
)

#: A declaration qualType's trailing ``__attribute__((name))`` -- the
#: attribute clang prints after the function type it applies to.
_TRAILING_ATTRIBUTE = re.compile(
    r"\s__attribute__\(\((?P<body>[^()]*(?:\([^()]*\))?)\)\)\s*$"
)


def trailing_calling_convention(qual_type: str) -> str | None:
    """The calling convention a clang ``qualType`` ends with, or ``None``.

    ``void (*)(int) __attribute__((ms_abi))`` -> ``ms_abi``;
    ``void (*)(int) __attribute__((regparm(2)))`` -> ``regparm(2)``. A
    trailing attribute that is not a calling convention, or no trailing
    attribute at all, is ``None``.
    """
    match = _TRAILING_ATTRIBUTE.search(qual_type)
    if match is None:
        return None
    body = match.group("body").strip()
    base = body.split("(", 1)[0].strip()
    return body if base in CC_ATTRIBUTE_BASES else None


def _function_type_id(index: dict[str, Element], type_id: str) -> str | None:
    """The id of the ``FunctionType`` a declaration's type reaches, or ``None``.

    Walks declarator wrappers only; a ``Typedef`` stops the walk (its
    convention is keyed on the typedef itself).
    """
    node = index.get(type_id)
    for _ in range(12):
        if node is None:
            return None
        if node.tag == "FunctionType":
            return node.get("id") or None
        if node.tag not in _DECLARATOR_WRAPPERS:
            return None
        node = index.get(node.get("type", ""))
    return None


def castxml_function_type_keys(root: Element) -> dict[str, str]:
    """castxml ``FunctionType`` node id -> the key of the declaration reaching it.

    Keys use the grammar of :func:`.vector_spellings.clang_vector_spellings`:
    ``typedef:<name>``, ``field:<scope>::<name>`` and ``param:<function>#<index>``.
    """
    index: dict[str, Element] = {el.get("id", ""): el for el in root.iter()}
    keyed: dict[str, str] = {}
    for el in root.iter():
        if el.tag == "Typedef" and el.get("name"):
            target = _function_type_id(index, el.get("type", ""))
            if target:
                keyed.setdefault(target, f"typedef:{el.get('name')}")
        elif el.tag == "Field" and el.get("name"):
            target = _function_type_id(index, el.get("type", ""))
            if target:
                scope = _castxml_scope(el, index)
                keyed.setdefault(target, f"field:{scope}::{el.get('name')}")
        elif el.tag == "Function":
            fn_key = _castxml_function_key(el, index)
            arguments = [child for child in el if child.tag == "Argument"]
            for position, arg in enumerate(arguments):
                target = _function_type_id(index, arg.get("type", ""))
                if target:
                    keyed.setdefault(target, f"param:{fn_key}#{position}")
            target = _function_type_id(index, el.get("returns", ""))
            if target:
                keyed.setdefault(target, f"return:{fn_key}")
    return keyed


def _cc_spelling_re() -> re.Pattern[str]:
    bases = "|".join(sorted(re.escape(b) for b in CC_ATTRIBUTE_BASES))
    return re.compile(
        rf"\b_*(?:{bases})_*\b|\b__(?:cdecl|stdcall|fastcall|thiscall|vectorcall)\b"
    )


_CC_SPELLING_RE = _cc_spelling_re()


def _file_stamp(path: str) -> tuple[int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


@memoized(maxsize=4096, witness=_file_stamp)
def _file_mentions_calling_convention(path: str) -> bool:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True  # unreadable: cannot rule a convention out
    return _CC_SPELLING_RE.search(text) is not None


def files_mention_calling_convention(root: Element) -> bool:
    """Whether any file castxml read for *root* spells a calling convention.

    A function type can carry a convention only if some header (or a macro
    defined in one) names it, so a translation unit none of whose files does
    never needs the clang pass for conventions. Unreadable files count as a
    mention: the gate may only skip work it can prove is unnecessary.
    """
    for el in root.iter("File"):
        name = el.get("name") or ""
        if (
            name
            and not name.startswith("<")
            and _file_mentions_calling_convention(name)
        ):
            return True
    return False


def has_function_type_nodes(root: Element) -> bool:
    """Whether a declaration in *root* reaches a ``FunctionType`` node.

    The gate for the clang pass: a header with no function-type declaration
    (no callback field, typedef or parameter) never needs the clang AST.
    """
    return bool(castxml_function_type_keys(root))


def _record_function(
    node: dict[str, Any], scope: list[str], out: dict[str, str]
) -> str:
    """Key a function's returned and parameter conventions; return its key."""
    qual_type = (node.get("type") or {}).get("qualType", "")
    fn_key = node.get("mangledName") or _clang_qualified(scope, node["name"])
    # A function that returns a function pointer carries the pointee's
    # convention as its trailing attribute too (``void (*(void))(int)
    # __attribute__((ms_abi))``); the ``(*`` before it marks that case.
    ret_cc = trailing_calling_convention(qual_type)
    if ret_cc is not None and "(*" in qual_type:
        out.setdefault(f"return:{fn_key}", ret_cc)
    params = [
        c
        for c in ast_inner(node)
        if isinstance(c, dict) and c.get("kind") == "ParmVarDecl"
    ]
    for index, child in enumerate(params):
        cc = trailing_calling_convention((child.get("type") or {}).get("qualType", ""))
        if cc is not None:
            out.setdefault(f"param:{fn_key}#{index}", cc)
    return fn_key


def _record_declaration(
    node: dict[str, Any], scope: list[str], function: str | None, out: dict[str, str]
) -> None:
    """Key a typedef's or a member field's own trailing convention."""
    kind, name = node.get("kind"), node.get("name") or ""
    if not name or kind not in ("TypedefDecl", "FieldDecl"):
        return
    if kind == "FieldDecl" and function is not None:
        return
    cc = trailing_calling_convention((node.get("type") or {}).get("qualType", ""))
    if cc is None:
        return
    key = (
        f"typedef:{name}"
        if kind == "TypedefDecl"
        else f"field:{_clang_qualified(scope, '')}::{name}"
    )
    out.setdefault(key, cc)


def clang_function_type_cc(clang_root: dict[str, Any]) -> dict[str, str]:
    """Declaration key -> calling convention, from a clang ``-ast-dump=json`` root.

    Only a declaration whose qualType ends in a calling-convention attribute
    is keyed; a parameter or field spelled by a typedef name carries no such
    suffix and is left to the typedef's own key.
    """
    out: dict[str, str] = {}

    def walk(node: Any, scope: list[str], function: str | None) -> None:
        if isinstance(node, list):
            for child in node:
                walk(child, scope, function)
            return
        if not isinstance(node, dict):
            return
        kind, name = node.get("kind"), node.get("name") or ""
        if kind == "FunctionDecl" and name:
            walk(ast_inner(node), scope, _record_function(node, scope, out))
            return
        _record_declaration(node, scope, function, out)
        if kind in ("NamespaceDecl", "RecordDecl", "CXXRecordDecl") and name:
            walk(ast_inner(node), [*scope, name], None)
            return
        walk(ast_inner(node), scope, function)

    walk(ast_inner(clang_root), [], None)
    return out


def resolve_function_type_cc(
    root: Element, clang_root: dict[str, Any]
) -> dict[str, str]:
    """Map castxml ``FunctionType`` node ids to clang's calling convention.

    Writes nothing. A function type with no clang declaration, or whose clang
    declaration spells no convention, is omitted (the castxml spelling stands).
    """
    keys = castxml_function_type_keys(root)
    by_key = clang_function_type_cc(clang_root)
    return {node: by_key[key] for node, key in keys.items() if key in by_key}


def with_function_type_cc(root: Element, conventions: dict[str, str]) -> Element:
    """A copy of *root* carrying *conventions* (children shared, not written)."""
    carried = Element(root.tag, dict(root.attrib))
    carried.set(FUNCTION_CC_ATTR, json.dumps(conventions, sort_keys=True))
    carried.extend(list(root))
    carried.text = root.text
    carried.tail = root.tail
    return carried


def load_function_type_cc(root: Element) -> dict[str, str]:
    """The node-id -> convention map :func:`with_function_type_cc` carried, or ``{}``."""
    raw = root.get(FUNCTION_CC_ATTR)
    if not raw:
        return {}
    loaded = json.loads(raw)
    return {str(k): str(v) for k, v in loaded.items()}
