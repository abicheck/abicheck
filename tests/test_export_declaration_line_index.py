"""``textual_declaration_hint``'s identifier line index is lossless.

Bug class: the hint scanned every line of every header naming the export's
leaf, once per export, with a freshly compiled regex -- quadratic in
exports x header lines. ``compare --no-baseline libmkl_rt.so -H mkl_cblas.h``
(~40k exports, ~100k header lines) ran past ten minutes. The fix scans only
the lines the per-header identifier index names, which is correct only if
every line ``leaf_re`` can match is indexed under that leaf.

The oracle is a brute-force scan written here from the docstring's rule, not
the implementation's own helper, over randomly generated header text whose
tokens sit next to digits, underscores, punctuation and conditionals.
"""

from __future__ import annotations

import random
import re
from pathlib import Path

import pytest

from abicheck.buildsource.export_declaration_evidence import (
    ExportDeclarationEvidence,
    _conditioned_lines,
    _HeaderText,
    _identifier_line_index,
    textual_declaration_hint,
)

_NAMES = ("foo", "foo_", "Foo", "foo1", "bar", "ns", "x")
_GLUE = (" ", "", "(", ")", ";", "=", ",", "[", "::", "1", "_", "\t", "*")


def _random_header(rng: random.Random) -> str:
    out = []
    for _ in range(rng.randint(1, 25)):
        if rng.random() < 0.15:
            out.append(rng.choice(("#ifdef COND", "#else", "#endif", "#if X > 1")))
            continue
        out.append(
            "".join(
                rng.choice(_NAMES) + rng.choice(_GLUE) for _ in range(rng.randint(1, 6))
            )
        )
    return "\n".join(out) + "\n"


def _brute_force(symbol: str, headers: list[_HeaderText]) -> tuple | None:
    """The documented rule, scanning every line of every header."""
    leaf_re = re.compile(rf"\b{re.escape(symbol)}\s*(\(|\[|;|=|,)")
    conditional = None
    for h in headers:
        for line, cond in h.lines:
            if not leaf_re.search(line):
                continue
            if h.excluded:
                return ("excluded_header", h.path, cond)
            if cond is not None and conditional is None:
                conditional = ("conditional", h.path, cond)
    return conditional


def _header(path: str, text: str, excluded: bool) -> _HeaderText:
    lines = _conditioned_lines(text)
    index = _identifier_line_index(lines)
    return _HeaderText(path, excluded, lines, frozenset(index), index)


@pytest.mark.parametrize("seed", range(300))
def test_indexed_hint_matches_full_scan(seed: int) -> None:
    rng = random.Random(seed)
    headers = [
        _header(f"/inc/h{i}.h", _random_header(rng), rng.random() < 0.3)
        for i in range(rng.randint(1, 4))
    ]
    ev = ExportDeclarationEvidence(headers=tuple(headers))
    for name in _NAMES:
        hint = textual_declaration_hint(name, ev)
        got = None if hint is None else (hint.reason, hint.header, hint.guard)
        assert got == _brute_force(name, headers), (name, seed)


def test_index_maps_each_identifier_to_its_lines(tmp_path: Path) -> None:
    lines = _conditioned_lines("int foo(int);\nint bar;\n/* foo */\nfoo1 = foo;\n")
    index = _identifier_line_index(lines)
    assert index["foo"] == (0, 3)  # comment stripped, repeated token counted once
    assert index["bar"] == (1,)
    assert index["foo1"] == (3,)
