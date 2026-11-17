# SPDX-License-Identifier: Apache-2.0
"""libstdc++ dual-ABI fixtures shared by the H5 release corpus and its spelling test."""

from __future__ import annotations

from abicheck.model import Function, Param, Visibility

_CXX11_STRING = (
    "std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >"
)
_LEGACY_STRING = (
    "std::basic_string<char, std::char_traits<char>, std::allocator<char> >"
)


def dual_abi_flip_functions() -> tuple[Function, Function]:
    """One std::string function under each ABI: the legacy spelling (removed on the
    new side) and the cxx11 spelling (added)."""
    legacy = Function(
        name="legacy_str",
        mangled="_Z10legacy_strRKSs",
        return_type="int",
        params=[Param(name="s", type="const std::string &")],
        visibility=Visibility.PUBLIC,
    )
    cxx11 = Function(
        name="legacy_str",
        mangled=(
            "_Z10legacy_strRKNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEE"
        ),
        return_type="int",
        params=[Param(name="s", type="const std::__cxx11::string &")],
        visibility=Visibility.PUBLIC,
    )
    return legacy, cxx11


def dual_abi_spellings() -> list[str]:
    """Sibling spellings: both ABIs, the inline namespace in a user namespace and in
    std, the [abi:cxx11] tag, whitespace variants, a generated family, and repeats
    (a memo must answer a repeated input the same as the first call)."""
    base = [
        "f(std::string const&)",
        f"f({_CXX11_STRING} const&)",
        "f[abi:cxx11](int)",
        "api::__cxx11::f(int)",
        "api::f(int)",
        f"g({_LEGACY_STRING})",
        f"g({_CXX11_STRING})",
        "  h( int )  ",
        "",
    ]
    generated = [f"n{i}({_CXX11_STRING} const&, int {i})" for i in range(20)]
    return (base + generated) * 2
