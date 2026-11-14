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

"""A record's Itanium data size (dsize) from the facts CastXML emits.

CastXML writes ``size`` and every member/base offset but no data size, which
is what decides whether a derived class (or a ``[[no_unique_address]]``
member's neighbour) may be placed in a record's tail padding. The Itanium C++
ABI (§2.4) makes it

* ``sizeof`` for a class that is POD for the purpose of layout (§1.1: the
  C++ TC1/C++03 POD definition), and
* otherwise the end of the last non-empty subobject: each non-virtual base at
  its offset plus *its* data size, each data member at its offset plus its
  ``sizeof`` (a bit-field: plus its width; a ``[[no_unique_address]]``
  member: plus its type's data size, nothing when empty), the vptr; rounded
  up to a byte.

POD for layout, as GCC and Clang both decide it (pinned by
``tests/test_castxml_data_size_integration.py`` against both compilers in
several dialects): not POD when the class has a base, a virtual function, a
non-public non-static data member, a default member initializer, a reference
member, a member (or array) of non-POD class type, or a user-provided
constructor, copy-assignment operator or destructor. The compilers disagree
-- so the answer is UNKNOWN, never guessed -- for a constructor defaulted or
deleted on its first declaration (GCC in C++20 and later treats it as
non-POD, Clang does not) and for a ``[[no_unique_address]]`` member (GCC
non-POD, Clang POD). CastXML omits deleted members, so a class without its
implicit default constructor may hide a deleted one: unknown as well.

Virtual bases are placed by the most-derived class at offsets CastXML does
not emit, so a class with one anywhere in its hierarchy has no data size
here. A non-virtual base occupies ``offset + nvsize(base)``, not its data
size: an empty base placed past a base's data (``struct X : B, E {}`` with
``E`` also a base of ``B``) extends ``nvsize`` beyond ``dsize``, and a class
deriving from ``X`` starts its own members after it. The recorded value is
therefore that size -- where a derived class's next member may go, which is
what tail-padding reuse depends on and what the compilers' own placement
measures (``tests/_data_size_matrix.py``); it equals the strict ``dsize``
except in that empty-base case.

All of this is the Itanium C++ ABI, and "POD" itself is the target's: the
generic Itanium rule (C++03 POD, above) or Apple arm64's C++11 POD
(trivial and standard-layout), selected from the target triple castxml
parsed for (``tail_padding_rule``). An MSVC target, a target the table does
not know, an unrecorded triple, or a document whose manglings are
Microsoft's (``?name@@...``) gets no data size. Every unknown input leaves the data size ``None``:
``TAIL_PADDING_REUSE_CHANGED`` only compares two recorded values.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from xml.etree.ElementTree import Element

from .context import CastxmlParserContext, member_elements as _members
from .tail_padding_rule import TailPaddingRule, ms_bitfields, pod11, rule_of

_RECORD_TAGS = frozenset({"Struct", "Class", "Union"})
_NAMED_TAGS = frozenset({"CvQualifiedType", "Typedef", "ElaboratedType"})
_REFERENCE_TAGS = frozenset({"ReferenceType", "RValueReferenceType"})
#: Elements that exist only in a C++ document.
_CXX_ONLY_TAGS = frozenset(
    {
        "Constructor",
        "Destructor",
        "OperatorMethod",
        "Method",
        "Class",
        "ReferenceType",
        "RValueReferenceType",
    }
)
_NUA_TOKEN = "no_unique_address"
_DEFINE_RE = re.compile(r"^\s*#\s*define\s+(\w+)(.*)$")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_DEFAULTED_RE = re.compile(r"=\s*default\b")
#: How many lines a special member's declaration may span.
_DECL_LINES = 4
_MAX_DEPTH = 32


class _FlagFn(Protocol):
    def __call__(
        self, ctx: CastxmlParserContext, el: Element, depth: int = 0
    ) -> bool | None: ...


def data_size_bits(ctx: CastxmlParserContext, el: Element) -> int | None:
    """The Itanium data size of record *el* in bits, or ``None`` when a fact
    it depends on is unknown or the parse did not target the Itanium ABI."""
    if not itanium_abi_target(ctx):
        return None
    cache = ctx.data_size_cache
    eid = el.get("id", "")
    if eid in cache:
        return cache[eid]
    cache[eid] = None  # a by-value cycle is impossible; stay safe anyway
    result = _sizes(ctx, el, 0)[1]
    cache[eid] = result
    return result


def itanium_abi_target(ctx: CastxmlParserContext) -> bool:
    """Whether the parse targeted an Itanium C++ ABI whose tail-padding
    rule is known: the recorded target triple maps to a rule, and the
    document's own manglings agree -- an Itanium one (``_Z...``) and no
    Microsoft one (``?...``). A C document (nothing is mangled) has no C++
    layout rule to disagree on -- every record is POD."""
    if rule_of(ctx) is None:
        return False
    if ctx.itanium_abi_target is None:
        itanium = msvc = False
        for el in ctx.root:
            mangled = el.get("mangled", "")
            if mangled.startswith("?"):
                msvc = True
                break
            itanium = itanium or mangled.startswith("_Z")
        ctx.itanium_abi_target = not msvc and (itanium or not _is_cxx(ctx))
    return ctx.itanium_abi_target


def pod_for_layout(ctx: CastxmlParserContext, el: Element) -> bool | None:
    """Whether record *el* is POD for the purpose of layout (tri-state)."""
    cache = ctx.pod_for_layout_cache
    eid = el.get("id", "")
    if eid in cache:
        return cache[eid]
    cache[eid] = None
    result = _pod(ctx, el)
    cache[eid] = result
    return result


# --- POD for the purpose of layout -------------------------------------------


def _is_cxx(ctx: CastxmlParserContext) -> bool:
    if ctx.is_cxx_document is None:
        ctx.is_cxx_document = any(el.tag in _CXX_ONLY_TAGS for el in ctx.root)
    return ctx.is_cxx_document


def _strip(ctx: CastxmlParserContext, type_id: str) -> Element | None:
    el = ctx.id_map.get(type_id)
    depth = 0
    while el is not None and el.tag in _NAMED_TAGS and depth < _MAX_DEPTH:
        el = ctx.id_map.get(el.get("type", ""))
        depth += 1
    return el


def _element_record(ctx: CastxmlParserContext, type_id: str) -> Element | None:
    """The record a type names, through wrappers and arrays."""
    el = _strip(ctx, type_id)
    depth = 0
    while el is not None and el.tag == "ArrayType" and depth < _MAX_DEPTH:
        el = _strip(ctx, el.get("type", ""))
        depth += 1
    return el if el is not None and el.tag in _RECORD_TAGS else None


def _pod(ctx: CastxmlParserContext, el: Element) -> bool | None:
    if el.get("incomplete") == "1":
        return None
    if not _is_cxx(ctx):
        return True  # every C struct and union is POD
    if any(b.tag == "Base" for b in el):
        return False
    record_id = el.get("id", "")
    members = _members(ctx, el)
    verdicts: list[bool | None] = []
    has_implicit_default_ctor = False
    has_user_ctor = False
    for m in members:
        verdicts.append(_member_pod(ctx, m, record_id))
        if m.tag == "Constructor":
            if m.get("artificial") == "1":
                if not [a for a in m if a.tag == "Argument"]:
                    has_implicit_default_ctor = True
            else:
                has_user_ctor = True
    # A user-declared constructor suppresses the implicit default one, and
    # CastXML omits a deleted constructor: with neither, a deleted one may
    # exist (non-POD to GCC from C++20 on, POD to Clang).
    if not has_implicit_default_ctor and not has_user_ctor:
        verdicts.append(None)
    if False in verdicts:
        return False
    return None if None in verdicts else True


def _member_pod(
    ctx: CastxmlParserContext, member: Element, record_id: str
) -> bool | None:
    """``False`` when *member* makes its class non-POD for layout, ``None``
    when that cannot be decided, ``True`` otherwise."""
    tag = member.tag
    if member.get("virtual") == "1":
        return False
    if tag == "Field":
        return _field_pod(ctx, member)
    if member.get("artificial") == "1":
        return True
    if tag == "Constructor":
        provided = _user_provided(ctx, member)
        # A defaulted constructor: compilers disagree (module docstring).
        return None if provided is not True else False
    if tag == "Destructor" or (
        tag == "OperatorMethod" and _is_copy_assign(ctx, member, record_id)
    ):
        provided = _user_provided(ctx, member)
        return None if provided is None else not provided
    return True


def _field_pod(ctx: CastxmlParserContext, field: Element) -> bool | None:
    if field.get("access") != "public":
        return None if field.get("access") is None else False
    if field.get("init") is not None:
        return False  # a default member initializer
    type_id = field.get("type", "")
    stripped = _strip(ctx, type_id)
    if stripped is None:
        return None
    if stripped.tag in _REFERENCE_TAGS:
        return False
    rec = _element_record(ctx, type_id)
    verdict: bool | None = True
    if rec is not None:
        verdict = pod_for_layout(ctx, rec)
        if verdict is False:
            return False
    if _maybe_no_unique_address(ctx, field):
        return None
    return verdict


def _is_copy_assign(ctx: CastxmlParserContext, member: Element, record_id: str) -> bool:
    if member.get("name") != "=":
        return False
    args = [a for a in member if a.tag == "Argument"]
    if not args or any(a.get("default") is None for a in args[1:]):
        return False
    el = ctx.id_map.get(args[0].get("type", ""))
    if el is not None and el.tag == "ReferenceType":
        el = ctx.id_map.get(el.get("type", ""))
    elif el is not None and el.tag == "RValueReferenceType":
        return False  # a move assignment leaves the class POD (both compilers)
    target = _strip(ctx, el.get("id", "")) if el is not None else None
    return target is not None and target.get("id") == record_id


# --- source text --------------------------------------------------------------


def _file_lines(ctx: CastxmlParserContext, file_id: str) -> list[str] | None:
    file_el = ctx.id_map.get(file_id)
    if file_el is None:
        return None
    fname = file_el.get("name", "")
    lines = ctx.source_lines_cache.get(fname)
    if lines is None:
        try:
            lines = Path(fname).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return None
        ctx.source_lines_cache[fname] = lines
    return lines


def _code(text: str) -> str:
    return _LINE_COMMENT_RE.sub("", _BLOCK_COMMENT_RE.sub(" ", text))


def _user_provided(ctx: CastxmlParserContext, member: Element) -> bool | None:
    """Whether a user-declared special member is user-provided (not defaulted
    on its first declaration); ``None`` when its text cannot say."""
    if member.get("inline") != "1":
        # A function defaulted on its first declaration is implicitly inline.
        return True
    lines = _file_lines(ctx, member.get("file", ""))
    line_raw = member.get("line", "")
    if lines is None or not line_raw.isdigit():
        return None
    start = int(line_raw) - 1
    if not 0 <= start < len(lines):
        return None
    text = _code("\n".join(lines[start : start + _DECL_LINES]))
    name = re.escape(member.get("name", ""))
    if member.tag == "Destructor":
        declarator = rf"~\s*{name}\s*\("
    elif member.tag == "Constructor":
        declarator = rf"(?<![\w~]){name}\s*\("
    else:
        declarator = r"\boperator\s*=\s*\("
    verdicts = {
        _provided_after(text, m.end() - 1) for m in re.finditer(declarator, text)
    }
    return verdicts.pop() if len(verdicts) == 1 else None


def _provided_after(text: str, open_at: int) -> bool | None:
    """For the declaration whose parameter list opens at *open_at*: ``False``
    when it is ``= default``, ``True`` when it has a body or ends without
    one, ``None`` when its end is not in *text*."""
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
                return not _DEFAULTED_RE.search(tail[: min(stops)])
    return None


def _nua_spellings(ctx: CastxmlParserContext) -> frozenset[str] | None:
    """The token and every macro name whose definition (transitively) spells
    ``no_unique_address`` in the document's files; ``None`` when a file
    cannot be read."""
    if ctx.nua_spellings is not None:
        return ctx.nua_spellings
    # Every definition a macro has anywhere: under ``#if``/``#else`` arms only
    # one is live, and the text cannot say which, so a name spells the
    # attribute when any of its definitions does.
    defines: dict[str, list[str]] = {}
    for el in ctx.root:
        if el.tag != "File" or el.get("name", "").startswith("<"):
            continue
        lines = _file_lines(ctx, el.get("id", ""))
        if lines is None:
            return None
        for line in lines:
            m = _DEFINE_RE.match(line)
            if m:
                defines.setdefault(m.group(1), []).append(m.group(2))
    spellings = {_NUA_TOKEN}
    changed = True
    while changed:
        changed = False
        for name, bodies in defines.items():
            if name not in spellings and any(_spells(b, spellings) for b in bodies):
                spellings.add(name)
                changed = True
    ctx.nua_spellings = frozenset(spellings)
    return ctx.nua_spellings


def _maybe_no_unique_address(ctx: CastxmlParserContext, field: Element) -> bool:
    """Whether *field* may carry ``[[no_unique_address]]`` (CastXML does not
    say): its declaration text, from the end of the previous member through
    its own line, spells the attribute or a macro that does."""
    spellings = _nua_spellings(ctx)
    lines = _file_lines(ctx, field.get("file", ""))
    line_raw = field.get("line", "")
    if spellings is None or lines is None or not line_raw.isdigit():
        return True
    end = int(line_raw)
    record = ctx.id_map.get(field.get("context", ""))
    start = end - 1
    if record is not None:
        start = (
            int(record.get("line", "0") or 0)
            if record.get("file") == field.get("file")
            else 0
        )
        for m in _members(ctx, record):
            ml = m.get("line", "")
            if m.get("file") == field.get("file") and ml.isdigit() and int(ml) < end:
                start = max(start, int(ml))
    start = max(0, min(start, end - 1))
    return _spells(_code("\n".join(lines[start:end])), spellings)


def _spells(text: str, spellings: frozenset[str] | set[str]) -> bool:
    """The attribute token anywhere (``__no_unique_address__``,
    ``msvc::no_unique_address``), a macro name as a whole word."""
    return any(
        s in text if s == _NUA_TOKEN else re.search(rf"\b{re.escape(s)}\b", text)
        for s in spellings
    )


# --- data size ----------------------------------------------------------------


def _int(el: Element, attr: str) -> int | None:
    raw = el.get(attr, "")
    return int(raw) if raw.lstrip("-").isdigit() else None


def _type_size_bits(
    ctx: CastxmlParserContext, type_id: str, depth: int = 0
) -> int | None:
    el = _strip(ctx, type_id)
    if el is None or depth > _MAX_DEPTH:
        return None
    if el.tag == "ArrayType":
        lo, hi = _int(el, "min"), _int(el, "max")
        inner = _type_size_bits(ctx, el.get("type", ""), depth + 1)
        if lo is None or hi is None or inner is None:
            return None  # a flexible array member, or an unsized element
        return (hi - lo + 1) * inner
    if el.tag in _RECORD_TAGS and el.get("incomplete") == "1":
        return None
    return _int(el, "size")


def _pointer_bits(ctx: CastxmlParserContext) -> int | None:
    for el in ctx.root:
        if el.tag == "PointerType" and _int(el, "size"):
            return _int(el, "size")
    return None


def _declares_virtual(ctx: CastxmlParserContext, el: Element) -> bool:
    return any(m.get("virtual") == "1" for m in _members(ctx, el))


def _base_records(ctx: CastxmlParserContext, el: Element) -> list[Element] | None:
    out: list[Element] = []
    for b in el:
        if b.tag == "Base":
            rec = _element_record(ctx, b.get("type", ""))
            if rec is None:
                return None
            out.append(rec)
    return out


def _flag_memo(
    query: str,
) -> Callable[[_FlagFn], _FlagFn]:
    """Memoize a hierarchy query per (record, depth) for the parse: a
    non-virtual diamond ladder reaches the same base along exponentially
    many paths."""

    def wrap(fn: _FlagFn) -> _FlagFn:
        def cached(
            ctx: CastxmlParserContext, el: Element, depth: int = 0
        ) -> bool | None:
            key = (query, el.get("id", ""), depth)
            cache = ctx.layout_flag_cache
            if key not in cache:
                cache[key] = fn(ctx, el, depth)
            return cache[key]

        return cached

    return wrap


@_flag_memo("dynamic")
def _dynamic(ctx: CastxmlParserContext, el: Element, depth: int = 0) -> bool | None:
    if depth > _MAX_DEPTH or el.get("incomplete") == "1":
        return None
    if _declares_virtual(ctx, el) or any(
        b.tag == "Base" and b.get("virtual") == "1" for b in el
    ):
        return True
    bases = _base_records(ctx, el)
    if bases is None:
        return None
    flags = [_dynamic(ctx, b, depth + 1) for b in bases]
    if True in flags:
        return True
    return None if None in flags else False


@_flag_memo("virtual_base")
def _has_virtual_base(
    ctx: CastxmlParserContext, el: Element, depth: int = 0
) -> bool | None:
    if depth > _MAX_DEPTH or el.get("incomplete") == "1":
        return None
    if any(b.tag == "Base" and b.get("virtual") == "1" for b in el):
        return True
    bases = _base_records(ctx, el)
    if bases is None:
        return None
    flags = [_has_virtual_base(ctx, b, depth + 1) for b in bases]
    if True in flags:
        return True
    return None if None in flags else False


@_flag_memo("empty")
def _is_empty(ctx: CastxmlParserContext, el: Element, depth: int = 0) -> bool | None:
    """Itanium "empty class": no non-static data member, no virtual function
    or virtual base, every base empty."""
    if depth > _MAX_DEPTH or el.get("incomplete") == "1":
        return None
    if el.tag == "Union":
        return not any(m.tag == "Field" for m in _members(ctx, el))
    if any(m.tag == "Field" for m in _members(ctx, el)):
        return False
    if _declares_virtual(ctx, el) or any(
        b.tag == "Base" and b.get("virtual") == "1" for b in el
    ):
        return False
    bases = _base_records(ctx, el)
    if bases is None:
        return None
    flags = [_is_empty(ctx, b, depth + 1) for b in bases]
    if False in flags:
        return False
    return None if None in flags else True


_UNKNOWN: tuple[int | None, int | None] = (None, None)


def _sizes(
    ctx: CastxmlParserContext, el: Element, depth: int
) -> tuple[int | None, int | None]:
    """``(dsize, nvsize)`` of *el* in bits; each ``None`` when unknown."""
    if depth > _MAX_DEPTH or el.get("incomplete") == "1":
        return _UNKNOWN
    size = _int(el, "size")
    pod = (
        pod11(ctx, el, _user_provided)
        if rule_of(ctx) is TailPaddingRule.POD11 and _is_cxx(ctx)
        else pod_for_layout(ctx, el)
    )
    if size is None or pod is None:
        return _UNKNOWN
    if pod:
        # An empty POD (C++11 POD admits an empty base) still sits at offset
        # 0 under the empty-base optimization, so nothing follows its byte.
        if el.tag != "Union" and _is_empty(ctx, el) is True:
            return 0, 0
        return size, size
    if el.tag == "Union" or _has_virtual_base(ctx, el) is not False:
        return _UNKNOWN
    empty = _is_empty(ctx, el)
    if empty is None:
        return _UNKNOWN
    if empty:
        return 0, 0
    # (lowest, highest) possible end of each subobject; the data size is
    # known only when the largest lower bound reaches every upper bound.
    ends: list[tuple[int, int]] = []
    # Where an empty base sits: inside nvsize, outside dsize.
    empty_ends: list[int] = []
    if _declares_virtual(ctx, el):
        base_recs = _base_records(ctx, el)
        dyn_bases = [_dynamic(ctx, b) for b in base_recs or []]
        if base_recs is None or None in dyn_bases:
            return _UNKNOWN
        if True not in dyn_bases:
            ptr = _pointer_bits(ctx)
            if ptr is None:
                return _UNKNOWN
            ends.append((ptr, ptr))  # the class's own vptr at offset 0
    for b in el:
        if b.tag != "Base":
            continue
        rec = _element_record(ctx, b.get("type", ""))
        off = _int(b, "offset")
        if rec is None or off is None:
            return _UNKNOWN
        base_empty = _is_empty(ctx, rec)
        if base_empty is None:
            return _UNKNOWN
        if base_empty:
            empty_ends.append(off * 8 + 8)
            continue
        sub = data_size_bits(ctx, rec)  # the base's nvsize
        if sub is None:
            return _UNKNOWN
        if sub:
            ends.append((off * 8 + sub, off * 8 + sub))
    for f in _members(ctx, el):
        if f.tag != "Field":
            continue
        end = _field_end(ctx, f)
        if end is None:
            return _UNKNOWN
        ends.append(end)
    lowest = max((lo for lo, _ in ends), default=0)
    if lowest < max((hi for _, hi in ends), default=0):
        return _UNKNOWN
    dsize = (lowest + 7) // 8 * 8
    return dsize, max(dsize, *empty_ends) if empty_ends else dsize


def _field_end(ctx: CastxmlParserContext, f: Element) -> tuple[int, int] | None:
    off = _int(f, "offset")
    if off is None:
        return None
    bits = _int(f, "bits")
    if bits is not None:
        if ms_bitfields(ctx):
            return None  # MinGW lays bit-fields out by Microsoft's rules
        return (off + bits, off + bits)
    type_id = f.get("type", "")
    size = _type_size_bits(ctx, type_id)
    if size is None:
        return None
    full = off + size
    stripped = _strip(ctx, type_id)
    if (
        stripped is None
        or stripped.tag not in _RECORD_TAGS
        or not _maybe_no_unique_address(ctx, f)
    ):
        return (full, full)
    # Possibly [[no_unique_address]]: the member then ends at its type's data
    # size (nothing when empty) instead of its sizeof.
    empty = _is_empty(ctx, stripped)
    sub = 0 if empty else data_size_bits(ctx, stripped) if empty is False else None
    if sub is None:
        return (0, full)
    overlapped = off + sub if sub else 0
    return (min(overlapped, full), max(overlapped, full))
