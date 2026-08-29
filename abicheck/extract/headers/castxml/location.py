# Copyright 2026 Nikolay Petrov
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

"""Source-location and built-in-origin resolution for the castxml backend.

Every function here takes a :class:`~.context.CastxmlParserContext`
explicitly rather than reading it off ``self`` — the D9 "entity modules ...
using shared context" shape, applied to the location-resolution
responsibility rather than a specific entity kind, since ``is_builtin_element``/
``source_location`` are read by more than one entity's parsing (functions,
variables, records, enums today; only enums have moved out of
``dumper_castxml.py`` so far).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element

from ....model import AccessLevel, ScopeOrigin, Visibility
from ....name_classification import strip_anonymous_type_location
from ....provenance import classify_origin, header_from_location
from .context import CastxmlParserContext


def is_builtin_element(ctx: CastxmlParserContext, el: Element) -> bool:
    """Return True if element originates from a compiler built-in pseudo-file.

    Real castxml output: elements carry a ``file`` attribute (e.g. ``file="f0"``)
    pointing directly to a ``File`` element in the id-map — NOT via a separate
    ``Location`` element.  The compound ``location`` attribute (``"f0:0"``) is
    informational only and is NOT a map key.

    Known built-in file names emitted by castxml:
    - ``<builtin>``       (clang/castxml built-in declarations)
    - ``<built-in>``      (older castxml / GCC)
    - ``<command-line>``  (preprocessor command-line defines)
    """
    file_id = el.get("file", "")
    if not file_id:
        return False
    file_el = ctx.id_map.get(file_id)
    if file_el is None:
        return False
    fname = file_el.get("name", "")
    return fname in ("<builtin>", "<built-in>", "<command-line>")


def source_location(ctx: CastxmlParserContext, el: Any) -> str | None:
    """Resolve a declaration's ``file:line`` source location.

    Mirrors the function-parsing path: castxml emits the location either
    directly as ``file``/``line`` attributes or as a ``location`` id
    referencing a ``Location`` element. Returns ``None`` when neither is
    present. Used to populate provenance (``source_header``/``origin``)
    on records, variables, and enums (ADR-015 v6).
    """
    file_id = el.get("file", "")
    line = el.get("line", "")
    if not (file_id and line):
        loc_id = el.get("location", "")
        loc_el = ctx.id_map.get(loc_id) if loc_id else None
        if loc_el is not None:
            file_id = loc_el.get("file", "")
            line = loc_el.get("line", "")
    file_el = ctx.id_map.get(file_id) if file_id else None
    fname = file_el.get("name", "") if file_el is not None else ""
    return f"{fname}:{line}" if fname and line else None


def optional_int_attr(el: Any, attr: str) -> int | None:
    raw = el.get(attr)
    return int(raw) if raw and raw.isdigit() else None


def deprecation_marker(el: Element) -> str | None:
    """Deprecation message for *el*, or ``None`` if not deprecated.

    castxml's ``GetDeclAttributes`` (``Output.cxx``) always adds a bare
    ``"deprecated"`` token to the compound ``attributes`` string when
    ``DeprecatedAttr`` is present, but only emits the dedicated
    ``deprecation="..."`` XML attribute when the attribute carries a
    non-empty message. A BARE ``[[deprecated]]``/
    ``__attribute__((deprecated))`` (no message) therefore has NO
    ``deprecation`` attribute at all — reading only ``el.get("deprecation")``
    missed every messageless deprecation (Codex review, PR #582, confirmed
    against castxml's own source). Falls back to ``""`` (deprecated, no
    message) when the bare token is present in ``attributes`` instead.
    """
    msg = el.get("deprecation")
    if msg is not None:
        return msg
    if re.search(r"\bdeprecated\b", el.get("attributes", "")):
        return ""
    return None


_CONTRACT_ATTRIBUTE_BASES = frozenset(
    {
        "noreturn",
        "nonnull",
        "returns_nonnull",
        "malloc",
        "format",
        "format_arg",
        "alloc_size",
        "alloc_align",
        "warn_unused_result",
        "sentinel",
        # calling-convention selections — a flip is an ABI change on the
        # affected targets, reported via the contract-attribute kinds.
        "cdecl",
        "stdcall",
        "fastcall",
        "thiscall",
        "regparm",
        "ms_abi",
        "sysv_abi",
        "vectorcall",
    }
)


def contract_attributes(attributes: str) -> list[str]:
    """Filter a castxml ``attributes`` string down to contract attributes.

    Returns normalized, sorted tokens with any ``gnu:``/``gnu::`` namespace
    prefix stripped and argument lists preserved (``nonnull(1)``). Tokens not
    in the known contract set (``noexcept``, ``final``, …) are ignored.

    Read by function and typedef parsing alike (Codex review, PR #940) —
    the same "shared across entity kinds" rule as ``deprecation_marker``
    above, moved here rather than duplicated or left importable only from
    the still-flat, unmigrated ``dumper_castxml_typedefs.py`` sibling
    module ``abicheck/extract/AGENTS.md`` says a migrated entity module
    must not reach into.
    """
    tokens: set[str] = set()
    for raw in attributes.split():
        token = raw
        for prefix in ("gnu::", "gnu:", "__"):
            if token.startswith(prefix):
                token = token[len(prefix) :]
        token = token.strip("_")
        base = token.split("(", 1)[0]
        if base in _CONTRACT_ATTRIBUTE_BASES:
            tokens.add(token)
    return sorted(tokens)


def source_line_has_explicit(
    ctx: CastxmlParserContext,
    loc_el: Element | None,
    declaration_el: Element | None = None,
) -> bool | None:
    """Fallback for castxml Converter nodes that omit explicit="1"."""
    if loc_el is not None:
        file_id = loc_el.get("file", "")
        line_raw = loc_el.get("line", "")
    elif declaration_el is not None:
        file_id = declaration_el.get("file", "")
        line_raw = declaration_el.get("line", "")
    else:
        return None
    file_el = ctx.id_map.get(file_id)
    if file_el is None:
        return None
    fname = file_el.get("name", "")
    if not fname or not line_raw:
        return None
    try:
        line_no = int(line_raw)
        lines = ctx.source_lines_cache.get(fname)
        if lines is None:
            lines = Path(fname).read_text(encoding="utf-8").splitlines()
            ctx.source_lines_cache[fname] = lines
    except (OSError, UnicodeDecodeError, ValueError, IndexError):
        return None
    # CastXML can point a split conversion operator at the ``operator``
    # line, while the ``explicit`` keyword is on the preceding line.
    start = max(0, line_no - 4)
    window_parts: list[str] = []
    for line in lines[start : min(len(lines), line_no + 5)]:
        window_parts.append(line.strip())
        if line_no - 1 <= start + len(window_parts) - 1 and (
            ";" in line or "{" in line
        ):
            break
    window = " ".join(window_parts)
    operator_match = re.search(r"\boperator\b", window)
    if operator_match is None:
        return False
    prefix = window[: operator_match.start()]
    declaration_start = max(prefix.rfind(";"), prefix.rfind("{"), prefix.rfind("}"))
    return bool(re.search(r"\bexplicit\b", prefix[declaration_start + 1 :]))


def qualified_name(ctx: CastxmlParserContext, el: Any) -> str:
    """Namespace/class-qualified name by walking ``context`` (bare name for a
    global; stops at ``"::"``). Segments are stripped via
    :func:`~abicheck.name_classification.strip_anonymous_type_location`,
    matching :func:`~.type_resolution.qualified_type_name`.

    Read by more than one entity kind's parsing (functions, constants,
    typedefs today), so it lives here rather than in ``functions.py`` — the
    same "shared across entity kinds" rule this module's own docstring
    states for ``is_builtin_element``/``source_location``.
    """
    parts = [strip_anonymous_type_location(el.get("name", ""))]
    ctx_id = el.get("context", "")
    seen: set[str] = set()
    while ctx_id and ctx_id not in seen:
        seen.add(ctx_id)
        parent = ctx.id_map.get(ctx_id)
        if parent is None:
            break
        cname = strip_anonymous_type_location(parent.get("name", ""))
        if cname and cname != "::":
            parts.append(cname)
        ctx_id = parent.get("context", "")
    return "::".join(reversed(parts))


def decl_is_public(ctx: CastxmlParserContext, el: Any) -> bool:
    """True if *el*'s declaring header classifies as a public header.

    Uses the shared provenance segment matcher (suffix/basename/public-dir
    containment), so build-prefixed paths and umbrella-included public
    headers match while system/private headers do not. Read by more than
    one entity kind's parsing (functions, variables/constants, typedefs
    today), same as :func:`qualified_name` above.
    """
    sh = header_from_location(source_location(ctx, el))
    if not sh:
        return False
    return (
        classify_origin(
            sh,
            ctx.pub_header_segs,
            ctx.pub_dir_segs,
            have_public_set=ctx.have_public_set,
        )
        == ScopeOrigin.PUBLIC_HEADER
    )


def visibility(ctx: CastxmlParserContext, mangled: str, name: str = "") -> Visibility:
    """Determine visibility based on ELF symbol tables.

    Read by more than one entity kind's parsing (functions, variables
    today) — see :func:`qualified_name` above for why this lives here
    rather than in one entity module.
    """
    if mangled and mangled in ctx.exported_dynamic:
        return Visibility.PUBLIC
    if name and name in ctx.exported_dynamic:
        return Visibility.PUBLIC
    if mangled and mangled in ctx.exported_static:
        return Visibility.ELF_ONLY
    if name and name in ctx.exported_static:
        return Visibility.ELF_ONLY
    return Visibility.HIDDEN


def access_level(el: Element) -> AccessLevel:
    """Map a castxml ``access`` attribute to :class:`AccessLevel`.

    Pure function over a single element's ``access`` attribute; read by
    every entity kind that carries member/declaration access (functions,
    fields today).
    """
    raw = el.get("access", "public")
    if raw == "protected":
        return AccessLevel.PROTECTED
    if raw == "private":
        return AccessLevel.PRIVATE
    return AccessLevel.PUBLIC
