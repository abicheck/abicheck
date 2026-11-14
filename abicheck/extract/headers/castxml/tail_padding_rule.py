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

"""Which tail-padding rule a target's C++ ABI follows, and the C++11 POD
test one of them needs.

A derived class may place members in a base's tail padding unless the base
is POD -- and which "POD" depends on the target (clang's
``TargetCXXABI::getTailPaddingUseRules``):

* ``POD03`` -- the generic Itanium ABI, locked to the C++03/TR1 POD
  definition: Linux and the BSDs, MinGW, Android, x86 and 32-bit ARM Darwin,
  AIX.
* ``POD11`` -- the C++11 POD definition (trivial and standard-layout):
  Apple arm64 (macOS, iOS), watchOS, Fuchsia, WebAssembly. A class whose
  data members are all private is POD here, not under ``POD03``.
* Microsoft's ABI always reuses tail padding and lays records out by its own
  rules; no Itanium data size applies, so it maps to ``None``, as does a
  target this table does not know.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from xml.etree.ElementTree import Element

from .context import CastxmlParserContext

_MAX_DEPTH = 32


class TailPaddingRule(StrEnum):
    POD03 = "pod03"
    POD11 = "pod11"


#: Operating-system components of a triple whose ABI is generic Itanium.
_POD03_SYSTEMS = (
    "linux",
    "freebsd",
    "netbsd",
    "openbsd",
    "dragonfly",
    "mingw32",
    "windows-gnu",
    "cygwin",
    "android",
    "solaris",
    "aix",
    "darwin",
    "macos",
    "ios",
    "hurd",
)


def tail_padding_rule(triple: str | None) -> TailPaddingRule | None:
    """The rule of target *triple* (``-dumpmachine`` spelling), or ``None``
    for an MSVC target or one not modeled here."""
    if not triple:
        return None
    t = triple.lower()
    if "msvc" in t:
        return None
    arch = t.split("-", 1)[0]
    if arch.startswith("wasm") or "fuchsia" in t or "watchos" in t:
        return TailPaddingRule.POD11
    apple = "apple" in t or "darwin" in t or "macos" in t or "ios" in t
    if apple and arch in ("arm64", "arm64e", "aarch64", "arm64_32"):
        return TailPaddingRule.POD11
    parts = t.split("-")[1:]
    rest = "-".join(parts)
    if any(sys_ in rest for sys_ in _POD03_SYSTEMS):
        return TailPaddingRule.POD03
    return None


def rule_of(ctx: CastxmlParserContext) -> TailPaddingRule | None:
    return tail_padding_rule(ctx.target_triple)


# --- C++11 POD: trivial and standard-layout ------------------------------------


#: Whether a user-declared special member is user-provided (``None``:
#: undecidable) -- ``data_size._user_provided``, passed in by the caller.
UserProvided = Callable[[CastxmlParserContext, Element], "bool | None"]


def pod11(
    ctx: CastxmlParserContext, el: Element, user_provided: UserProvided
) -> bool | None:
    """C++11 POD for record *el*: trivially copyable, standard-layout, and a
    trivial default constructor (tri-state; ``None`` when undecidable)."""
    from .record_traits import standard_layout, trivially_copyable

    if el.get("incomplete") == "1":
        return None
    return _all(
        [
            trivially_copyable(ctx, el),
            standard_layout(ctx, el),
            _trivial_default_ctor(ctx, el, 0, user_provided),
        ]
    )


def _all(verdicts: list[bool | None]) -> bool | None:
    if False in verdicts:
        return False
    return None if None in verdicts else True


def _members(ctx: CastxmlParserContext, el: Element) -> list[Element]:
    found = (ctx.id_map.get(mid) for mid in el.get("members", "").split())
    return [m for m in found if m is not None]


def _record(ctx: CastxmlParserContext, type_id: str) -> Element | None:
    el = ctx.id_map.get(type_id)
    depth = 0
    while (
        el is not None
        and depth < _MAX_DEPTH
        and el.tag
        in (
            "CvQualifiedType",
            "Typedef",
            "ElaboratedType",
            "ArrayType",
        )
    ):
        el = ctx.id_map.get(el.get("type", ""))
        depth += 1
    return el


def _is_default_ctor(ctor: Element) -> bool:
    args = [a for a in ctor if a.tag == "Argument"]
    return all(a.get("default") is not None for a in args)


def _trivial_default_ctor(
    ctx: CastxmlParserContext, el: Element, depth: int, user_provided: UserProvided
) -> bool | None:
    """Whether *el* has a trivial default constructor ([class.default.ctor])."""
    if depth > _MAX_DEPTH or el.get("incomplete") == "1":
        return None
    if el.tag not in ("Struct", "Class", "Union"):
        return True
    members = _members(ctx, el)
    ctors = [m for m in members if m.tag == "Constructor"]
    if not ctors:
        return None  # CastXML omits a deleted one: it may exist
    defaults = [c for c in ctors if _is_default_ctor(c)]
    if not defaults:
        return False  # a user-declared constructor and no default one
    verdicts: list[bool | None] = []
    for c in defaults:
        if c.get("artificial") == "1":
            continue
        provided = user_provided(ctx, c)
        verdicts.append(None if provided is None else not provided)
    for b in el:
        if b.tag == "Base":
            if b.get("virtual") == "1":
                return False
            rec = _record(ctx, b.get("type", ""))
            verdicts.append(
                None
                if rec is None
                else _trivial_default_ctor(ctx, rec, depth + 1, user_provided)
            )
    for f in members:
        if f.tag != "Field":
            continue
        if f.get("init") is not None:
            return False  # a default member initializer
        rec = _record(ctx, f.get("type", ""))
        if rec is None:
            verdicts.append(None)
        elif rec.tag in ("ReferenceType", "RValueReferenceType"):
            verdicts.append(None)  # its default constructor is deleted
        else:
            verdicts.append(_trivial_default_ctor(ctx, rec, depth + 1, user_provided))
    return _all(verdicts)
