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

from __future__ import annotations

import pytest

from abicheck.name_classification import (
    ITANIUM_RTTI_PREFIXES,
    LOCAL_RTTI_PREFIXES,
    RTTI_DATA_PREFIXES,
    has_internal_namespace_component,
    is_abi_surface_type_name,
    is_compiler_internal_type,
    is_cxx_runtime_library,
    is_local_name_symbol,
    is_local_rtti_symbol,
    is_non_abi_surface_type,
    is_rtti_symbol,
    is_stdlib_local_name_symbol,
    symbol_origin,
)


@pytest.mark.parametrize(
    "name",
    ["_ZTV4Base", "_ZTI4Base", "_ZTS4Base", "_ZTT4Base", "_ZTc0_4Base", "_ZTh8_N3FooE"],
)
def test_is_rtti_symbol_true(name: str) -> None:
    assert is_rtti_symbol(name)


@pytest.mark.parametrize("name", ["_ZN3Foo3barEv", "main", "", "_Z3fooi"])
def test_is_rtti_symbol_false(name: str) -> None:
    assert not is_rtti_symbol(name)


@pytest.mark.parametrize("name", ["_ZTIZ4mainEUlvE_", "_ZTSZ3fooEUliE_", "_ZTVZ1gEvE", "_ZTTZ1hEvE"])
def test_local_rtti_detected(name: str) -> None:
    assert is_local_rtti_symbol(name)
    # A function-local RTTI symbol is still a generic RTTI symbol.
    assert is_rtti_symbol(name)


def test_non_local_rtti_not_flagged_local() -> None:
    assert not is_local_rtti_symbol("_ZTI4Base")


@pytest.mark.parametrize(
    "name",
    [
        "_ZZ4mainE1x",
        "_ZZNKSt7__cxx1112regex_traitsIcE16lookup_classnameIPKcEENS1_10_RegexMaskET_S6_bE12__classnames",
    ],
)
def test_is_local_name_symbol_true(name: str) -> None:
    assert is_local_name_symbol(name)


@pytest.mark.parametrize("name", ["_ZN3Foo3barEv", "_ZTIZ4mainEUlvE_", "main", ""])
def test_is_local_name_symbol_false(name: str) -> None:
    # A local-RTTI symbol (_ZTIZ...) is a distinct production (typeinfo of a
    # local type), not the bare local-name production this checks for.
    assert not is_local_name_symbol(name)


@pytest.mark.parametrize(
    "name",
    [
        # Real symbol from a live pvxs binary (PR #641 validation): a const
        # member function (_ZZNK...), std::__cxx11:: nested namespace.
        "_ZZNKSt7__cxx1112regex_traitsIcE16lookup_classnameIPKcEENS1_10_RegexMaskET_S6_bE12__classnames",
        "_ZZNSt6vectorIiSaIiEE9push_backERKiE1x",  # plain (non-const) std:: member
        "_ZZN9__gnu_cxx13new_allocatorIiE10deallocateEPim1E",
        "_ZZN10__cxxabiv116__enum_type_infoD2Ev1x",
        "_ZZSt4sortIN9__gnu_cxx17__normal_iteratorIPiSt6vectorIiSaIiEEEEEvT_S6_1x",
        # CodeRabbit review, PR #641: a restrict-qualified ('r') stdlib
        # member function's local name must also match -- an earlier version
        # of the qualifier character class omitted 'r' entirely.
        "_ZZNr10__cxxabiv116__enum_type_infoD2Ev1x",
        # Codex review, PR #641: Itanium standard-substitution codes for
        # common std:: templates (Sa/Sb/Ss/Si/So/Sd) occupy the exact
        # grammar slot the bare `St` substitution does, and must match too.
        "_ZZNKSaIiE1fEvE1x",  # std::allocator<int>
        "_ZZNSbIcSt11char_traitsIcESaIcEE1fEvE1x",  # std::basic_string
        "_ZZNKSs1fEvE1x",  # std::string
        "_ZZNKSi1fEvE1x",  # std::istream
        "_ZZNKSo1fEvE1x",  # std::ostream
        "_ZZNKSd1fEvE1x",  # std::iostream
        # Codex review, PR #641: libstdc++ debug mode's __gnu_debug::
        # namespace is already a recognized stdlib/runtime implementation
        # namespace elsewhere (_STDLIB_TYPE_NAMESPACE_PREFIXES); a local
        # static in one of its functions must match here too.
        "_ZZN11__gnu_debug6vectorIiSaIiEE9push_backERKiE1x",
        # Codex review, PR #641: a recursively-nested <local-name> -- a
        # static local to a lambda's own call operator, where the lambda is
        # itself local to a stdlib function (std::outer()). Real GCC output.
        "_ZZZSt5outervENKUlvE_clEvE1x",
    ],
)
def test_is_stdlib_local_name_symbol_true(name: str) -> None:
    assert is_stdlib_local_name_symbol(name)


def test_is_stdlib_local_name_symbol_nested_but_user_owned_false() -> None:
    # Same recursive-nesting shape as the stdlib case above, but the
    # outermost function belongs to the library under test (pvxs::foo), not
    # the C++ runtime -- must NOT be classified as stdlib-owned even though
    # it also carries multiple leading Zs.
    assert not is_stdlib_local_name_symbol("_ZZZN4pvxs3fooEvENKUlvE_clEvE1x")


@pytest.mark.parametrize(
    "name",
    [
        # Codex review, PR #641: a user specialization of a standard
        # customization-point template contains USER-AUTHORED code, so it
        # must NOT be classified as stdlib-owned even though it nominally
        # lives in namespace std. Real GCC output for an inline
        # std::hash<MyType>::operator()'s local static.
        "_ZZNKSt4hashI6MyTypeEclERKS0_E4salt",
        "_ZZNKSt4lessI6MyTypeEclERKS0_S3_E1x",
        "_ZZNKSt7greaterI6MyTypeEclERKS0_S3_E1x",
    ],
)
def test_is_stdlib_local_name_symbol_user_specialized_customization_point_false(
    name: str,
) -> None:
    assert not is_stdlib_local_name_symbol(name)


@pytest.mark.parametrize(
    "name",
    [
        # A library-under-test's own public inline/template function's local
        # static (Codex review, PR #641): must NOT be classified as
        # stdlib-owned, since consumers can genuinely bind against it.
        "_ZZN4pvxs6client6ConfigEvE5cache",
        "_ZN3Foo3barEv",  # not a local-name symbol at all
        # CodeRabbit review, PR #641: a truncated "10__cxxabiv" (9-character
        # name after a length-10 prefix -- not a real, never-emitted
        # spelling) followed by an arbitrary next character must NOT match;
        # an earlier version's optional trailing "1" on __cxxabiv1 over-matched
        # this.
        "_ZZN10__cxxabivEv1x",
        "",
    ],
)
def test_is_stdlib_local_name_symbol_false(name: str) -> None:
    assert not is_stdlib_local_name_symbol(name)


@pytest.mark.parametrize(
    "name",
    ["_ZN4daal8internal3FooEv", "_ZN3lib6detail4implE", "_ZN3lib8__detailE", "_ZN3lib5_implE"],
)
def test_internal_namespace_component(name: str) -> None:
    assert has_internal_namespace_component(name)


def test_internal_substring_not_matched_without_length_prefix() -> None:
    # "internal" without the conventional length prefix must not match.
    assert not has_internal_namespace_component("_ZN3lib8internelE")  # typo, no "8internal"
    assert not has_internal_namespace_component("my_internal_helper")


def test_symbol_origin_rtti_beats_internal() -> None:
    # RTTI for an internal type classifies as "rtti" (RTTI checked first).
    assert symbol_origin("_ZTIN4daal8internal3FooE") == "rtti"


def test_symbol_origin_buckets() -> None:
    assert symbol_origin("_ZTV4Base") == "rtti"
    assert symbol_origin("_ZN4daal8internal3FooEv") == "internal"
    assert symbol_origin("_ZN3Foo3barEv") == "public"
    assert symbol_origin("") == "public"


def test_data_prefixes_are_subset_of_generic() -> None:
    # The size-owning data objects are a subset of the generic RTTI artifacts.
    assert set(RTTI_DATA_PREFIXES) <= set(ITANIUM_RTTI_PREFIXES)
    # Local-RTTI prefixes are the generic-data prefixes plus the local marker "Z".
    assert all(p[:-1] in ITANIUM_RTTI_PREFIXES for p in LOCAL_RTTI_PREFIXES)


def test_report_summary_reexport_is_same_callable() -> None:
    from abicheck.report_summary import classify_symbol_origin

    assert classify_symbol_origin is symbol_origin


# --- type-name classification (moved from model.py in C10) -------------------


def test_is_compiler_internal_type() -> None:
    assert is_compiler_internal_type("__va_list_tag")
    assert is_compiler_internal_type("__int128")
    assert not is_compiler_internal_type("MyStruct")
    assert not is_compiler_internal_type("")


def test_is_non_abi_surface_type_stdlib_and_anonymous() -> None:
    assert is_non_abi_surface_type("std::vector<int>")
    assert is_non_abi_surface_type("__gnu_cxx::__normal_iterator")
    assert is_non_abi_surface_type("Foo::(anonymous struct)")
    assert is_non_abi_surface_type("Outer::{lambda(int)#1}")
    assert not is_non_abi_surface_type("mylib::PublicType")
    # When the inspected DSO IS the runtime, std:: is its own surface.
    assert not is_non_abi_surface_type("std::string", exclude_stdlib_namespaces=False)


def test_is_abi_surface_type_name_is_inverse() -> None:
    assert is_abi_surface_type_name("mylib::PublicType", exclude_stdlib=True)
    assert not is_abi_surface_type_name("std::vector<int>", exclude_stdlib=True)


def test_is_cxx_runtime_library() -> None:
    assert is_cxx_runtime_library("libstdc++.so.6")
    assert is_cxx_runtime_library("/usr/lib/libc++.so.1")
    assert is_cxx_runtime_library("stdc++")  # short ABICC -lib form
    assert not is_cxx_runtime_library("libmylib.so.1")
    assert not is_cxx_runtime_library(None)


def test_model_reexports_are_the_same_objects() -> None:
    # Back-compat: ~9 detector modules import these from model. The re-export
    # must be the very same object as the canonical definition.
    from abicheck import model

    assert model.is_non_abi_surface_type is is_non_abi_surface_type
    assert model.is_compiler_internal_type is is_compiler_internal_type
    assert model.is_abi_surface_type_name is is_abi_surface_type_name
    assert model.is_cxx_runtime_library is is_cxx_runtime_library
    # The constant was public on model before C10; keep it importable there.
    from abicheck.name_classification import COMPILER_INTERNAL_TYPES

    assert model.COMPILER_INTERNAL_TYPES is COMPILER_INTERNAL_TYPES


def test_stdlib_rtti_prefixes_is_canonical_union() -> None:
    """STDLIB_RTTI_PREFIXES is the single source of truth merged from the two
    historically-separate copies (elf_symbol_filter surface filter +
    diff_elf_layout L0 exclusion). Pin its membership and prove it is a superset
    of both historical sets so neither call site lost coverage (N-D merge).
    """
    from abicheck import diff_elf_layout, elf_symbol_filter
    from abicheck.name_classification import STDLIB_RTTI_PREFIXES

    # Both call sites now share the very same canonical object.
    assert elf_symbol_filter._STDLIB_RTTI_PREFIXES is STDLIB_RTTI_PREFIXES
    assert diff_elf_layout._RUNTIME_RTTI_PREFIXES is STDLIB_RTTI_PREFIXES

    # The historical memberships (verbatim, pre-merge) — the canonical set must
    # be a superset of each so no symbol previously matched stops matching.
    historical_elf_stdlib_rtti = {
        "_ZTISt", "_ZTSSt", "_ZTVSt", "_ZTTSt",
        "_ZTINSt", "_ZTSNSt", "_ZTVNSt", "_ZTTNSt",
        "_ZTIN9__gnu_cxx", "_ZTSN9__gnu_cxx", "_ZTVN9__gnu_cxx", "_ZTTN9__gnu_cxx",
        "_ZTIN10__cxxabiv", "_ZTSN10__cxxabiv", "_ZTTN10__cxxabiv",
        "_ZTIN7__cxx11", "_ZTSN7__cxx11", "_ZTVN7__cxx11", "_ZTTN7__cxx11",
    }
    historical_layout_runtime_rtti = {
        "_ZTVN10__cxxabiv", "_ZTIN10__cxxabiv", "_ZTSN10__cxxabiv",
        "_ZTVSt", "_ZTISt", "_ZTSSt",
        "_ZTVNSt", "_ZTINSt", "_ZTSNSt",
        "_ZTVN9__gnu_cxx", "_ZTIN9__gnu_cxx", "_ZTSN9__gnu_cxx",
    }
    canonical = set(STDLIB_RTTI_PREFIXES)
    assert historical_elf_stdlib_rtti <= canonical
    assert historical_layout_runtime_rtti <= canonical
    # The canonical set is exactly the union — no stray extra prefixes.
    assert canonical == historical_elf_stdlib_rtti | historical_layout_runtime_rtti
    # No duplicates in the tuple.
    assert len(STDLIB_RTTI_PREFIXES) == len(canonical)

    # The only entry layout gained from the merge is _ZTVN10__cxxabiv; for
    # elf_symbol_filter that prefix is already covered by _STDLIB_PREFIXES, so
    # the elf surface filter is unchanged.
    only_new_for_layout = canonical - historical_layout_runtime_rtti
    assert only_new_for_layout == (
        historical_elf_stdlib_rtti - historical_layout_runtime_rtti
    )
    assert any(
        "_ZTVN10__cxxabiv".startswith(p) or p == "_ZTVN10__cxxabiv"
        for p in elf_symbol_filter._STDLIB_PREFIXES
    )
