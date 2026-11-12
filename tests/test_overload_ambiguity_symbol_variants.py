"""``overload_ambiguity_introduced`` when a declaration is recorded under its
real symbols.

Regression for the native gcc header backend gap "records no overload-set
ambiguity" (edit-corpus operator ``converting_ctor_overload_added``). That
backend records a constructor under both Itanium symbols, the complete
(``C1``) and the base (``C2``) object constructor. Castxml and clang record
one synthetic header key instead. The detector read the second copy of the
kept ``S(int)`` as a rival overload that ties with the first, concluded that
``S s({})`` was already ambiguous, and withdrew every witness.

General invariant (bug class ``extraction.backend_fact_family_parity``): the
detector's answer does not depend on how many symbols carry one declaration.
For every (old, added) pair of the parameter-type domain shared with
``test_overload_ambiguity.py``, and for every (old pair, added) triple, the
finding over a ``C1``+``C2`` spelling equals the finding over the
single-entry spelling. The integration test checks the real gcc plugin
against castxml on a compiled library.
"""

from __future__ import annotations

import itertools
import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.checker import ChangeKind, compare
from abicheck.model import (
    AbiSnapshot,
    EnumType,
    Function,
    Param,
    RecordType,
    Visibility,
)

_KIND = ChangeKind.OVERLOAD_AMBIGUITY_INTRODUCED

#: header spelling -> Itanium parameter mangling (written out by hand)
_MANGLED = {
    "int": "i",
    "long": "l",
    "double": "d",
    "char *": "Pc",
    "fnptr_t": "PFivE",
    "E": "1E",
    "Widget": "6Widget",
    "const Widget &": "RK6Widget",
    "const int &": "RKi",
    "long &&": "Ol",
    "std::initializer_list<int>": "St16initializer_listIiE",
    "const std::initializer_list<int> &": "RKSt16initializer_listIiE",
}


def _synthetic(ptype: str) -> list[Function]:
    return [
        Function(
            name="S",
            mangled=f"__abicheck_ctor__ns::S({ptype})",
            return_type="void",
            params=[Param(name="v", type=ptype)],
            visibility=Visibility.PUBLIC,
        )
    ]


def _itanium(ptype: str) -> list[Function]:
    return [
        Function(
            name="ns::S::S",
            mangled=f"_ZN2ns1S{variant}E{_MANGLED[ptype]}",
            return_type="void",
            params=[Param(name="v", type=ptype)],
            visibility=Visibility.PUBLIC,
        )
        for variant in ("C1", "C2")
    ]


def _snap(funcs: list[Function]) -> AbiSnapshot:
    return AbiSnapshot(
        library="lib.so",
        version="1",
        from_headers=True,
        functions=funcs,
        typedefs={"fnptr_t": "int (*)()"},
        enums=[EnumType(name="E", members=[])],
        types=[RecordType(name="Widget", kind="struct")],
    )


def _fires(spell, old: tuple[str, ...], added: str) -> bool:
    base = [f for t in old for f in spell(t)]
    new = _snap([*base, *spell(added)])
    return _KIND in {c.kind for c in compare(_snap(base), new).changes}


_PAIRS = list(itertools.permutations(_MANGLED, 2))
_TRIPLES = list(itertools.permutations(_MANGLED, 3))


@pytest.mark.parametrize(("a", "b"), _PAIRS)
def test_symbol_variants_do_not_change_the_pair_finding(a: str, b: str) -> None:
    assert _fires(_itanium, (a,), b) == _fires(_synthetic, (a,), b)


def test_symbol_variants_do_not_change_the_whole_set_finding() -> None:
    wrong = [
        (a, c, b)
        for a, c, b in _TRIPLES
        if _fires(_itanium, (a, c), b) != _fires(_synthetic, (a, c), b)
    ]
    assert not wrong, wrong


def test_the_reported_shape_fires_under_both_spellings() -> None:
    """``S(int)`` then ``S(long)``: ``S s({})`` compiles, then is ambiguous."""
    assert _fires(_synthetic, ("int",), "long")
    assert _fires(_itanium, ("int",), "long")


def test_a_set_already_ambiguous_stays_unreported_under_variants() -> None:
    """Negative control: ``{S(int), S(double)}`` was already ambiguous for
    ``{}``, so adding ``S(long)`` introduces nothing, however it is spelled."""
    assert not _fires(_itanium, ("int", "double"), "long")
    assert not _fires(_synthetic, ("int", "double"), "long")


_V1 = "namespace ns { struct CO { CO(int); int v; }; }\n"
_V2 = "namespace ns { struct CO { CO(int); CO(long); int v; }; }\n"
_SRC = {
    "v1": "namespace ns { CO::CO(int x) : v(x) {} }\n",
    "v2": "namespace ns { CO::CO(int x) : v(x) {} CO::CO(long x) : v(int(x)) {} }\n",
}


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["castxml", "gcc"])
def test_real_backends_report_the_converting_ctor_ambiguity(
    backend: str, tmp_path: Path
) -> None:
    if shutil.which("g++") is None:
        pytest.skip("g++ not available")
    if backend == "gcc":
        from abicheck.errors import SnapshotError
        from abicheck.extract.headers.gcc.discovery import find_plugin

        try:
            find_plugin("g++", "c++")
        except SnapshotError:
            pytest.skip("native gcc plugin not built")
    from abicheck.dumper import dump

    snaps = []
    for tag, header in (("v1", _V1), ("v2", _V2)):
        hdr = tmp_path / f"{tag}.hpp"
        hdr.write_text(header)
        cpp = tmp_path / f"{tag}.cpp"
        cpp.write_text(f'#include "{tag}.hpp"\n{_SRC[tag]}')
        so = tmp_path / f"lib{tag}.so"
        subprocess.run(["g++", "-shared", "-fPIC", "-o", str(so), str(cpp)], check=True)
        snaps.append(dump(so, [hdr], header_backend=backend))
    kinds = {c.kind for c in compare(*snaps).changes}
    assert _KIND in kinds
