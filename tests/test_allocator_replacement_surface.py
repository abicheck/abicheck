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


@pytest.mark.parametrize("name", ["_ZN3Foo3nwEm", "_Z4news", "_Z3newv", "api_fn"])
def test_a_function_that_only_looks_like_an_allocator_stays_the_librarys(
    name: str,
) -> None:
    # Negative control: a member operator new and plain names that start
    # with "new" are the library's API.
    assert is_abi_relevant_elf_symbol(name) is True


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
