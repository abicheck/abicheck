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

The witness is a claim about the *whole* overload set, not the pair: a third
overload can win the call outright. Against two scalar identity conversions
the only way to rank strictly better at a ``{}`` position is a
``std::initializer_list`` parameter ([over.ics.rank]/3.1), so ``f({})`` keeps
calling ``f(std::initializer_list<int>)`` when ``f(double)`` joins ``f(int)``
(2026-10-11 re-audit). Any other member taking an ``initializer_list`` at a
``{}`` position therefore withdraws the witness; every other third candidate
can at best tie, which leaves the call ambiguous.

The call must also have been valid before: the old overload must have been
the unique best candidate. Any other old member that could tie with it --
an identity conversion at every ``{}`` position (a scalar, or a reference to
one: ``S(int)`` next to ``S(const int &)`` was already ambiguous) and the same
type, or a reference to it, at every other position -- or that could win
(``initializer_list``) withdraws the witness too.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from ..detector_registry import registry
from ..diff_cxx_rules import itanium_qualified_name
from ..diff_helpers import make_change
from ..model.change_catalog.kinds import ChangeKind
from ..model.execution_cache import memoized
from ..model.snapshot_reliability import both_header_aware
from ..model.synthetic_key import synthetic_ctor_owner
from ..name_classification import canonicalize_type_name
from .public_surface import reconciled_function_surfaces

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


@memoized(maxsize=16384)
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
    args = _witness_args(old, new, typedefs, enums)
    return None if args is None else f"({', '.join(args)})"


def _witness_args(
    old: Function,
    new: Function,
    typedefs: Mapping[str, str],
    enums: frozenset[str],
) -> list[str] | None:
    """The arguments of :func:`ambiguity_witness`, one per position."""
    if not _comparable_pair(old, new):
        return None
    args = [
        _witness_argument(p_old, p_new, typedefs, enums)
        for p_old, p_new in zip(old.params, new.params, strict=True)
    ]
    if "{}" not in args:
        return None
    return [a for a in args if a is not None] if None not in args else None


def _strip_reference(spelling: str) -> str:
    """*spelling* (already resolved) without a top-level ``&``/``&&``
    (canonical spellings write ``&&`` as ``& &``). A top-level reference
    always ends the spelling; a nested one is followed by ``>`` or ``)``."""
    bare = spelling.rstrip(" &")
    return spelling if bare == spelling else _strip_cv(bare.strip())


def _viable_arity(f: Function, n: int) -> bool:
    if len(f.params) == n:
        return True
    return len(f.params) > n and all(p.default is not None for p in f.params[n:])


def _may_tie_or_win(
    rival: Function,
    best: Function,
    args: list[str],
    typedefs: Mapping[str, str],
    enums: frozenset[str],
) -> bool:
    """Whether *rival* could rank at least as well as *best* for the witness
    call *args* (``{}`` or an lvalue of *best*'s parameter type), so that
    *best* was not the unique best candidate."""
    if rival.is_variadic or not _viable_arity(rival, len(args)):
        return bool(rival.is_variadic)  # unknown ranking: assume it could
    braced = [i for i, a in enumerate(args) if a == "{}"]
    if _wins_braced_call(rival, braced, typedefs):
        return True
    for i, arg in enumerate(args):
        theirs = _strip_reference(_resolve(rival.params[i].type, typedefs))
        if arg == "{}":
            if not is_scalar_type(theirs, typedefs, enums):
                return False  # a user-defined conversion: worse
        elif theirs != _resolve(best.params[i].type, typedefs):
            return False  # a conversion where *best* binds exactly: worse
    return True


def _wins_braced_call(
    other: Function, braced: list[int], typedefs: Mapping[str, str]
) -> bool:
    """Whether *other* may rank better than every scalar candidate for a
    call passing ``{}`` at the *braced* positions: it takes a
    ``std::initializer_list`` at one of them (see the module docstring)."""
    return any(
        i < len(other.params)
        and "initializer_list" in _resolve(other.params[i].type, typedefs)
        for i in braced
    )


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
    if not both_header_aware(old, new):
        return []
    old_map, new_map = reconciled_function_surfaces(old, new)
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
            change = _first_new_ambiguity(
                key, n, kept, previous, members, typedefs, enums
            )
            if change is not None:
                changes.append(change)
    return changes


def _first_new_ambiguity(
    key: str,
    added: Function,
    kept: list[Function],
    previous: list[Function],
    current: list[Function],
    typedefs: Mapping[str, str],
    enums: frozenset[str],
) -> Change | None:
    """The finding for the first kept overload *added* makes ambiguous with a
    call that was not already ambiguous among the old overloads and that no
    other member of the *current* overload set wins outright."""
    for o in kept:
        args = _witness_args(o, added, typedefs, enums)
        if args is None:
            continue
        braced = [i for i, a in enumerate(args) if a == "{}"]
        if any(
            _wins_braced_call(f, braced, typedefs)
            for f in current
            if f is not o and f is not added
        ):
            continue  # a third overload takes the call
        witness = f"({', '.join(args)})"
        if any(
            _may_tie_or_win(o2, o, args, typedefs, enums)
            for o2 in previous
            if o2 is not o
        ):
            continue  # the call did not select *o* alone before
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
