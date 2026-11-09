"""``visibility_leak`` judges an export by the name it declares, not what it mentions.

Found by the config-matrix edit corpus (``rebuild_hidden_visibility`` on the
``std::string`` seed, binary evidence): the OLD library's public
``greet(std::string const&)`` and ``name_len(std::string const&)`` were
reported as "internal-looking" exports because the ``internal``/``__`` name
heuristic scanned the whole mangled string -- and libstdc++'s dual-ABI
``std::__cxx11`` namespace sits in every ``std::string`` *parameter*. The same
report counted the weak ``__gnu_cxx::char_traits<char>`` instantiations every
``std::string`` user exports, which the transitive-runtime filter knew only in
their demangled spelling.

Bug class ``classification.name_shape_as_contract_membership`` (corollary b:
only the scope that owns the entity counts). The oracle is the generator:
each mangled name is built here from known components with a local encoder,
so the expected answer never comes from the parser under test.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.checker import ChangeKind, compare
from abicheck.elf_symbol_filter import is_abi_relevant_elf_symbol
from abicheck.model import AbiSnapshot, Function, Visibility

# Identifiers with no internal-looking spelling, and ones each carrying one.
PUBLIC = ("greet", "core", "Token", "api", "Index", "name_len", "v2")
INTERNAL = ("detail", "internal", "helper", "_impl", "__priv", "do_private")

# Parameter types by hand-written Itanium encoding. Several *spell* an
# internal-looking name; none may matter to the function's own name.
STRING = "RKNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEE"  # const std::string&
PARAMS = {
    "int": "i",
    "std::string const&": STRING,
    "detail::Token const&": "RKN6detail5TokenE",
    "mylib::internal::Node*": "PN5mylib8internal4NodeE",
    "std::__cxx11::list<int>&": "RNSt7__cxx114listIiSaIiEEE",
}


def _src(name: str) -> str:
    return f"{len(name)}{name}"


def _mangle(scope: tuple[str, ...], leaf: str, params: tuple[str, ...]) -> str:
    """``scope::leaf(params...)`` as an Itanium function symbol."""
    head = (
        "_ZN" + "".join(_src(c) for c in (*scope, leaf)) + "E"
        if scope
        else "_Z" + _src(leaf)
    )
    return head + ("".join(PARAMS[p] for p in params) or "v")


def _old(symbols: list[str]) -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        functions=[
            Function(name=s, mangled=s, return_type="?", visibility=Visibility.ELF_ONLY)
            for s in symbols
        ],
        elf_only_mode=True,
    )


def _leaks(symbols: list[str]) -> list[str]:
    """The exports the VISIBILITY_LEAK finding names (comparing *symbols* to themselves)."""
    result = compare(_old(symbols), _old(symbols))
    found = [c for c in result.changes if c.kind == ChangeKind.VISIBILITY_LEAK]
    if not found:
        return []
    (leak,) = found
    names = leak.description.rsplit(": ", 1)[1].split(", ")
    assert leak.old_value == str(
        len(names)
    )  # the count agrees with the list (< 5 here)
    return names


idents = st.sampled_from(PUBLIC + INTERNAL)
param_lists = st.lists(st.sampled_from(sorted(PARAMS)), max_size=3).map(tuple)


@settings(max_examples=300, deadline=None)
@given(
    scope=st.lists(idents, max_size=3).map(tuple),
    leaf=idents,
    params=param_lists,
)
def test_only_the_declared_name_decides_a_leak(
    scope: tuple[str, ...], leaf: str, params: tuple[str, ...]
) -> None:
    symbol = _mangle(scope, leaf, params)
    expected = any(c in INTERNAL for c in (*scope, leaf))  # known by construction
    assert bool(_leaks([symbol])) is expected, symbol


@settings(max_examples=200, deadline=None)
@given(
    scope=st.lists(st.sampled_from(PUBLIC), max_size=3).map(tuple),
    leaf=st.sampled_from(PUBLIC),
    params=param_lists,
)
def test_parameter_types_never_change_the_answer(
    scope: tuple[str, ...], leaf: str, params: tuple[str, ...]
) -> None:
    """Metamorphic: adding parameters to a public function adds no leak."""
    assert (
        _leaks([_mangle(scope, leaf, params)])
        == _leaks([_mangle(scope, leaf, ())])
        == []
    )


@pytest.mark.parametrize("namespace", ["__gnu_cxx", "__gnu_debug", "__cxxabiv1"])
@pytest.mark.parametrize("quals", ["", "K", "V", "VK", "rVK", "R", "O", "KR", "KO"])
def test_every_runtime_namespace_member_is_transitive_under_any_qualifier(
    namespace: str, quals: str
) -> None:
    # namespace::char_traits<char>::eq(char const&, char const&) [quals]
    symbol = f"_ZN{quals}{_src(namespace)}11char_traitsIcE2eqERKcS3_"
    assert not is_abi_relevant_elf_symbol(symbol)
    # ... unless the inspected library is the C++ runtime itself.
    assert is_abi_relevant_elf_symbol(symbol, filter_transitive_runtime_symbols=False)


@pytest.mark.parametrize(
    "symbol",
    [
        "_ZN5mylib3fooEv",  # an ordinary namespace
        "_ZN10__gnu_cxxx3fooEv",  # a different, longer identifier
        "_ZN3fooIN9__gnu_cxx1XEE3barEv",  # the runtime namespace only in a template argument
        "_Z3fooN9__gnu_cxx1XE",  # ... or only in a parameter type
    ],
)
def test_a_name_that_merely_mentions_a_runtime_namespace_stays_relevant(
    symbol: str,
) -> None:
    assert is_abi_relevant_elf_symbol(symbol)


def test_the_reported_std_string_library_has_no_leak() -> None:
    """The corpus cell's OLD library export table, spelled out."""
    exports = [
        "_Z5greetRKNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEE",
        "_Z8add_intsii",
        "_Z8name_lenRKNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEE",
        "_ZN9__gnu_cxx11char_traitsIcE2eqERKcS3_",
        "_ZN9__gnu_cxx11char_traitsIcE6lengthEPKc",
        "_ZNSt11char_traitsIcE6lengthEPKc",
        "_ZSt23__is_constant_evaluatedv",
    ]
    assert _leaks(exports) == []
    # A real internal export beside them is still reported, and alone.
    real = "_ZN5mylib6detail6expandEv"
    assert _leaks([*exports, real]) == [real]


# Public shapes g++ 13 emits for a std-heavy API (``nm -D`` of a real build,
# checked with ``c++filt``): container and string parameters, the dual-ABI
# ``[abi:cxx11]`` tag on a name returning ``std::string``, a constructor, a
# const member, a function template instantiated over ``std::string`` and a
# ``__gnu_cxx::__normal_iterator`` parameter. None declares an internal name.
PUBLIC_STD_SHAPES = (
    "_ZN5mylib3sumERKSt6vectorIiSaIiEE",  # sum(std::vector<int> const&)
    "_ZN5mylib4keysERKSt3mapINSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEEiSt4lessIS6_ESaISt4pairIKS6_iEEE",
    "_ZN5mylib5countERKNSt7__cxx114listINS0_12basic_stringIcSt11char_traitsIcESaIcEEESaIS6_EEE",
    "_ZN5mylib5firstEN9__gnu_cxx17__normal_iteratorIPiSt6vectorIiSaIiEEEE",
    "_ZN5mylib6Parser5parseERKNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEE",
    "_ZN5mylib6Parser7versionB5cxx11Ev",  # static std::string version() [abi:cxx11]
    "_ZN5mylib6ParserC1ERKNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEE",
    "_ZN5mylib6ParserC2ERKNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEE",
    "_ZNK5mylib6Parser4nameB5cxx11Ev",  # std::string name() const [abi:cxx11]
    "_ZN5mylib7convertINSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEEEET_PKc",
    "_ZN5mylib8greetingB5cxx11Ev",
)


@pytest.mark.parametrize("symbol", PUBLIC_STD_SHAPES)
def test_a_public_std_shape_is_not_a_leak(symbol: str) -> None:
    assert _leaks([symbol]) == []


@pytest.mark.parametrize(
    "symbol",
    [
        # mylib::detail::parse(std::string const&)
        "_ZN5mylib6detail5parseERKNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEE",
        # mylib::internal::render[abi:cxx11](int)
        "_ZN5mylib8internal6renderB5cxx11Ei",
    ],
)
def test_an_internal_scope_is_a_leak_whatever_it_takes_or_returns(symbol: str) -> None:
    assert _leaks([symbol]) == [symbol]


# The whole dynamic export table of that g++ 13 -O0 build: the API above, the
# libstdc++ weak instantiations it pulls in (``std::``, ``__gnu_cxx::``, and
# ``_ZZ…_Guard`` local classes) and the two internal-scope functions.
REAL_EXPORT_TABLE = (
    "_ZN5mylib3sumERKSt6vectorIiSaIiEE",
    "_ZN5mylib4keysERKSt3mapINSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEEiSt4lessIS6_ESaISt4pairIKS6_iEEE",
    "_ZN5mylib5countERKNSt7__cxx114listINS0_12basic_stringIcSt11char_traitsIcESaIcEEESaIS6_EEE",
    "_ZN5mylib5firstEN9__gnu_cxx17__normal_iteratorIPiSt6vectorIiSaIiEEEE",
    "_ZN5mylib6Parser5parseERKNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEE",
    "_ZN5mylib6Parser7versionB5cxx11Ev",
    "_ZN5mylib6ParserC1ERKNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEE",
    "_ZN5mylib6ParserC2ERKNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEE",
    "_ZN5mylib6detail5parseERKNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEE",
    "_ZN5mylib7convertINSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEEEET_PKc",
    "_ZN5mylib8greetingB5cxx11Ev",
    "_ZN5mylib8internal6renderB5cxx11Ei",
    "_ZN9__gnu_cxx11char_traitsIcE2eqERKcS3_",
    "_ZN9__gnu_cxx11char_traitsIcE6lengthEPKc",
    "_ZNK5mylib6Parser4nameB5cxx11Ev",
    "_ZNSt11char_traitsIcE6lengthEPKc",
    "_ZNSt15__new_allocatorIcED1Ev",
    "_ZNSt15__new_allocatorIcED2Ev",
    "_ZNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEE12_Alloc_hiderD1Ev",
    "_ZNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEE12_Alloc_hiderD2Ev",
    "_ZNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEE12_M_constructIPKcEEvT_S8_St20forward_iterator_tag",
    "_ZNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEC1IS3_EEPKcRKS3_",
    "_ZNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEC2IS3_EEPKcRKS3_",
    "_ZSt23__is_constant_evaluatedv",
    "_ZZNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEE12_M_constructIPKcEEvT_S8_St20forward_iterator_tagEN6_GuardC1EPS4_",
    "_ZZNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEE12_M_constructIPKcEEvT_S8_St20forward_iterator_tagEN6_GuardC2EPS4_",
    "_ZZNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEE12_M_constructIPKcEEvT_S8_St20forward_iterator_tagEN6_GuardD1Ev",
    "_ZZNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEE12_M_constructIPKcEEvT_S8_St20forward_iterator_tagEN6_GuardD2Ev",
)


def test_the_real_export_table_leaks_exactly_its_internal_scopes() -> None:
    assert _leaks(list(REAL_EXPORT_TABLE)) == [
        "_ZN5mylib6detail5parseERKNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEE",
        "_ZN5mylib8internal6renderB5cxx11Ei",
    ]
