"""A replaced global allocator stays reported, and its export loss is not a break.

Two defects, one finding:

- ``allocator_replacement_added``/``_removed`` name one allocator export in
  ``symbol``. ``elf_symbol_filter`` treats ``operator delete`` (``_ZdlPv``) as
  the C++ runtime's, so with a public-header surface the scoping pass read
  the finding as ``not-exported`` and dropped the only report of the
  process-wide interposition.
- ``_STDLIB_PREFIXES`` spelled ``operator new`` as ``_ZnwSt``, which no
  ``operator new`` mangles as, so a dropped ``_Znwm`` export read as the
  library's own API removed (``func_removed_elf_only``, BREAKING) although
  every consumer resolves ``operator new`` from the C++ runtime.

The oracle below is the Itanium mangling of every replaceable global
allocation function ([new.delete.single], [new.delete.array]: plain, nothrow,
aligned, sized), written out by hand -- not the prefixes the filter matches.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolType
from abicheck.elf_symbol_filter import is_abi_relevant_elf_symbol
from abicheck.model import AbiSnapshot, Function, ScopeOrigin, Visibility

_SIZE = {"lp64": "m", "ilp32": "j"}  # std::size_t: unsigned long / unsigned int
_NOTHROW = "RKSt9nothrow_t"
_ALIGN = "St11align_val_t"


def _replaceable(size: str) -> list[str]:
    """Every replaceable global operator new/delete, mangled for one size_t."""
    out = []
    for op in ("nw", "na"):  # operator new / new[]
        for tail in ("", _NOTHROW, _ALIGN, _ALIGN + _NOTHROW):
            out.append(f"_Z{op}{size}{tail}")
    for op in ("dl", "da"):  # operator delete / delete[]
        for tail in ("", size, _NOTHROW, _ALIGN, size + _ALIGN, _ALIGN + _NOTHROW):
            out.append(f"_Z{op}Pv{tail}")
    return out


ALLOCATORS = sorted(
    set(itertools.chain.from_iterable(_replaceable(s) for s in _SIZE.values()))
)


@pytest.mark.parametrize("name", ALLOCATORS)
def test_every_replaceable_allocation_function_reads_as_the_runtimes(name: str) -> None:
    # The same verdict for new and delete: before the fix _ZdlPv read as the
    # runtime's and _Znwm as the library's own API.
    assert is_abi_relevant_elf_symbol(name) is False


# A user-declared global overload is the library's own API: consumers that
# call ``new (pool) T`` bind the library's export, which the runtime lacks.
_USER_OVERLOADS = [
    "_ZnwmR4Pool",  # operator new(std::size_t, Pool&)
    "_ZnamR4Pool",  # operator new[](std::size_t, Pool&)
    "_ZdlPvR4Pool",  # operator delete(void*, Pool&)
    "_ZdaPvR4Pool",  # operator delete[](void*, Pool&)
    "_ZnwjPKci",  # operator new(std::size_t, const char*, int), ILP32
    "_ZnwmSt11align_val_tR4Pool",  # aligned, with an extra parameter
]


@pytest.mark.parametrize(
    "name",
    ["_ZN3Foo3nwEm", "_Z4news", "_Z3newv", "api_fn", *_USER_OVERLOADS],
)
def test_a_function_that_only_looks_like_an_allocator_stays_the_librarys(
    name: str,
) -> None:
    # Negative control: a member operator new, plain names that start with
    # "new", and user-declared placement overloads are the library's API.
    assert is_abi_relevant_elf_symbol(name) is True


@pytest.mark.parametrize("name", _USER_OVERLOADS)
def test_dropping_a_user_declared_overload_still_breaks(name: str) -> None:
    # The runtime does not provide it, so its consumers stop linking.
    result = compare(_snap(["_Znwm", "_ZdlPv", name]), _snap(["_Znwm", "_ZdlPv"]))
    assert result.verdict is Verdict.BREAKING, sorted(
        c.kind.name for c in result.changes
    )


def _sym(name: str) -> ElfSymbol:
    return ElfSymbol(name=name, sym_type=SymbolType.FUNC, size=16)


def _snap(exports: list[str]) -> AbiSnapshot:
    """A snapshot with a public header declaring api_fn, as `dump -H` gives."""
    api = Function(
        name="api_fn",
        mangled="api_fn",
        return_type="int",
        visibility=Visibility.PUBLIC,
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    return AbiSnapshot(
        library="libedit.so",
        version="1",
        functions=[api],
        from_headers=True,
        elf=ElfMetadata(
            machine="EM_X86_64",
            symbols=[_sym("api_fn"), *(_sym(n) for n in exports)],
        ),
    )


_SETS = [
    ["_Znwm", "_ZdlPv"],
    ["_Znwm", "_ZdlPv", "_ZdlPvm"],
    ["_Znam", "_ZdaPv"],
    ["_ZnwmSt11align_val_t", "_ZdlPvSt11align_val_t"],
    ["_Znwj", "_ZdlPvj"],
]


@pytest.mark.parametrize("exports", _SETS)
def test_dropping_a_replaced_allocator_is_reported_and_not_a_break(
    exports: list[str],
) -> None:
    result = compare(_snap(exports), _snap([]))
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.ALLOCATOR_REPLACEMENT_REMOVED in kinds, sorted(
        k.name for k in kinds
    )
    # Consumers resolve every allocation function from the C++ runtime once
    # the library stops providing it: nothing they bind disappears.
    assert result.verdict is not Verdict.BREAKING, sorted(k.name for k in kinds)


@pytest.mark.parametrize("exports", _SETS)
def test_starting_to_replace_the_allocator_is_reported(exports: list[str]) -> None:
    result = compare(_snap([]), _snap(exports))
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.ALLOCATOR_REPLACEMENT_ADDED in kinds, sorted(
        k.name for k in kinds
    )
    assert result.verdict is Verdict.COMPATIBLE_WITH_RISK


def test_removing_the_librarys_own_api_beside_the_allocator_still_breaks() -> None:
    # Real break preserved: the fix silences only the allocation functions.
    old = _snap(["_Znwm", "_ZdlPv"])
    new = _snap([])
    new.elf.symbols = [s for s in new.elf.symbols if s.name != "api_fn"]
    result = compare(old, new)
    assert result.verdict is Verdict.BREAKING


# Partial changes to the replaced set: dropping operator new while keeping
# operator delete leaves the runtime's new paired with the library's delete,
# an interposition change each direction must report.
_PARTIAL = [
    (["_Znwm", "_ZdlPv"], ["_ZdlPv"]),
    (["_Znwm", "_ZdlPv", "_ZdlPvm"], ["_Znwm", "_ZdlPv"]),
    (["_Znwj", "_Znaj", "_ZdlPv", "_ZdaPv"], ["_Znwj", "_ZdlPv"]),
    (["_ZnwmSt11align_val_t", "_Znwm", "_ZdlPv"], ["_Znwm", "_ZdlPv"]),
]


@pytest.mark.parametrize(("more", "fewer"), _PARTIAL)
def test_a_partial_change_to_the_replaced_set_is_reported_each_way(
    more: list[str], fewer: list[str]
) -> None:
    dropped = compare(_snap(more), _snap(fewer))
    kinds = {c.kind for c in dropped.changes}
    assert ChangeKind.ALLOCATOR_REPLACEMENT_REMOVED in kinds, sorted(
        k.name for k in kinds
    )
    assert ChangeKind.ALLOCATOR_REPLACEMENT_ADDED not in kinds
    assert dropped.verdict is Verdict.COMPATIBLE_WITH_RISK
    gained = compare(_snap(fewer), _snap(more))
    kinds = {c.kind for c in gained.changes}
    assert ChangeKind.ALLOCATOR_REPLACEMENT_ADDED in kinds, sorted(
        k.name for k in kinds
    )
    assert ChangeKind.ALLOCATOR_REPLACEMENT_REMOVED not in kinds
    assert gained.verdict is Verdict.COMPATIBLE_WITH_RISK


def test_an_unchanged_replaced_set_reports_nothing() -> None:
    same = ["_Znwm", "_ZdlPv", "_ZdlPvm"]
    result = compare(_snap(same), _snap(list(reversed(same))))
    assert result.verdict is Verdict.NO_CHANGE, sorted(
        c.kind.name for c in result.changes
    )
