# SPDX-License-Identifier: Apache-2.0
"""Tests for forwarded compiler dialect option predicates."""

from abicheck._compiler_options import has_explicit_cpp_std, has_explicit_std


def test_has_explicit_std_accepts_string_and_tokens() -> None:
    assert has_explicit_std("-O2 -std=c17")
    assert has_explicit_std(None, ("/std:c++20",))
    assert not has_explicit_std("-O2", ("-Wall",))


def test_has_explicit_cpp_std_distinguishes_c_and_cpp() -> None:
    assert has_explicit_cpp_std("-O2 -std=gnu++17")
    assert has_explicit_cpp_std(None, ("/std:c++latest",))
    assert not has_explicit_cpp_std("-std=c17")
    assert not has_explicit_cpp_std(None, ("-Wall",))
