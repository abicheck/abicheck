# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Template arity must not read a function type's parameter list.

``_count_top_level_template_args`` decides how many template arguments a
name carries. A function-typed argument (``std::function<void(int, int)>``,
Rust's ``Option<extern "C" fn(i32, i32) -> i32>``) contributes its parameter
commas and its ``->`` return arrow; neither changes how many arguments the
template takes, so a callback's signature change must not be reported as a
template gaining a mandatory parameter.
"""

from __future__ import annotations

import pytest

from abicheck.diff_templates import _count_top_level_template_args


@pytest.mark.parametrize(
    "name, expected",
    [
        ("std::function<void(int, int)>", 1),
        ("Foo<int(*)(int, int), char>", 2),
        ('core::option::Option<extern "C" fn(i32, i32) -> i32>', 1),
        ("Foo<fn(i32) -> i32, u8>", 2),
        ("Foo<A<B, C>, fn(x, y) -> Z>", 2),
        ("Foo<int, std::pair<int, char>>", 2),
    ],
)
def test_arity_ignores_function_type_parameters(name: str, expected: int) -> None:
    assert _count_top_level_template_args(name) == expected


@pytest.mark.parametrize(
    "sig_a, sig_b",
    [
        ('Option<extern "C" fn(i32) -> i32>', 'Option<extern "C" fn(i32, i32) -> i32>'),
        ("std::function<int(int)>", "std::function<int(int, int)>"),
    ],
)
def test_fn_type_arity_change_keeps_template_arity(sig_a: str, sig_b: str) -> None:
    assert _count_top_level_template_args(sig_a) == _count_top_level_template_args(
        sig_b
    )
