"""clang's ``MacroQualifiedType`` spelling never leaks into a return type.

Regression for the 2026-10-08 re-audit: ``__attribute__((ms_abi)) int f()``
respelled as ``CALL int f()`` (same expansion) read as
``func_return_changed`` ``int`` -> ``CALL int``. The oracle is clang's own
``desugaredQualType`` (real spellings captured from clang 18/20 JSON
dumps): a leading word clang printed only because of macro sugar goes; a
genuine type name, keyword or qualified name stays.
"""

from __future__ import annotations

import pytest

from abicheck.extract.headers.clang.macro_qualifier import (
    node_qualtype_without_macro_qualifiers,
    strip_macro_qualifiers,
)
from abicheck.extract.headers.clang.return_type import return_type

_TAIL = " __attribute__((ms_abi))"


@pytest.mark.parametrize(
    ("qual", "desugared", "ret"),
    [
        (f"CALL int (int, int){_TAIL}", f"int (int, int){_TAIL}", "int"),
        (f"API CALL int (int){_TAIL}", f"int (int){_TAIL}", "int"),
        (f"CALL size_t (int){_TAIL}", f"unsigned long (int){_TAIL}", "size_t"),
        (
            f"CALL const char *(void){_TAIL}",
            f"const char *(void){_TAIL}",
            "const char *",
        ),
        (f"CALL struct S (void){_TAIL}", f"struct S (void){_TAIL}", "struct S"),
        # No macro sugar: a typedef'd or qualified return type is kept.
        ("size_t (int)", "unsigned long (int)", "size_t"),
        ("ns::T (int)", "ns::Impl (int)", "ns::T"),
        ("unsigned int (int)", None, "unsigned int"),
        ("const Widget &(void)", "const ns::Widget &(void)", "const Widget &"),
    ],
)
def test_return_type_ignores_macro_sugar(
    qual: str, desugared: str | None, ret: str
) -> None:
    assert return_type(strip_macro_qualifiers(qual, desugared)) == ret


def test_node_reader() -> None:
    node = {
        "type": {
            "qualType": f"CALL int (int){_TAIL}",
            "desugaredQualType": f"int (int){_TAIL}",
        }
    }
    assert node_qualtype_without_macro_qualifiers(node) == f"int (int){_TAIL}"
    plain = {"type": {"qualType": "int (int)"}}
    assert node_qualtype_without_macro_qualifiers(plain) == "int (int)"
