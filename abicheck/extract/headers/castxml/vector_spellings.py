# SPDX-License-Identifier: Apache-2.0
"""Vector-type spellings castxml cannot express, joined from the clang AST.

castxml's XML renders a GCC/Clang vector type (``vector_size``, ``__m256``,
``ext_vector_type``) as ``<Unimplemented type_class="Vector"/>``: no element
type, no lane count, no byte size. The same header parsed by clang keeps all
three, so this module reads the clang JSON AST and produces the spelling
``T __attribute__((vector_size(N)))`` -- the spelling the DWARF path records
(:func:`abicheck.dwarf_utils.array_type_spelling`), so both backends agree on
one vector type.

Keys, one per declaration whose type is a vector:

* ``typedef:<name>`` -- a typedef whose underlying type is the vector.
* ``field:<record>::<field>`` -- an inline vector field (no typedef).
* ``param:<function>#<index>`` -- a parameter (declared with an inline vector).
* ``return:<function>`` -- a return type (declared with an inline vector).

The castxml side maps each vector ``Unimplemented`` node to a key from the
declaration that references it, then to the clang spelling for that key.
Nothing here runs a process: the caller supplies the clang AST root.
"""

from __future__ import annotations

import json
import re
from typing import Any
from xml.etree.ElementTree import Element

from .context import CastxmlParserContext

__all__ = [
    "VECTOR_SPELLINGS_ATTR",
    "resolve_vector_spellings",
    "castxml_vector_spellings",
    "clang_vector_spellings",
    "has_vector_nodes",
    "parse_vector_qualtype",
    "with_vector_spellings",
]

#: The attribute :func:`with_vector_spellings` carries the mapping under, on a
#: copy of the parsed root. The parsed tree itself is never written.
VECTOR_SPELLINGS_ATTR = "abicheck-vector-spellings"

#: Byte size of each element type a vector may carry (LP64 / common ABIs). An
#: element type missing here yields no spelling, so the node keeps its
#: ``Unimplemented`` status instead of a guessed size.
_ELEMENT_BYTES: dict[str, int] = {
    "bool": 1,
    "char": 1,
    "signed char": 1,
    "unsigned char": 1,
    "short": 2,
    "unsigned short": 2,
    "_Float16": 2,
    "int": 4,
    "unsigned int": 4,
    "float": 4,
    "long": 8,
    "unsigned long": 8,
    "long long": 8,
    "unsigned long long": 8,
    "double": 8,
    "__int128": 16,
    "unsigned __int128": 16,
}

# clang spells a vector ``__attribute__((__vector_size__(N * sizeof(T))))`` or,
# for a literal size, ``(__vector_size__(M))``; ``ext_vector_type(N)`` counts
# lanes directly. The element type is the text after the attribute.
_VECTOR_SIZE_RE = re.compile(
    r"__attribute__\(\(__vector_size__\((?:(?P<lanes>\d+)\s*\*\s*sizeof\((?P<elem>[^)]+)\)"
    r"|(?P<bytes>\d+))\)\)\)\s*(?P<type>.+)$"
)
_EXT_VECTOR_RE = re.compile(
    r"__attribute__\(\(ext_vector_type\((?P<lanes>\d+)\)\)\)\s*(?P<type>.+)$"
)


def parse_vector_qualtype(qual_type: str) -> str | None:
    """The ``T __attribute__((vector_size(N)))`` spelling of a clang vector type.

    Returns ``None`` when *qual_type* is not a vector, or its element type has
    no entry in :data:`_ELEMENT_BYTES`.
    """
    text = qual_type.strip()
    match = _VECTOR_SIZE_RE.match(text)
    if match:
        elem = (match.group("elem") or match.group("type")).strip()
        if match.group("lanes") is not None:
            elem = match.group("elem").strip()
            element = _ELEMENT_BYTES.get(elem)
            if element is None:
                return None
            total = int(match.group("lanes")) * element
        else:
            total = int(match.group("bytes"))
            elem = match.group("type").strip()
            if elem not in _ELEMENT_BYTES:
                return None
        return f"{elem} __attribute__((vector_size({total})))"
    match = _EXT_VECTOR_RE.match(text)
    if match:
        elem = match.group("type").strip()
        element = _ELEMENT_BYTES.get(elem)
        if element is None:
            return None
        return (
            f"{elem} __attribute__((vector_size("
            f"{int(match.group('lanes')) * element})))"
        )
    return None


def _clang_qualified(stack: list[str], name: str) -> str:
    return "::".join([*stack, name]) if name else "::".join(stack)


def clang_vector_spellings(clang_root: dict[str, Any]) -> dict[str, str]:
    """Vector spellings by declaration key, from a clang ``-ast-dump=json`` root.

    Typedefs are collected first so a parameter or field spelled by typedef name
    (``f8 x``) resolves through the typedef's own vector spelling.
    """
    typedefs: dict[str, str] = {}
    keyed: list[tuple[str, str]] = []

    def walk(node: Any, scope: list[str], function: str | None) -> None:
        if isinstance(node, list):
            for child in node:
                walk(child, scope, function)
            return
        if not isinstance(node, dict):
            return
        kind = node.get("kind")
        name = node.get("name") or ""
        inner = node.get("inner") or []
        if kind == "TypedefDecl" and name:
            spelling = parse_vector_qualtype(
                (node.get("type") or {}).get("qualType", "")
            )
            if spelling is not None:
                typedefs[name] = spelling
                keyed.append((f"typedef:{name}", spelling))
        elif kind == "FieldDecl" and name and function is None:
            qual_type = (node.get("type") or {}).get("qualType", "")
            spelling = typedefs.get(qual_type) or parse_vector_qualtype(qual_type)
            if spelling is not None and qual_type not in typedefs:
                keyed.append((f"field:{_clang_qualified(scope, '')}::{name}", spelling))
        elif kind == "FunctionDecl" and name:
            fn_key = _clang_qualified(scope, name)
            ret_spelling = _vector_of_function_return(node, typedefs)
            if ret_spelling is not None:
                keyed.append((f"return:{fn_key}", ret_spelling))
            index = 0
            for child in inner:
                if isinstance(child, dict) and child.get("kind") == "ParmVarDecl":
                    qual_type = (child.get("type") or {}).get("qualType", "")
                    spelling = typedefs.get(qual_type) or parse_vector_qualtype(
                        qual_type
                    )
                    if spelling is not None and qual_type not in typedefs:
                        keyed.append((f"param:{fn_key}#{index}", spelling))
                    index += 1
            walk(inner, scope, fn_key)
            return
        if kind in ("NamespaceDecl", "RecordDecl", "CXXRecordDecl") and name:
            walk(inner, [*scope, name], None)
            return
        walk(inner, scope, function)

    walk(clang_root.get("inner") or [], [], None)
    out = dict(keyed)
    # ``typedef:`` entries are authoritative for the typedef name only.
    return out


def _vector_of_function_return(
    node: dict[str, Any], typedefs: dict[str, str]
) -> str | None:
    """The vector return spelling of a function, from its ``T (args)`` qualType."""
    qual_type = (node.get("type") or {}).get("qualType", "")
    head = qual_type.split("(", 1)[0].strip()
    if not head or head == qual_type.strip():
        return None
    return typedefs.get(head) or parse_vector_qualtype(head)


def _castxml_scope(el: Element, ctx_index: dict[str, Element]) -> str:
    """The ``::``-joined enclosing record/namespace names of *el* (global omitted)."""
    names: list[str] = []
    current = el.get("context")
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        parent = ctx_index.get(current)
        if parent is None:
            break
        name = parent.get("name") or ""
        if name and name != "::":
            names.append(name)
        current = parent.get("context")
    return "::".join(reversed(names))


def castxml_vector_spellings(root: Element) -> dict[str, str]:
    """castxml declaration keys for every vector ``Unimplemented`` node in *root*.

    Maps the node id of each vector node to the key of the declaration that
    references it, in the same key grammar :func:`clang_vector_spellings` uses.
    A node reached through a typedef is keyed by the typedef name. A vector node
    referenced directly (inline) is keyed by its field or parameter.
    """
    index: dict[str, Element] = {el.get("id", ""): el for el in root.iter()}
    vector_ids = {
        el.get("id", "")
        for el in root.iter()
        if el.tag == "Unimplemented" and el.get("type_class") == "Vector"
    }
    keyed: dict[str, str] = {}
    for el in root.iter():
        if el.tag == "Typedef":
            target = el.get("type", "")
            if target in vector_ids and el.get("name"):
                keyed.setdefault(target, f"typedef:{el.get('name')}")
        elif el.tag == "Field":
            target = el.get("type", "")
            if target in vector_ids and el.get("name"):
                scope = _castxml_scope(el, index)
                keyed.setdefault(target, f"field:{scope}::{el.get('name')}")
        elif el.tag == "Function":
            fn_key = _castxml_function_key(el, index)
            arguments = [child for child in el if child.tag == "Argument"]
            for position, arg in enumerate(arguments):
                if arg.get("type", "") in vector_ids:
                    keyed.setdefault(arg.get("type", ""), f"param:{fn_key}#{position}")
            ret = el.get("returns", "")
            if ret in vector_ids:
                keyed.setdefault(ret, f"return:{fn_key}")
    return keyed


def _castxml_function_key(el: Element, index: dict[str, Element]) -> str:
    scope = _castxml_scope(el, index)
    name = el.get("name") or ""
    return f"{scope}::{name}" if scope else name


def resolve_vector_spellings(
    root: Element, clang_root: dict[str, Any]
) -> dict[str, str]:
    """Map castxml's vector nodes to clang spellings. Writes nothing.

    Returns the node-id -> spelling mapping. Vector nodes with no clang
    declaration keep their ``Unimplemented`` status (the mapping omits them).
    """
    keys = castxml_vector_spellings(root)
    by_key = clang_vector_spellings(clang_root)
    return {node: by_key[key] for node, key in keys.items() if key in by_key}


def with_vector_spellings(root: Element, spellings: dict[str, str]) -> Element:
    """A new root element carrying *spellings*, sharing *root*'s children.

    The parsed tree (and its interned children) is never written: the copy is
    a fresh element whose children are the original objects.
    """
    carried = Element(root.tag, dict(root.attrib))
    carried.set(VECTOR_SPELLINGS_ATTR, json.dumps(spellings, sort_keys=True))
    carried.extend(list(root))
    carried.text = root.text
    carried.tail = root.tail
    return carried


def has_vector_nodes(root: Element) -> bool:
    """Whether castxml emitted any vector ``Unimplemented`` node in *root*."""
    return any(
        el.tag == "Unimplemented" and el.get("type_class") == "Vector"
        for el in root.iter()
    )


def load_vector_spellings(ctx: CastxmlParserContext) -> dict[str, str]:
    """The mapping :func:`with_vector_spellings` carried on ``ctx.root``, or ``{}``."""
    raw = ctx.root.get(VECTOR_SPELLINGS_ATTR)
    if not raw:
        return {}
    loaded = json.loads(raw)
    return {str(k): str(v) for k, v in loaded.items()}
