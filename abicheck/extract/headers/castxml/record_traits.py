"""The trivially-copyable trait of a CastXML record, where CastXML proves it.

CastXML has no trait attribute, but it emits every special member a record
has: an implicitly-declared one with ``artificial="1"``, a user-declared one
without it. It does not say whether a user-declared member was
``= default``-ed on its first declaration (trivial when the implicit one
would be) or user-provided (never trivial), so that one case is read from
the declaration's own text and, when it is defaulted, left unknown.

The answer is tri-state, never a guess:

* ``True`` -- no virtual function or virtual base, every copy/move
  constructor, copy/move assignment and the destructor is implicit, at least
  one copy constructor exists (CastXML omits a deleted one), and every base
  and non-static data member is itself proven trivially copyable;
* ``False`` -- a virtual function or base, a user-provided special member,
  or a base/member proven not trivially copyable;
* ``None`` -- anything else (an incomplete type, a defaulted member, an
  unreadable declaration, a member type CastXML does not resolve).

This is what lets a stripped binary plus headers see
``case69_trivial_to_nontrivial`` (a struct gaining ``~Point() {}`` switches
from register to hidden-pointer passing) as ``trivially_copyable_lost``.
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.etree.ElementTree import Element

from .context import CastxmlParserContext

_RECORD_TAGS = frozenset({"Struct", "Class", "Union"})
#: Types whose values are trivially copyable by definition.
_SCALAR_TAGS = frozenset(
    {"FundamentalType", "Enumeration", "PointerType", "MethodType", "OffsetType"}
)
#: Wrappers whose trait is that of the type they name.
_TRANSPARENT_TAGS = frozenset(
    {"CvQualifiedType", "Typedef", "ElaboratedType", "ArrayType"}
)
_DEFAULTED_RE = re.compile(r"=\s*default\b")
#: How many lines a special member's declaration may span.
_DECL_LINES = 4


def trivially_copyable(ctx: CastxmlParserContext, el: Element) -> bool | None:
    """The trait of record *el* (see the module docstring)."""
    cache = ctx.trivially_copyable_cache
    eid = el.get("id", "")
    if eid in cache:
        return cache[eid]
    cache[eid] = None  # a by-value cycle is impossible; stay safe anyway
    result = _record_trait(ctx, el)
    cache[eid] = result
    return result


def _type_trait(ctx: CastxmlParserContext, type_id: str, depth: int = 0) -> bool | None:
    el = ctx.id_map.get(type_id)
    if el is None or depth > 32:
        return None
    if el.tag in _SCALAR_TAGS:
        return True
    if el.tag in _TRANSPARENT_TAGS:
        return _type_trait(ctx, el.get("type", ""), depth + 1)
    if el.tag in _RECORD_TAGS:
        return trivially_copyable(ctx, el)
    return None


def _referenced_record_id(ctx: CastxmlParserContext, type_id: str) -> str | None:
    """The record a ``cv T &``/``cv T &&`` names, else ``None``."""
    el = ctx.id_map.get(type_id)
    if el is None or el.tag not in ("ReferenceType", "RValueReferenceType"):
        return None
    inner = ctx.id_map.get(el.get("type", ""))
    while inner is not None and inner.tag in ("CvQualifiedType", "ElaboratedType"):
        inner = ctx.id_map.get(inner.get("type", ""))
    return inner.get("id") if inner is not None else None


def _is_copy_or_move(
    ctx: CastxmlParserContext, member: Element, record_id: str
) -> bool:
    args = [a for a in member if a.tag == "Argument"]
    if not args or _referenced_record_id(ctx, args[0].get("type", "")) != record_id:
        return False
    return all(a.get("default") is not None for a in args[1:])


def _defaulted_on_declaration(
    ctx: CastxmlParserContext, member: Element
) -> bool | None:
    """Whether *member*'s declaration text says ``= default`` (``None`` when
    it cannot be read)."""
    file_el = ctx.id_map.get(member.get("file", ""))
    line_raw = member.get("line", "")
    if file_el is None or not line_raw.isdigit():
        return None
    fname = file_el.get("name", "")
    lines = ctx.source_lines_cache.get(fname)
    if lines is None:
        try:
            lines = Path(fname).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return None
        ctx.source_lines_cache[fname] = lines
    start = int(line_raw) - 1
    if not 0 <= start < len(lines):
        return None
    text = "\n".join(lines[start : start + _DECL_LINES])
    name = re.escape(member.get("name", ""))
    if member.tag == "Destructor":
        declarator = rf"~\s*{name}\s*\("
    elif member.tag == "Constructor":
        declarator = rf"(?<![\w~]){name}\s*\("
    else:
        declarator = r"\boperator\s*=\s*\("
    # Every spelling on the line: copy and move constructors may share it.
    verdicts = {
        _defaulted_after(text, m.end() - 1) for m in re.finditer(declarator, text)
    }
    return verdicts.pop() if len(verdicts) == 1 else None


def _defaulted_after(text: str, open_at: int) -> bool | None:
    """Whether the declaration whose parameter list opens at *open_at* ends
    in ``= default`` (``None`` when its end is not in *text*)."""
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                tail = text[i + 1 :]
                stops = [j for j in (tail.find(";"), tail.find("{")) if j != -1]
                if not stops:
                    return None
                return bool(_DEFAULTED_RE.search(tail[: min(stops)]))
    return None


def _record_trait(ctx: CastxmlParserContext, el: Element) -> bool | None:
    if el.get("incomplete") == "1":
        return None
    record_id = el.get("id", "")
    unknown = False
    has_copy_ctor = False
    for base in el:
        if base.tag != "Base":
            continue
        if base.get("virtual") == "1":
            return False
        trait = _type_trait(ctx, base.get("type", ""))
        if trait is False:
            return False
        unknown |= trait is None
    for mid in el.get("members", "").split():
        member = ctx.id_map.get(mid)
        if member is None:
            continue
        tag = member.tag
        if (
            tag in ("Method", "OperatorMethod", "Destructor")
            and member.get("virtual") == "1"
        ):
            return False
        if tag == "Field":
            trait = _type_trait(ctx, member.get("type", ""))
            if trait is False:
                return False
            unknown |= trait is None
            continue
        special = tag == "Destructor" or (
            tag in ("Constructor", "OperatorMethod")
            and (tag == "Constructor" or member.get("name") == "=")
            and _is_copy_or_move(ctx, member, record_id)
        )
        if not special:
            continue
        if tag == "Constructor":
            has_copy_ctor = True
        if member.get("artificial") == "1":
            continue
        defaulted = _defaulted_on_declaration(ctx, member)
        if defaulted is False:
            return False  # user-provided
        unknown = True
    if unknown or not has_copy_ctor:
        return None
    return True
