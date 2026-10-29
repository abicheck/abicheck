"""Which entity owns a calling convention written in a declaration (default lane).

``source_calling_conventions`` reads a declaration's own text. A convention
inside the declarator of a *returned* function pointer belongs to the returned
type (the return:<f> join carries it), not to f; a convention before the
declarator, or after a plain parameter list, belongs to f. Each placement
below states its owner, so a change that moves a convention to the wrong
entity fails here without a toolchain.
"""

from __future__ import annotations

import itertools
import re
import time
from types import SimpleNamespace
from xml.etree.ElementTree import Element

import pytest

from abicheck.extract.headers.castxml.calling_convention import (
    _pointer_return_declarator_start,
    source_calling_conventions,
)


def _owner_set(lines: list[str], line_no: int) -> set[str]:
    """The conventions the heuristic reads for the function ``f`` on *line_no*."""
    ctx = SimpleNamespace(
        id_map={"f1": Element("File", name="x.h")},
        preprocessed=None,
        source_lines_cache={"x.h": lines},
    )
    el = Element("Function", file="f1", line=str(line_no))
    return source_calling_conventions(ctx, el, "f")


@pytest.mark.parametrize(
    ("placement", "lines", "line_no", "owned_by_f"),
    [
        pytest.param(
            "leading attribute before the return type",
            ["__attribute__((ms_abi)) void f(int);"],
            1,
            {"ms_abi"},
            id="leading-f-owns",
        ),
        pytest.param(
            "leading attribute on a pointer-returning function: f's own",
            ["__attribute__((ms_abi)) void (*f(void))(int);"],
            1,
            {"ms_abi"},
            id="leading-pointer-return-f-owns",
        ),
        pytest.param(
            "attribute inside the returned declarator's parentheses",
            ["void (__attribute__((ms_abi)) *f(void))(int);"],
            1,
            set(),
            id="returned-inside-group",
        ),
        pytest.param(
            "different conventions: leading (f) and inside the group (returned)",
            ["__attribute__((sysv_abi)) void (__attribute__((ms_abi)) *f(void))(int);"],
            1,
            {"sysv_abi"},
            id="both-different",
        ),
        pytest.param(
            "trailing attribute after a plain parameter list",
            ["void f(int) __attribute__((ms_abi));"],
            1,
            {"ms_abi"},
            id="plain-trailing-f-owns",
        ),
        pytest.param(
            "plain function, no convention",
            ["void f(int);"],
            1,
            set(),
            id="plain",
        ),
        pytest.param(
            "typedef'd return: the convention is on the typedef, not on f",
            [
                "typedef void (__attribute__((ms_abi)) *cb_t)(int);",
                "cb_t f(void);",
            ],
            2,
            set(),
            id="typedef-return",
        ),
        pytest.param(
            "trailing attribute after a pointer-return parameter list",
            ["void (*f(void))(int) __attribute__((ms_abi));"],
            1,
            set(),
            id="returned-trailing",
        ),
    ],
)
def test_convention_is_owned_by_the_entity_it_is_written_on(
    placement: str, lines: list[str], line_no: int, owned_by_f: set[str]
) -> None:
    assert _owner_set(lines, line_no) == owned_by_f, placement


#: Reference grammar with balanced attribute arguments (one nesting level).
#: The retired lazy ``.*?`` also spanned an unbalanced ``)``, accepting
#: ``(__attribute__((x)))*``, which is not a pointer declarator.
_OLD_POINTER_RETURN = re.compile(
    r"\(\s*(?:__attribute__\s*\(\((?:[^()]|\([^()]*\))*\)\)\s*)*\*\s*$"
)
_PIECES = (
    "(",
    " ",
    "*",
    "__attribute__((ms_abi))",
    "__attribute__((a(b)))",
    ")",
    "void",
    "x",
)


@pytest.mark.parametrize("parts", list(itertools.product(_PIECES, repeat=4)))
def test_pointer_return_scan_matches_reference_regex(parts: tuple[str, ...]) -> None:
    """Exhaustive small-domain equivalence with a balanced reference grammar
    (the retired regex backtracked exponentially, flagged by CodeQL)."""
    text = "".join(parts)
    ref = _OLD_POINTER_RETURN.search(text)
    assert _pointer_return_declarator_start(text) == (ref.start() if ref else None)


def test_pointer_return_scan_is_linear_on_repeated_attributes() -> None:
    text = "(__attribute__((" + "))__attribute__((" * 20000
    start = time.perf_counter()
    assert _pointer_return_declarator_start(text) is None
    assert time.perf_counter() - start < 1.0
