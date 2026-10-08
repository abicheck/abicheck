"""Clang-backend parameter spellings never carry a top-level ``restrict``.

Regression for the GCC/Clang catalog audit (case207): with the clang header
backend, ``void blend(float *restrict dst, ...)`` kept ``restrict`` in the
parameter's type spelling, so adding it read as a parameter *type* change
(``func_params_changed``, BREAKING, "stack/register mismatch") on top of the
real ``param_restrict_changed``; CastXML reports the qualifier only through
``Param.is_restrict``. The invariant: for every pointer spelling and every
top-level qualifier combination, adding top-level ``restrict`` (any of its
three spellings) leaves the normalized spelling equal to the unrestricted
one, while a restrict on a pointee or inside a nested parameter list stays.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.extract.headers.clang.functions import _without_top_level_restrict

_BASES = ["float *", "const float *", "char **", "struct S *", "ns::T<int> *"]
_RESTRICT = ["restrict", "__restrict", "__restrict__"]
_CV = [[], ["const"], ["volatile"], ["const", "volatile"]]


def _spell(base: str, quals: list[str]) -> str:
    # clang's TypePrinter: qualifiers follow the '*' with no space.
    return base + " ".join(quals) if quals else base


@pytest.mark.parametrize(
    ("base", "restrict", "cv", "restrict_first"),
    list(itertools.product(_BASES, _RESTRICT, _CV, [True, False])),
)
def test_top_level_restrict_is_not_part_of_the_type(
    base: str, restrict: str, cv: list[str], restrict_first: bool
) -> None:
    quals = [restrict, *cv] if restrict_first else [*cv, restrict]
    assert _without_top_level_restrict(_spell(base, quals)) == _spell(base, cv)


@pytest.mark.parametrize(
    "spelling",
    [
        "float *restrict *",
        "void (*)(float *restrict)",
        "int",
        "std::vector<float *restrict>",
        "const char &",
    ],
)
def test_non_top_level_restrict_is_kept(spelling: str) -> None:
    assert _without_top_level_restrict(spelling) == spelling
