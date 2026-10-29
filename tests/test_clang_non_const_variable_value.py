"""The clang backend records a non-const header variable's initializer as its
value, and leaves const values to the constant channel.

Oracle: the clang AST shape of ``inline int x = 1;`` (an ``IntegerLiteral``
initializer under the ``VarDecl``), which the test builds from the documented
JSON dump, not from the helper under test.
"""

from __future__ import annotations

from abicheck.extract.headers.clang.expr import (
    is_const_variable,
    non_const_variable_value,
)


def _var(
    type_name: str,
    *,
    constexpr: bool = False,
    inline: bool = True,
    value: str = "1",
) -> dict:
    return {
        "kind": "VarDecl",
        "name": "x",
        "type": {"qualType": type_name},
        **({"constexpr": True} if constexpr else {}),
        **({"inline": True} if inline else {}),
        "inner": [
            {"kind": "IntegerLiteral", "value": value, "type": {"qualType": "int"}}
        ],
    }


def test_non_const_public_variable_carries_its_initializer() -> None:
    node = _var("int")
    assert non_const_variable_value(node, "int", "public") == "1"
    assert non_const_variable_value(node, "int", None) == "1"


def test_non_const_value_follows_the_initializer_not_a_fixed_default() -> None:
    assert non_const_variable_value(_var("int", value="2"), "int", "public") == "2"


def test_const_and_constexpr_stay_with_the_constant_channel() -> None:
    assert non_const_variable_value(_var("const int"), "const int", "public") is None
    node = _var("const int", constexpr=True)
    assert is_const_variable(node, "const int") is True
    assert non_const_variable_value(node, "const int", "public") is None


def test_private_and_protected_variables_carry_no_value() -> None:
    node = _var("int")
    assert non_const_variable_value(node, "int", "private") is None
    assert non_const_variable_value(node, "int", "protected") is None


def test_variable_without_initializer_has_no_value() -> None:
    node = {"kind": "VarDecl", "name": "x", "type": {"qualType": "int"}, "inner": []}
    assert non_const_variable_value(node, "int", "public") is None


def test_non_arithmetic_non_const_variable_has_no_value() -> None:
    """A pointer, class, or unknown typedef initializer is a spelling, not a
    number: ``const int *p = &a;`` would otherwise report a rename of ``a``."""
    for type_name in ("int *", "const int *", "std::string", "my_handle_t"):
        assert non_const_variable_value(_var(type_name), type_name, "public") is None, (
            type_name
        )


def test_arithmetic_spellings_with_cv_and_stdint_carry_value() -> None:
    for type_name in ("unsigned long long", "volatile int", "uint32_t", "double"):
        assert non_const_variable_value(_var(type_name), type_name, "public") == "1", (
            type_name
        )


def test_non_inline_non_const_global_has_no_value() -> None:
    """``int simple = 42;`` in a header is not a shared definition: no value,
    the same as the castxml path."""
    assert non_const_variable_value(_var("int", inline=False), "int", "public") is None
