"""Join clang's const variable templates onto a castxml root, cached as a sidecar.

castxml's XML has no element for a variable template (``template <class T>
inline constexpr int vt = 1;``), instantiated or not, so the castxml header tier
never sees its initializer. The clang AST of the same header does. Following
:mod:`.vector_join`, the clang pass runs only when a public header's text can
declare one, its result is a sidecar under the castxml cache key, and a
missing or malformed sidecar is a miss, never "no variable templates".

The joined entries are then written into the castxml root as ``Variable``
elements, so the castxml parser reads them exactly as it reads any
``constexpr`` global: a constant, with its qualified name, value, entity id and
declaring header. Only const arithmetic templates in the given public headers
are joined; a non-const or class-typed template is not a value the constant
channel carries.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element, SubElement

from ....errors import SnapshotError
from ....storage.ast_cache_location import ast_cache_store
from ...arithmetic_type_spellings import is_arithmetic_spelling
from ..clang.backend import clang_header_dump
from ..clang.expr import _initializer_value

log = logging.getLogger(__name__)

VARIABLE_TEMPLATE_SIDECAR_BACKEND = "castxml-variable-templates"

#: A declaration that can be a variable template: ``template <...>`` directly
#: followed by a const-qualified variable with an initializer. A function
#: template has a ``(`` before its ``=``, so it does not match.
_VAR_TEMPLATE_TEXT = re.compile(
    rb"\btemplate\s*<[^;{]*>\s*(?:(?:inline|static|extern)\s+)*"
    rb"(?:constexpr|const)\b[^(;{]*=",
    re.DOTALL,
)

#: ``<stdint.h>``/``<stddef.h>`` names are typedefs, which the castxml root
#: spells as ``Typedef`` elements; only fundamental spellings are synthesized.
_TYPEDEF_SPELLINGS = frozenset(
    {
        "int8_t",
        "uint8_t",
        "int16_t",
        "uint16_t",
        "int32_t",
        "uint32_t",
        "int64_t",
        "uint64_t",
        "size_t",
        "ptrdiff_t",
    }
)


def has_variable_template_text(headers: Iterable[Path]) -> bool:
    """Whether any header's text can declare a const variable template."""
    for header in headers:
        try:
            if _VAR_TEMPLATE_TEXT.search(header.read_bytes()):
                return True
        except OSError:
            continue
    return False


class _Position:
    """The last file and line clang printed. clang omits a ``file``/``line`` that
    repeats the previous node's, so a node's position is the last one seen."""

    def __init__(self) -> None:
        self.file: str | None = None
        self.line: int = 0

    def advance(self, loc: Any) -> None:
        if not isinstance(loc, dict):
            return
        if loc.get("file"):
            self.file = str(loc["file"])
        if loc.get("line") is not None:
            self.line = int(loc["line"])


def clang_const_variable_templates(
    clang_root: dict[str, Any], public_headers: Iterable[Path]
) -> list[dict[str, Any]]:
    """The const arithmetic variable templates declared in *public_headers*.

    Each entry: ``scope`` (enclosing namespace names, outermost first), ``name``,
    ``type`` (fundamental spelling, cv-qualifier stripped), ``init`` (the
    clang initializer spelling), ``file`` and ``line``. A template whose file
    is not one of *public_headers* (a header only included by one of them is
    not), whose type is not fundamental, or that has no initializer is skipped.
    """
    wanted = {_resolved(h) for h in public_headers}
    out: list[dict[str, Any]] = []

    def visit(node: Any, scope: tuple[str, ...], pos: _Position) -> None:
        if isinstance(node, list):
            for child in node:
                visit(child, scope, pos)
            return
        if not isinstance(node, dict):
            return
        pos.advance(node.get("loc"))
        pos.advance((node.get("range") or {}).get("begin"))
        kind = node.get("kind")
        if kind == "NamespaceDecl" and node.get("name"):
            visit(node.get("inner", []), (*scope, node["name"]), pos)
        elif kind == "VarTemplateDecl":
            _maybe_record(node, scope, pos, wanted, out)
        else:
            visit(node.get("inner", []), scope, pos)

    visit(clang_root, (), _Position())
    return out


def _maybe_record(
    node: dict[str, Any],
    scope: tuple[str, ...],
    pos: _Position,
    wanted: set[str],
    out: list[dict[str, Any]],
) -> None:
    decl = next(
        (c for c in node.get("inner", []) or [] if c.get("kind") == "VarDecl"),
        None,
    )
    if decl is None or not decl.get("name") or pos.file is None:
        return
    if _resolved(pos.file) not in wanted:
        return
    type_name = (decl.get("type") or {}).get("qualType", "")
    const = bool(decl.get("constexpr")) or bool(re.search(r"\bconst\b", type_name))
    if not const or not is_arithmetic_spelling(type_name):
        return
    spelling = " ".join(re.sub(r"\b(?:const|volatile)\b", " ", type_name).split())
    if spelling in _TYPEDEF_SPELLINGS:
        return
    init = _initializer_value(decl)
    if init is None:
        return
    out.append(
        {
            "scope": list(scope),
            "name": decl["name"],
            "type": spelling,
            "const": True,
            "init": init,
            "file": pos.file,
            "line": pos.line,
        }
    )


def _resolved(path: Any) -> str:
    try:
        return str(Path(str(path)).resolve())
    except OSError:
        return str(path)


def valid_sidecar(payload: Any) -> list[dict[str, Any]] | None:
    """The stored entries, or ``None`` if the payload is not a well-formed list."""
    if not isinstance(payload, dict) or not isinstance(payload.get("templates"), list):
        return None
    entries = payload["templates"]
    for entry in entries:
        if not (
            isinstance(entry, dict)
            and isinstance(entry.get("scope"), list)
            and all(isinstance(s, str) for s in entry["scope"])
            and isinstance(entry.get("name"), str)
            and isinstance(entry.get("type"), str)
            and isinstance(entry.get("init"), str)
            and isinstance(entry.get("file"), str)
            and isinstance(entry.get("line"), int)
        ):
            return None
    return entries


def with_variable_templates(root: Element, entries: list[dict[str, Any]]) -> Element:
    """Write *entries* into *root* as the ``Variable`` elements castxml would emit.

    Namespaces, the file and the fundamental type are found in *root* where it
    already has them, and created where it does not. An entry whose qualified
    name already has a ``Variable`` in *root* is not added twice.
    """
    if not entries:
        return root
    builder = _Builder(root)
    for entry in entries:
        builder.add(entry)
    return root


class _Builder:
    def __init__(self, root: Element) -> None:
        self._root = root
        self._next = 0
        self._ids = {el.get("id") for el in root.iter() if el.get("id")}
        self._global = next(
            (el for el in root if el.tag == "Namespace" and el.get("name") == "::"),
            None,
        )
        if self._global is None:
            self._global = SubElement(
                root, "Namespace", {"id": self._new_id("ns"), "name": "::"}
            )
        self._types: dict[str, str] = {}
        self._consts: set[tuple[str, ...]] = set()
        for el in root.iter("Variable"):
            if el.get("name") and el.get("context"):
                self._consts.add((el.get("context", ""), el.get("name", "")))

    def _new_id(self, prefix: str) -> str:
        while True:
            self._next += 1
            candidate = f"_{prefix}{self._next}"
            if candidate not in self._ids:
                self._ids.add(candidate)
                return candidate

    def _namespace(self, names: list[str]) -> str:
        parent = self._global.get("id", "")
        for name in names:
            child = next(
                (
                    el
                    for el in self._root
                    if el.tag == "Namespace"
                    and el.get("name") == name
                    and el.get("context") == parent
                ),
                None,
            )
            if child is None:
                child = SubElement(
                    self._root,
                    "Namespace",
                    {"id": self._new_id("ns"), "name": name, "context": parent},
                )
            parent = child.get("id", "")
        return parent

    def _file(self, path: str) -> str:
        found = next(
            (el for el in self._root if el.tag == "File" and el.get("name") == path),
            None,
        )
        if found is not None:
            return found.get("id", "")
        return SubElement(
            self._root, "File", {"id": self._new_id("f"), "name": path}
        ).get("id", "")

    def _type(self, spelling: str) -> str:
        if spelling not in self._types:
            base = SubElement(
                self._root,
                "FundamentalType",
                {"id": self._new_id("t"), "name": spelling},
            ).get("id", "")
            qualified = SubElement(
                self._root,
                "CvQualifiedType",
                {"id": self._new_id("t"), "type": base, "const": "1"},
            )
            self._types[spelling] = qualified.get("id", "")
        return self._types[spelling]

    def add(self, entry: dict[str, Any]) -> None:
        context = self._namespace(entry["scope"])
        if (context, entry["name"]) in self._consts:
            return
        self._consts.add((context, entry["name"]))
        SubElement(
            self._root,
            "Variable",
            {
                "id": self._new_id("v"),
                "name": entry["name"],
                "type": self._type(entry["type"]),
                "init": entry["init"],
                "context": context,
                "file": self._file(entry["file"]),
                "line": str(entry["line"]),
            },
        )


def join_variable_templates(
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
    """Return *root* with the clang-derived const variable templates written in.

    A header whose text cannot declare one costs nothing. A clang failure leaves
    the templates absent and is logged; nothing is published for it.
    """
    if not has_variable_template_text(headers):
        return root
    store = ast_cache_store(key, VARIABLE_TEMPLATE_SIDECAR_BACKEND)
    cached = valid_sidecar(store.lookup(key))
    if cached is not None:
        return with_variable_templates(root, cached)
    try:
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
            "clang could not parse the header(s) for variable templates (%s); "
            "variable templates stay unseen on the castxml path",
            exc,
        )
        return root
    entries = clang_const_variable_templates(clang_root, headers)
    store.publish(key, {"templates": entries}, ())
    return with_variable_templates(root, entries)
