# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Override identity keys parameters through the signature canonicalizer.

Bug class: two normalizers for "are these the same parameter type".
``dumper_clang_vtable`` kept its own top-level-cv stripper, narrower than
``signature_normalization.canonicalize_function_signature_param_type``: it
missed array-to-pointer adjustment and by-value cv inside a callback
parameter, so a derived ``f(int *)`` did not override a base ``f(int[])``.
The oracle is the language rule: spellings that mangle identically key
identically, and real overload discriminators stay distinct.
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("base", "derived", "same"),
    [
        # An array parameter is adjusted to a pointer: one signature.
        ("int[]", "int *", True),
        ("int[4]", "int *", True),
        # A by-value cv inside a callback parameter is dropped there too.
        ("void (*)(const int)", "void (*)(int)", True),
        # DWARF/GCC vs clang integer spelling of one type.
        ("long unsigned int", "unsigned long", True),
        # Pointee qualifiers are real overload discriminators.
        ("const char *", "char *", False),
        ("int *const *", "int **", False),
    ],
)
def test_override_parameter_identity_is_the_signature_identity(
    base: str, derived: str, same: bool
) -> None:
    from abicheck.model.signature_normalization import (
        canonicalize_function_signature_param_type as key,
    )

    assert (key(base) == key(derived)) is same


@pytest.mark.parametrize(
    ("base", "derived"),
    [
        ("int[]", "int *"),
        ("int[8]", "int *"),
        ("void (*)(const int)", "void (*)(int)"),
    ],
)
def test_override_key_of_two_spellings_of_one_signature_agrees(
    base: str, derived: str
) -> None:
    """The method key -- what override detection compares -- agrees for two
    parameter spellings of the same function signature (array adjustment,
    by-value cv inside a callback); the vtable path kept its own narrower
    normalizer and missed both."""
    from abicheck.dumper_clang_vtable import _method_signature_key

    def method(param: str) -> dict:
        return {
            "kind": "CXXMethodDecl",
            "name": "f",
            "type": {"qualType": f"void ({param})"},
            "inner": [{"kind": "ParmVarDecl", "type": {"qualType": param}}],
        }

    assert _method_signature_key(method(base)) == _method_signature_key(method(derived))
