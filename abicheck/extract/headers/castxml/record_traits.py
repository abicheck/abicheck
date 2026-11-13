"""The trivially-copyable and standard-layout traits of a CastXML record,
where CastXML proves them.

CastXML has no trait attribute, but it emits every special member a record
has: an implicitly-declared one with ``artificial="1"``, a user-declared one
without it. It does not say whether a user-declared member was
``= default``-ed on its first declaration (trivial exactly when its
subobjects are) or user-provided (never trivial), so that one case is read
from the declaration's own text; an unreadable declaration stays unknown.

The answers are tri-state, never a guess:

* ``trivially_copyable`` ``True`` -- no virtual function or virtual base,
  every copy/move constructor, copy/move assignment and the destructor is
  implicit or ``= default``-ed on its first declaration, at least one copy
  constructor exists (CastXML omits a deleted one), and every base and
  non-static data member is itself proven trivially copyable;
* ``trivially_copyable`` ``False`` -- a virtual function or base, a
  user-provided special member, or a base/member proven not trivially
  copyable;
* ``standard_layout`` ``True`` -- C++ [class.prop]/3: no virtual function or
  virtual base, no reference member, one access control for every
  non-static data member, the data-member placement rules across the
  hierarchy, and every base and member proven standard-layout;
* ``standard_layout`` ``False`` -- any of those rules is violated, or a
  base/member is proven not standard-layout;
* ``None`` -- anything else (an incomplete type, an unreadable declaration,
  a type CastXML does not resolve, a member whose access is not emitted).

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
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
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
    ctx: CastxmlParserContext, member: Element, record_name: str
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
    # Comments carry no declaration tokens: `S(const S&) /* = default */;` is
    # user-provided. Blank them out before reading the declarator.
    text = _BLOCK_COMMENT_RE.sub(" ", text)
    text = _LINE_COMMENT_RE.sub("", text)
    name = re.escape(member.get("name", ""))
    if member.tag == "Destructor":
        declarator = rf"~\s*{name}\s*\("
    elif member.tag == "Constructor":
        declarator = rf"(?<![\w~]){name}\s*\("
    else:
        declarator = r"\boperator\s*=\s*\("
    # Every spelling on the line is a candidate. A constructor or assignment
    # is only the member when its parameter list names the record (a copy or
    # move): ``LTC() = default; LTC(const LTC &);`` on one line must not let
    # the defaulted default constructor make the copy constructor ambiguous.
    takes_argument = member.tag != "Destructor"
    verdicts: set[bool | None] = set()
    for m in re.finditer(declarator, text):
        open_at = m.end() - 1
        if takes_argument and not _copy_or_move_parameters(text, open_at, record_name):
            continue
        verdicts.add(_defaulted_after(text, open_at))
    return verdicts.pop() if len(verdicts) == 1 else None


def _copy_or_move_parameters(text: str, open_at: int, record_name: str) -> bool:
    """Whether the parenthesised list opening at *open_at* is a single
    parameter naming *record_name* (a copy or move, not a default or other
    constructor)."""
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                params = text[open_at + 1 : i]
                return bool(re.search(rf"\b{re.escape(record_name)}\b", params))
    return False


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


def _is_special(ctx: CastxmlParserContext, member: Element, record_id: str) -> bool:
    """A destructor, or a copy/move constructor or assignment of *record_id*."""
    tag = member.tag
    if tag == "Destructor":
        return True
    if tag == "OperatorMethod" and member.get("name") != "=":
        return False
    return tag in ("Constructor", "OperatorMethod") and _is_copy_or_move(
        ctx, member, record_id
    )


def _member_trait(
    ctx: CastxmlParserContext, member: Element, record_id: str
) -> bool | None:
    """What one member says about the record's trait: ``False`` disproves it,
    ``None`` leaves it unknown, ``True`` is consistent with it."""
    tag = member.tag
    if (
        tag in ("Method", "OperatorMethod", "Destructor")
        and member.get("virtual") == "1"
    ):
        return False
    if tag == "Field":
        return _type_trait(ctx, member.get("type", ""))
    if not _is_special(ctx, member, record_id) or member.get("artificial") == "1":
        return True
    # User-declared: ``= default`` on its first declaration is trivial exactly
    # when the subobjects are (the base/member checks decide that); a
    # user-provided one is never trivial; an unreadable one stays unknown.
    record = ctx.id_map.get(record_id)
    return _defaulted_on_declaration(
        ctx, member, record.get("name", "") if record is not None else ""
    )


def _base_trait(ctx: CastxmlParserContext, base: Element) -> bool | None:
    """A virtual base disproves the trait; otherwise the base's own trait."""
    if base.get("virtual") == "1":
        return False
    return _type_trait(ctx, base.get("type", ""))


def _members(ctx: CastxmlParserContext, el: Element) -> list[Element]:
    found = (ctx.id_map.get(mid) for mid in el.get("members", "").split())
    return [m for m in found if m is not None]


def _has_copy_constructor(
    ctx: CastxmlParserContext, members: list[Element], record_id: str
) -> bool:
    """CastXML omits a deleted copy constructor, so the trait is only proven
    when one is emitted."""
    return any(
        m.tag == "Constructor" and _is_special(ctx, m, record_id) for m in members
    )


def _record_trait(ctx: CastxmlParserContext, el: Element) -> bool | None:
    if el.get("incomplete") == "1":
        return None
    record_id = el.get("id", "")
    members = _members(ctx, el)
    traits = [_base_trait(ctx, b) for b in el if b.tag == "Base"]
    traits += [_member_trait(ctx, m, record_id) for m in members]
    if False in traits:
        return False
    if None in traits or not _has_copy_constructor(ctx, members, record_id):
        return None
    return True


#: Wrappers whose layout is that of the type they name, without an array.
_NAMED_TAGS = frozenset({"CvQualifiedType", "Typedef", "ElaboratedType"})
_REFERENCE_TAGS = frozenset({"ReferenceType", "RValueReferenceType"})


def standard_layout(ctx: CastxmlParserContext, el: Element) -> bool | None:
    """The standard-layout trait of record *el*, tri-state like
    :func:`trivially_copyable` (C++ [class.prop]/3, C++11 and later)."""
    cache = ctx.standard_layout_cache
    eid = el.get("id", "")
    if eid in cache:
        return cache[eid]
    cache[eid] = None  # a by-value cycle is impossible; stay safe anyway
    result = _standard_layout_trait(ctx, el)
    cache[eid] = result
    return result


def _record_of(
    ctx: CastxmlParserContext, type_id: str, depth: int = 0
) -> Element | None:
    """The record a type names through cv/typedef/elaborated wrappers
    (arrays are not stripped: an array is not the record it holds)."""
    el = ctx.id_map.get(type_id)
    if el is None or depth > 32:
        return None
    if el.tag in _RECORD_TAGS:
        return el
    if el.tag in _NAMED_TAGS:
        return _record_of(ctx, el.get("type", ""), depth + 1)
    return None


def _is_reference(ctx: CastxmlParserContext, type_id: str, depth: int = 0) -> bool:
    el = ctx.id_map.get(type_id)
    if el is None or depth > 32:
        return False
    if el.tag in _REFERENCE_TAGS:
        return True
    return el.tag in _NAMED_TAGS and _is_reference(ctx, el.get("type", ""), depth + 1)


def _standard_layout_type(
    ctx: CastxmlParserContext, type_id: str, depth: int = 0
) -> bool | None:
    el = ctx.id_map.get(type_id)
    if el is None or depth > 32:
        return None
    if el.tag in _SCALAR_TAGS:
        return True
    if el.tag in _TRANSPARENT_TAGS:
        return _standard_layout_type(ctx, el.get("type", ""), depth + 1)
    if el.tag in _RECORD_TAGS:
        return standard_layout(ctx, el)
    return None


def _has_data_members(
    ctx: CastxmlParserContext, el: Element, depth: int = 0
) -> bool | None:
    """Whether record *el* or any base has a non-static data member."""
    if el.get("incomplete") == "1" or depth > 32:
        return None
    if any(m.tag == "Field" for m in _members(ctx, el)):
        return True
    flags: list[bool | None] = []
    for b in el:
        if b.tag != "Base":
            continue
        base = _record_of(ctx, b.get("type", ""))
        flags.append(None if base is None else _has_data_members(ctx, base, depth + 1))
    if True in flags:
        return True
    return None if None in flags else False


def _base_subobjects(
    ctx: CastxmlParserContext, el: Element, depth: int = 0
) -> list[str] | None:
    """Ids of every base subobject of *el* along every inheritance path, one
    entry per occurrence (``D : L, R`` with ``L : E`` and ``R : E`` lists
    ``E`` twice); ``None`` when a base cannot be resolved."""
    ids: list[str] = []
    for b in el:
        if b.tag != "Base":
            continue
        base = _record_of(ctx, b.get("type", ""))
        if base is None or depth > 32:
            return None
        ids.append(base.get("id", ""))
        inner = _base_subobjects(ctx, base, depth + 1)
        if inner is None:
            return None
        ids += inner
    return ids


def _first_member_types(
    ctx: CastxmlParserContext, type_id: str, depth: int = 0
) -> set[str] | None:
    """Record ids in C++ [class.prop]/3's set M(X) for the type *type_id*,
    including the type itself when it is a record: an array contributes its
    element type, a union every member type, a non-union class its first
    non-static data member's type, each recursively. ``None`` when a type in
    the chain cannot be resolved."""
    el = ctx.id_map.get(type_id)
    if el is None or depth > 32:
        return None
    if el.tag in _NAMED_TAGS or el.tag == "ArrayType":
        return _first_member_types(ctx, el.get("type", ""), depth + 1)
    if el.tag not in _RECORD_TAGS:
        return set()
    if el.get("incomplete") == "1":
        return None
    out = {el.get("id", "")}
    fields = [m for m in _members(ctx, el) if m.tag == "Field"]
    for f in fields if el.tag == "Union" else fields[:1]:
        inner = _first_member_types(ctx, f.get("type", ""), depth + 1)
        if inner is None:
            return None
        out |= inner
    return out


def _data_member_rules(
    ctx: CastxmlParserContext,
    el: Element,
    fields: list[Element],
    bases: list[Element],
) -> bool | None:
    """[class.prop]/3.6 (data members in at most one class of the hierarchy,
    and none in the most derived one when a base has some), /3.7 (one base
    subobject per type) and /3.8 (no base in the first-member set M(D))."""
    flags = [
        None
        if (base := _record_of(ctx, b.get("type", ""))) is None
        else _has_data_members(ctx, base)
        for b in bases
    ]
    with_data = flags.count(True)
    unknown = flags.count(None)
    if fields:
        if with_data:
            return False
        hierarchy: bool | None = None if unknown else True
    else:
        if with_data >= 2:
            return False
        hierarchy = None if with_data + unknown >= 2 else True
    if not bases:
        return hierarchy
    subobjects = _base_subobjects(ctx, el)
    if subobjects is None:
        return None
    # [class.prop]/3.7: at most one base class subobject of any given type.
    if len(subobjects) != len(set(subobjects)):
        return False
    if not fields:
        # An inherited first data member (``struct D : E, B {}`` with
        # ``B { E e; }``) is not checked against M(D): GCC and Clang both call
        # such a D standard-layout, so the literal N4659 wording would report a
        # STANDARD_LAYOUT_LOST no compiler sees (LLVM #47741). Pinned against
        # the compiler by ``LayoutInheritedFirst`` in
        # tests/test_castxml_record_traits_layout_integration.py.
        return hierarchy
    # [class.prop]/3.8: no base class in M(D), the first-member type set.
    first_types = _first_member_types(ctx, fields[0].get("type", ""))
    if first_types is None:
        return None
    if first_types & set(subobjects):
        return False
    return hierarchy


def _standard_layout_trait(ctx: CastxmlParserContext, el: Element) -> bool | None:
    if el.get("incomplete") == "1":
        return None
    members = _members(ctx, el)
    fields = [m for m in members if m.tag == "Field"]
    bases = [b for b in el if b.tag == "Base"]
    # [class.prop]/3.2: no virtual function (declared here) and no virtual base.
    if any(
        m.tag in ("Method", "OperatorMethod", "Destructor") and m.get("virtual") == "1"
        for m in members
    ) or any(b.get("virtual") == "1" for b in bases):
        return False
    traits: list[bool | None] = [
        _standard_layout_type(ctx, b.get("type", "")) for b in bases
    ]
    for f in fields:
        # [class.prop]/3.1: no reference data member.
        if _is_reference(ctx, f.get("type", "")):
            return False
        traits.append(_standard_layout_type(ctx, f.get("type", "")))
    # [class.prop]/3.3: one access control for every non-static data member.
    accesses = {f.get("access") for f in fields}
    if None in accesses:
        traits.append(None)
    elif len(accesses) > 1:
        return False
    traits.append(_data_member_rules(ctx, el, fields, bases))
    if False in traits:
        return False
    if None in traits:
        return None
    return True
