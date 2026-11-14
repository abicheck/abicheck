# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""Return types narrowed to a derived class at offset 0.

``FUNC_RETURN_CHANGED`` compares the spelled return type. A pointer or
reference return that changes from ``B*`` to ``D*``, where every step of the
derivation from ``D`` to ``B`` is a non-virtual base at offset 0, hands old
callers the very address they expect to treat as a ``B*``: the return type is
not in a non-template function's mangled name, and a virtual overrider with
such a covariant return needs no this-adjusting thunk and gets no new vtable
slot (Itanium C++ ABI 2.5.3). That is not an ABI break, so such a change is
re-kinded ``FUNC_RETURN_COVARIANT_NARROWED`` (compatible with risk: rebuilt
callers see the narrower type).

It runs once per comparison after the detectors, like
``compare.abi_equivalent_retype``. It never raises a verdict, and it re-kinds
only what the new snapshot proves: an unknown base offset, a virtual base, a
cv or pointer-kind difference, or a class it cannot find keeps the break.
"""

from __future__ import annotations

from dataclasses import replace
from functools import lru_cache

from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.entities import RecordType
from ..model.snapshot import AbiSnapshot

__all__ = ["narrowed_to_offset_zero_base", "refine_covariant_returns"]

_CV = frozenset({"const", "volatile"})
_TAGS = frozenset({"struct", "class"})
_SIGILS = frozenset({"*", "&", "&&"})


def _tokens(spelling: str) -> list[str]:
    """*spelling* as words and declarator tokens (``&&`` kept whole)."""
    out: list[str] = []
    for word in spelling.replace("*", " * ").replace("&", " & ").split():
        if word == "&" and out and out[-1] == "&":
            out[-1] = "&&"
        else:
            out.append(word)
    return out


def _is_class_name(token: str) -> bool:
    parts = token.split("::")
    if parts and parts[0] == "":
        parts = parts[1:]
    return bool(parts) and all(p.isidentifier() for p in parts)


@lru_cache(maxsize=4096)
def _split(spelling: str | None) -> tuple[str, str, str, str] | None:
    """``(leading cv, class name, sigil, trailing cv)`` of a spelling that is
    one class name behind exactly one pointer or reference declarator, else
    None (a template argument list, a function type, a second declarator)."""
    if spelling is None:
        return None
    toks = _tokens(spelling)
    sigils = [i for i, t in enumerate(toks) if t in _SIGILS]
    if len(sigils) != 1:
        return None
    at = sigils[0]
    head, tail = toks[:at], toks[at + 1 :]
    words = [t for t in head if t not in _TAGS]
    names = [t for t in words if t not in _CV]
    if len(names) != 1 or not _is_class_name(names[0]) or len(tail) > 1:
        return None
    if tail and tail[0] not in _CV:
        return None
    pre = " ".join(sorted(t for t in words if t in _CV))
    name = "::".join(part for part in names[0].split("::") if part)
    return pre, name, toks[at], " ".join(tail)


def _record(snap: AbiSnapshot, name: str) -> RecordType | None:
    rec = snap.type_by_name(name)
    if rec is None and "::" in name:
        rec = snap.type_by_name(name.rsplit("::", 1)[-1])
    return rec


def _same_class(a: str, b: str) -> bool:
    """Same class, tolerating one side spelled as a bare leaf."""
    if a == b:
        return True
    leaf_equal = a.rsplit("::", 1)[-1] == b.rsplit("::", 1)[-1]
    return leaf_equal and ("::" not in a or "::" not in b)


def narrowed_to_offset_zero_base(
    derived: str, base: str, snap: AbiSnapshot, _depth: int = 0
) -> bool:
    """Whether *base* is reached from *derived* through non-virtual bases each
    at a known offset 0 in *snap*."""
    if _depth > 32:
        return False
    rec = _record(snap, derived)
    if rec is None:
        return False
    virtual = set(rec.resolved_virtual_bases())
    for b in rec.resolved_bases():
        if b in virtual or rec.base_offsets.get(b) != 0:
            continue
        if _same_class(b, base) or narrowed_to_offset_zero_base(
            b, base, snap, _depth + 1
        ):
            return True
    return False


def _covariant(change: Change, new: AbiSnapshot) -> bool:
    old_s = _split(change.old_value)
    new_s = _split(change.new_value)
    if old_s is None or new_s is None:
        return False
    (o_pre, o_name, o_sig, o_post), (n_pre, n_name, n_sig, n_post) = old_s, new_s
    if (o_pre, o_sig, o_post) != (n_pre, n_sig, n_post) or _same_class(o_name, n_name):
        return False
    return narrowed_to_offset_zero_base(n_name, o_name, new)


def refine_covariant_returns(
    changes: list[Change], old: AbiSnapshot, new: AbiSnapshot
) -> list[Change]:
    """*changes* with each provably offset-0 covariant return narrowing
    re-kinded; every other change passes through unchanged and in order."""
    del old  # the derivation is read from the side that declares it
    out: list[Change] = []
    for change in changes:
        if change.kind is ChangeKind.FUNC_RETURN_CHANGED and _covariant(change, new):
            change = replace(
                change,
                kind=ChangeKind.FUNC_RETURN_COVARIANT_NARROWED,
                description=(
                    f"Return type narrowed to a derived class at offset 0: "
                    f"{change.symbol} {change.old_value} -> {change.new_value} "
                    "(same address, same mangled name, no new vtable slot)"
                ),
            )
        out.append(change)
    return out
