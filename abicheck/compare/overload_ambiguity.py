"""A new overload that makes an existing call form ambiguous.

Adding an overload is binary-compatible, but it can break recompilation of a
call that had exactly one viable candidate before. The common, provable shape
is an argument with no type of its own -- an empty braced list ``{}`` -- passed
where the old and the new overload both take a *scalar* (arithmetic, enum,
pointer, pointer-to-member): ``{}`` converts to every scalar by the identity
conversion ([over.ics.list]), so neither candidate is better and the call is
ambiguous. ``mylib::ets x({});`` compiles against
``ets(int)`` and is ambiguous once ``ets(int (*)())`` joins it
(``case111_enumerable_thread_specific_lambda_ambiguity``).

The witness is built per (preexisting overload, new overload) pair of the same
callable -- constructors included, which ``overload_added`` deliberately
skips: same arity, same implicit-object qualifiers, identical types at every
other position, and at least one position where both take a scalar. It is a
consumer-conditional break (only callers passing ``{}`` there are affected),
so it is reported as a risk naming the witness call, never as an unconditional
API break. Templates, variadics and reference/class parameters are left alone:
their ranking rules are not decided by this witness.
"""

from __future__ import annotations

import functools
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from ..detector_registry import registry
from ..diff_cxx_rules import itanium_qualified_name
from ..diff_helpers import make_change
from ..diff_symbols import _both_header_aware, _reconciled_function_surfaces
from ..model.change_catalog.kinds import ChangeKind
from ..model.synthetic_key import synthetic_ctor_owner
from ..name_classification import canonicalize_type_name

if TYPE_CHECKING:
    from ..model import AbiSnapshot, Function, Param
    from ..model.change import Change

__all__ = ["callable_key", "is_scalar_type", "ambiguity_witness"]

_ARITHMETIC = frozenset(
    {
        "bool", "_Bool", "char", "signed char", "unsigned char", "wchar_t",
        "char8_t", "char16_t", "char32_t", "short", "short int",
        "unsigned short", "unsigned short int", "int", "signed", "signed int",
        "unsigned", "unsigned int", "long", "long int", "unsigned long",
        "unsigned long int", "long long", "long long int",
        "unsigned long long", "unsigned long long int", "float", "double",
        "long double", "__int128", "unsigned __int128", "std::nullptr_t",
        "size_t", "ssize_t", "ptrdiff_t", "intptr_t", "uintptr_t",
        "int8_t", "int16_t", "int32_t", "int64_t",
        "uint8_t", "uint16_t", "uint32_t", "uint64_t",
    }
)  # fmt: skip
_CV = frozenset({"const", "volatile"})
_CTOR_LEAF = "{ctor}"
_MAX_TYPEDEF_HOPS = 8


def callable_key(f: Function) -> str | None:
    """Scope-qualified identity of *f*'s overload set (``ns::C::{ctor}`` for a
    constructor, synthetic header-only keys included); ``None`` for a C name."""
    return _callable_key(f.mangled)


@functools.lru_cache(maxsize=16384)
def _callable_key(mangled: str) -> str | None:
    owner = synthetic_ctor_owner(mangled)
    if owner is not None:
        return f"{owner}::{_CTOR_LEAF}"
    return itanium_qualified_name(mangled)


def _display(key: str) -> str:
    """*key* as a caller spells the callable (``ns::C`` for a constructor)."""
    scope, _, leaf = key.rpartition("::")
    return scope if leaf == _CTOR_LEAF else key


def _top_level(spelling: str) -> str:
    """*spelling* with template arguments and parenthesised groups blanked,
    so only top-level declarator tokens remain."""
    out: list[str] = []
    depth = 0
    for ch in spelling:
        if ch in "<(":
            depth += 1
        elif ch in ">)":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
            continue
        out.append(" ")
    return "".join(out)


def _strip_cv(spelling: str) -> str:
    """*spelling* without leading/trailing ``const``/``volatile`` tokens."""
    tokens = spelling.split()
    while tokens and tokens[0] in _CV:
        tokens.pop(0)
    while tokens and tokens[-1] in _CV:
        tokens.pop()
    return " ".join(tokens)


def _resolve(spelling: str, typedefs: Mapping[str, str]) -> str:
    current = canonicalize_type_name(spelling).strip()
    for _ in range(_MAX_TYPEDEF_HOPS):
        bare = _strip_cv(current)
        target = typedefs.get(bare) or typedefs.get(bare.rsplit("::", 1)[-1])
        if not target or target == bare:
            return bare
        current = canonicalize_type_name(target).strip()
    return _strip_cv(current)


def is_scalar_type(
    spelling: str, typedefs: Mapping[str, str], enums: frozenset[str]
) -> bool:
    """Whether a parameter of type *spelling* is a scalar ``{}`` converts to by
    identity: arithmetic, enum, object/function pointer, pointer-to-member.
    References and class types are not (their conversions rank differently)."""
    resolved = _resolve(spelling, typedefs)
    top = _top_level(resolved)
    if "&" in top:
        return False
    if "*" in top or "(*)" in resolved.replace(" ", "") or "::*" in resolved:
        return True
    if resolved in _ARITHMETIC:
        return True
    return resolved in enums or resolved.rsplit("::", 1)[-1] in enums


def _same_object_shape(a: Function, b: Function) -> bool:
    return (
        a.is_static == b.is_static
        and a.is_const == b.is_const
        and a.is_volatile == b.is_volatile
        and (a.ref_qualifier or "") == (b.ref_qualifier or "")
    )


def _comparable_pair(old: Function, new: Function) -> bool:
    """Whether the witness rule applies to this pair at all."""
    if old.is_variadic or new.is_variadic or old.is_deleted or new.is_deleted:
        return False
    if "<" in (old.name or "") or "<" in (new.name or ""):
        return False  # templates rank by deduction, not by this witness
    return len(old.params) == len(new.params) and _same_object_shape(old, new)


def _witness_argument(
    p_old: Param, p_new: Param, typedefs: Mapping[str, str], enums: frozenset[str]
) -> str | None:
    """The argument for one position: the parameter's own name when both
    sides take the same type, ``{}`` when both take a scalar, else ``None``."""
    if _resolve(p_old.type, typedefs) == _resolve(p_new.type, typedefs):
        return p_old.name or "_"
    both_scalar = is_scalar_type(p_old.type, typedefs, enums) and is_scalar_type(
        p_new.type, typedefs, enums
    )
    return "{}" if both_scalar else None


def ambiguity_witness(
    old: Function,
    new: Function,
    typedefs: Mapping[str, str],
    enums: frozenset[str],
) -> str | None:
    """The argument list (``"(x, {})"``) whose call resolves to *old* alone
    but is ambiguous between *old* and *new*; ``None`` when there is none."""
    if not _comparable_pair(old, new):
        return None
    args = [
        _witness_argument(p_old, p_new, typedefs, enums)
        for p_old, p_new in zip(old.params, new.params, strict=True)
    ]
    if None in args or "{}" not in args:
        return None
    return f"({', '.join(a for a in args if a is not None)})"


def _signature(f: Function) -> str:
    return f"({', '.join(p.type for p in f.params)})"


def _groups(functions: Iterable[Function]) -> dict[str, list[Function]]:
    out: dict[str, list[Function]] = {}
    for f in functions:
        key = callable_key(f)
        if key is not None:
            out.setdefault(key, []).append(f)
    return out


@registry.detector("overload_ambiguity")
def _diff_overload_ambiguity(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """``OVERLOAD_AMBIGUITY_INTRODUCED`` -- see the module docstring.

    Header-tier only: the witness needs each parameter's declared type, which
    a symbol table or a demangled name does not give reliably.
    """
    if not _both_header_aware(old, new):
        return []
    old_map, new_map = _reconciled_function_surfaces(old, new)
    typedefs = {**old.declarations.typedefs, **new.declarations.typedefs}
    enums = frozenset(
        e.name for e in (*old.declarations.enums, *new.declarations.enums)
    )
    old_groups = _groups(old_map.values())
    changes: list[Change] = []
    for key, members in _groups(new_map.values()).items():
        previous = old_groups.get(key, [])
        kept = [f for f in previous if f.mangled in new_map]
        for n in (f for f in members if f.mangled not in old_map):
            change = _first_new_ambiguity(key, n, kept, previous, typedefs, enums)
            if change is not None:
                changes.append(change)
    return changes


def _first_new_ambiguity(
    key: str,
    added: Function,
    kept: list[Function],
    previous: list[Function],
    typedefs: Mapping[str, str],
    enums: frozenset[str],
) -> Change | None:
    """The finding for the first kept overload *added* makes ambiguous with a
    call that was not already ambiguous among the old overloads."""
    for o in kept:
        witness = ambiguity_witness(o, added, typedefs, enums)
        if witness is None:
            continue
        if any(
            ambiguity_witness(o, o2, typedefs, enums) == witness
            for o2 in previous
            if o2 is not o
        ):
            continue  # already ambiguous before
        return make_change(
            ChangeKind.OVERLOAD_AMBIGUITY_INTRODUCED,
            symbol=added.mangled,
            name=key,
            detail=f"{_display(key)}{witness}",
            old=_signature(o),
            new=_signature(added),
            entity_id=added.entity_id,
        )
    return None
