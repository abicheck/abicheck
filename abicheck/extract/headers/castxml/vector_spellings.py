# SPDX-License-Identifier: Apache-2.0
"""Fundamental types castxml cannot express, joined from the clang AST.

castxml's XML renders a GCC/Clang vector type (``vector_size``, ``__m256``,
``ext_vector_type``) as ``<Unimplemented type_class="Vector"/>``: no element
type, no lane count, no byte size. The same header parsed by clang keeps all
three, so this module reads the clang JSON AST and produces the spelling
``T __attribute__((vector_size(N)))`` -- the spelling the DWARF path records
(:func:`abicheck.dwarf_utils.array_type_spelling`), so both backends agree on
one vector type.

castxml does the same for a complex type (``type_class="Complex"``, e.g.
``float _Complex``) and a bit-precise integer (``type_class="BitInt"``, e.g.
``_BitInt(8)``). Each is returned as clang spells it. Without the join, every
such parameter, field or return was the one literal ``Unimplemented`` spelling,
so ``float _Complex`` -> ``double _Complex`` compared equal and was a false
clean on the default header lane.

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

from ....model.fundamental_type import is_fundamental_type_spelling
from ..clang.ast_inner import ast_inner
from .context import CastxmlParserContext

__all__ = [
    "VECTOR_SPELLINGS_ATTR",
    "resolve_vector_spellings",
    "clang_vector_spellings",
    "has_vector_nodes",
    "UNEXPRESSIBLE_TYPE_CLASSES",
    "parse_unexpressible_qualtype",
    "parse_vector_qualtype",
    "with_vector_spellings",
]

#: The attribute :func:`with_vector_spellings` carries the mapping under, on a
#: copy of the parsed root. The parsed tree itself is never written.
VECTOR_SPELLINGS_ATTR = "abicheck-vector-spellings"

#: castxml ``type_class`` values of an ``Unimplemented`` node that is a
#: fundamental type castxml cannot spell: a vector (``Vector``), a complex
#: number (``Complex``: ``float _Complex``), and a bit-precise integer
#: (``BitInt``: ``_BitInt(N)``). Each is joined from the clang AST, so none is
#: left as the literal ``Unimplemented`` -- a spelling every such type shares,
#: which made ``float _Complex`` and ``double _Complex`` parameters compare equal.
UNEXPRESSIBLE_TYPE_CLASSES: frozenset[str] = frozenset({"Vector", "Complex", "BitInt"})

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


#: clang's bit-precise integer spelling: ``_BitInt(8)`` / ``unsigned _BitInt(16)``.
_BITINT_RE = re.compile(r"(?:(?:un)?signed\s+)?_BitInt\(\s*\d+\s*\)")
_COMPLEX_KEYWORDS = frozenset({"_Complex", "__complex__"})


def parse_unexpressible_qualtype(qual_type: str) -> str | None:
    """The spelling of a clang type castxml leaves ``Unimplemented``, or ``None``.

    A vector goes through :func:`parse_vector_qualtype`. A complex type
    (``_Complex float``, ``long double _Complex``) or a bit-precise integer
    (``_BitInt(8)``) is returned as clang spells it: it has no element or lane
    data to rebuild, so the spelling is the whole answer. A pointer, array or
    function-declarator form is not returned (its inner node is not keyed).
    """
    vector = parse_vector_qualtype(qual_type)
    if vector is not None:
        return vector
    text = " ".join(qual_type.split())
    if _BITINT_RE.fullmatch(text):
        return text
    if not text or any(ch in text for ch in "*&[]()"):
        return None
    if _COMPLEX_KEYWORDS.isdisjoint(text.split()):
        return None
    return text if is_fundamental_type_spelling(text) else None


def _clang_qualified(stack: list[str], name: str) -> str:
    return "::".join([*stack, name]) if name else "::".join(stack)


_QUALIFIER_SUFFIX_RE = re.compile(
    r"\s*\b(?:const|volatile|restrict|__restrict__|__restrict)\s*$"
)
_ARRAY_SUFFIX_RE = re.compile(r"\s*\[[^\[\]]*\]\s*$")
_POINTER_SUFFIX_RE = re.compile(r"\s*(?:&&|&|\*)\s*$")
_LEADING_CV_RE = re.compile(r"^\s*(?:const|volatile)\s+")


def _strip_pointer_array_cv(text: str) -> str:
    """*text* without its trailing pointer, reference, array and cv declarators.

    ``_Complex float *const[4]`` -> ``_Complex float``. A function declarator
    (``void (*)(int)``) is left as it is: its trailing ``)`` is not stripped.
    """
    while True:
        previous = text
        text = _LEADING_CV_RE.sub("", text)
        text = _QUALIFIER_SUFFIX_RE.sub("", text)
        text = _ARRAY_SUFFIX_RE.sub("", text)
        text = _POINTER_SUFFIX_RE.sub("", text)
        if text == previous:
            return text.strip()


def _declared_spelling(qual_type: str, typedefs: dict[str, str]) -> str | None:
    """The unexpressible spelling of a declaration's type, or ``None``.

    The type may be the complex, bit-int or vector spelling itself, a typedef
    name of one, or a pointer, reference, array or cv form of either. castxml
    leaves the element type ``Unimplemented``, so its spelling is the base's,
    whatever declarator wraps it.
    """
    text = " ".join(qual_type.split())
    if not text:
        return None
    if text in typedefs:
        return typedefs[text]
    direct = parse_unexpressible_qualtype(text)
    if direct is not None:
        return direct
    base = _strip_pointer_array_cv(text)
    if not base or base == text:
        return None
    if base in typedefs:
        return typedefs[base]
    return parse_unexpressible_qualtype(base)


def clang_vector_spellings(clang_root: dict[str, Any]) -> dict[str, str]:
    """Unexpressible-type spellings by declaration key, from a clang ``-ast-dump=json`` root.

    Typedefs are collected first so a parameter or field spelled by typedef name
    (``f8 x``) resolves through the typedef's own spelling.
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
        inner = ast_inner(node)
        if kind == "TypedefDecl" and name:
            spelling = _declared_spelling(
                (node.get("type") or {}).get("qualType", ""), typedefs
            )
            if spelling is not None:
                typedefs[name] = spelling
                keyed.append((f"typedef:{name}", spelling))
        elif kind == "FieldDecl" and name and function is None:
            qual_type = (node.get("type") or {}).get("qualType", "")
            spelling = _declared_spelling(qual_type, typedefs)
            if spelling is not None and qual_type not in typedefs:
                keyed.append((f"field:{_clang_qualified(scope, '')}::{name}", spelling))
        elif kind == "FunctionDecl" and name:
            # The mangled name tells overloads apart; fall back to the scoped name.
            fn_key = node.get("mangledName") or _clang_qualified(scope, name)
            ret_spelling = _vector_of_function_return(node, typedefs)
            if ret_spelling is not None:
                keyed.append((f"return:{fn_key}", ret_spelling))
            index = 0
            for child in inner:
                if isinstance(child, dict) and child.get("kind") == "ParmVarDecl":
                    qual_type = (child.get("type") or {}).get("qualType", "")
                    spelling = _declared_spelling(qual_type, typedefs)
                    if spelling is not None and qual_type not in typedefs:
                        keyed.append((f"param:{fn_key}#{index}", spelling))
                    index += 1
            walk(inner, scope, fn_key)
            return
        if kind in ("NamespaceDecl", "RecordDecl", "CXXRecordDecl") and name:
            walk(inner, [*scope, name], None)
            return
        walk(inner, scope, function)

    walk(ast_inner(clang_root), [], None)
    return dict(keyed)


def _before_parameter_list(qual_type: str) -> str:
    """The text before the last top-level ``(...)`` group, or ``""``.

    A function's qualType ends with its parameter list, but an inline vector
    return carries its own ``__attribute__((...))`` parentheses first, so the
    split must be on the last top-level group, not the first ``(``.
    """
    depth = 0
    start = -1
    for i, ch in enumerate(qual_type):
        if ch == "(":
            if depth == 0:
                start = i
            depth += 1
        elif ch == ")":
            depth -= 1
    return qual_type[:start].strip() if start > 0 else ""


def _vector_of_function_return(
    node: dict[str, Any], typedefs: dict[str, str]
) -> str | None:
    """The unexpressible return spelling of a function, from its ``T (args)`` qualType."""
    qual_type = (node.get("type") or {}).get("qualType", "")
    head = _before_parameter_list(qual_type)
    if not head:
        return None
    return _declared_spelling(head, typedefs)


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


#: castxml nodes a declared type may wrap around the element type it names.
_DECLARATOR_TAGS = frozenset(
    {
        "PointerType",
        "ReferenceType",
        "RValueReferenceType",
        "ArrayType",
        "CvQualifiedType",
        "Typedef",
    }
)


def _unexpressible_under(
    type_id: str, index: dict[str, Element], unexpressible: frozenset[str]
) -> str | None:
    """The unexpressible node a declared type reaches through its declarators.

    Walks ``type`` attributes through pointer, reference, array, cv and typedef
    nodes; returns the first unexpressible ``Unimplemented`` node reached.
    """
    current = type_id
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        if current in unexpressible:
            return current
        node = index.get(current)
        if node is None or node.tag not in _DECLARATOR_TAGS:
            return None
        current = node.get("type", "")
    return None


def castxml_declaration_keys(root: Element) -> dict[str, list[str]]:
    """Every declaration key that reaches each unexpressible node, in document order.

    Keys use the grammar :func:`clang_vector_spellings` uses. A node is reached
    by each declaration whose type wraps it: a typedef, a field or parameter
    (directly or through a pointer, array or typedef chain), or a return. Several
    keys may name one node; the resolver takes the first one clang knows.
    """
    index: dict[str, Element] = {el.get("id", ""): el for el in root.iter()}
    unexpressible = frozenset(
        el.get("id", "")
        for el in root.iter()
        if el.tag == "Unimplemented"
        and el.get("type_class") in UNEXPRESSIBLE_TYPE_CLASSES
    )
    keys: dict[str, list[str]] = {}

    def add(type_id: str, key: str) -> None:
        node = _unexpressible_under(type_id, index, unexpressible)
        if node is None:
            return
        found = keys.setdefault(node, [])
        if key not in found:
            found.append(key)

    for el in root.iter():
        if el.tag == "Typedef" and el.get("name"):
            add(el.get("type", ""), f"typedef:{el.get('name')}")
        elif el.tag == "Field" and el.get("name"):
            scope = _castxml_scope(el, index)
            add(el.get("type", ""), f"field:{scope}::{el.get('name')}")
        elif el.tag == "Function":
            fn_key = _castxml_function_key(el, index)
            arguments = [child for child in el if child.tag == "Argument"]
            for position, arg in enumerate(arguments):
                add(arg.get("type", ""), f"param:{fn_key}#{position}")
            add(el.get("returns", ""), f"return:{fn_key}")
    return keys


def _castxml_function_key(el: Element, index: dict[str, Element]) -> str:
    """The key clang uses for this function: its mangled name when castxml has one."""
    mangled = el.get("mangled")
    if mangled:
        return mangled
    scope = _castxml_scope(el, index)
    name = el.get("name") or ""
    return f"{scope}::{name}" if scope else name


def resolve_vector_spellings(
    root: Element, clang_root: dict[str, Any]
) -> dict[str, str]:
    """Map castxml's unexpressible nodes to clang spellings. Writes nothing.

    A node takes the spelling of the first of its declaration keys that clang
    knows. A node no clang declaration names keeps its ``Unimplemented`` status
    (the mapping omits it).
    """
    by_key = clang_vector_spellings(clang_root)
    resolved: dict[str, str] = {}
    for node, candidates in castxml_declaration_keys(root).items():
        for key in candidates:
            if key in by_key:
                resolved[node] = by_key[key]
                break
    return resolved


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
        el.tag == "Unimplemented" and el.get("type_class") in UNEXPRESSIBLE_TYPE_CLASSES
        for el in root.iter()
    )


def load_vector_spellings(ctx: CastxmlParserContext) -> dict[str, str]:
    """The mapping :func:`with_vector_spellings` carried on ``ctx.root``, or ``{}``."""
    raw = ctx.root.get(VECTOR_SPELLINGS_ATTR)
    if not raw:
        return {}
    loaded = json.loads(raw)
    return {str(k): str(v) for k, v in loaded.items()}
