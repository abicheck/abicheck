"""The standard library a binary was built against is read from the binary's
own exports, never from header-parsed manglings or its imports
(``model.stdlib_evidence.stdlib_evidence_symbols``).

Oracle: the Itanium spelling of each runtime -- libc++ instantiations live in
``std::__1`` (``_ZNSt3__1``), libstdc++ ones in plain ``std`` (``_ZNSt6vector``).
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.checker import ChangeKind, compare
from abicheck.diff_stdlib_impl import _effective_build_mode
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.build_mode_facts import StdlibFamily
from abicheck.model.elf_facts import ElfImport, ElfMetadata, ElfSymbol
from abicheck.model.stdlib_evidence import stdlib_evidence_symbols

_LIBCXX_EXPORTS = (
    "_ZNSt3__19allocatorIiE9constructB8ne180100IiJEEEvPT_DpOT0_",
    "_ZNSt3__122__compressed_pair_elemIPiLi0ELb0EEC2B8ne180100IDnvEEOT_",
)
_LIBSTDCXX_EXPORTS = ("_ZNSt6vectorIiSaIiEED2Ev", "_ZNSt6vectorIiSaIiEEC2EmRKS0_")
# What a header parser that reads libstdc++ headers declares, whatever the
# library was built against.
_HEADER_DECL = "_ZNSt6vectorIiSaIiEE9push_backERKi"
# libc++ exports its exception classes in plain std: an import of one says
# nothing about libstdc++.
_LIBCXX_IMPORTS = ("_ZNSt11logic_errorC2EPKc", "_ZTISt12length_error")


def _snap(
    exports: tuple[str, ...], *, version: str = "1", with_imports: bool = True
) -> AbiSnapshot:
    snap = AbiSnapshot(
        library="libk.so.1",
        version=version,
        functions=[
            Function(
                name="push_back",
                mangled=_HEADER_DECL,
                return_type="void",
                visibility=Visibility.PUBLIC,
            )
        ],
    )
    snap.elf = ElfMetadata(
        symbols=[ElfSymbol(name=n) for n in exports],
        imports=[ElfImport(name=n) for n in (_LIBCXX_IMPORTS if with_imports else ())],
    )
    return snap


@pytest.mark.parametrize(
    ("exports", "family"),
    [
        (_LIBCXX_EXPORTS, StdlibFamily.LIBCXX),
        (_LIBSTDCXX_EXPORTS, StdlibFamily.LIBSTDCXX),
    ],
)
@pytest.mark.parametrize("with_imports", [False, True])
def test_family_follows_the_exports(
    exports: tuple[str, ...], family: StdlibFamily, with_imports: bool
) -> None:
    snap = _snap(exports, with_imports=with_imports)
    assert stdlib_evidence_symbols(snap) == list(exports)
    assert _effective_build_mode(snap).stdlib is family


def test_declarations_are_the_fallback_without_a_symbol_table() -> None:
    snap = _snap(())
    snap.elf = None
    assert stdlib_evidence_symbols(snap) == [_HEADER_DECL]


@pytest.mark.parametrize(
    ("old", "new"),
    list(itertools.product((_LIBCXX_EXPORTS, _LIBSTDCXX_EXPORTS), repeat=2)),
)
def test_a_runtime_switch_is_reported_exactly_when_the_exports_switch(
    old: tuple[str, ...], new: tuple[str, ...]
) -> None:
    result = compare(_snap(old), _snap(new, version="2"))
    reported = any(
        c.kind is ChangeKind.STDLIB_IMPLEMENTATION_CHANGED for c in result.changes
    )
    assert reported is (old != new)
