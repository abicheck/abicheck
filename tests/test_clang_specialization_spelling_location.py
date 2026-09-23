"""A clang specialization's spelled template arguments must not carry the
checkout directory of a lambda/anonymous-tag argument: two extractions of
identical headers under different roots have to spell members' owners the
same way, or every lambda-parameterised instantiation reads as removed+added.
"""

from __future__ import annotations

import pytest

from abicheck.extract.headers.clang.templates import _specialization_spelling


def _spec(*qual_types: str) -> dict:
    return {
        "kind": "ClassTemplateSpecializationDecl",
        "inner": [
            {"kind": "TemplateArgument", "type": {"qualType": q}} for q in qual_types
        ],
    }


@pytest.mark.parametrize(
    "arg",
    [
        "(lambda at {r}/include/eve/detail/kumi.hpp:1138:31)",
        "(unnamed struct at {r}/a.h:5:6)",
        "const (lambda at {r}/b.hpp:1:2) &",
    ],
)
@pytest.mark.parametrize(
    "roots", [("/w/old", "/w/new"), ("/tmp/x", "/opt/build/deep/tree")]
)
def test_spelling_is_independent_of_checkout_root(
    arg: str, roots: tuple[str, str]
) -> None:
    old, new = (
        _specialization_spelling(
            _spec(arg.format(r=r), "const unsigned char &"), "foldable", None
        )
        for r in roots
    )
    assert old == new
    assert old is not None and roots[0] not in old


def test_distinct_lambdas_stay_distinct() -> None:
    a = _specialization_spelling(_spec("(lambda at /r/h.hpp:1:2)"), "f", None)
    b = _specialization_spelling(_spec("(lambda at /r/h.hpp:3:4)"), "f", None)
    assert a != b
