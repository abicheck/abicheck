# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""`textual_declaration_hint` compiles a pattern only for a leaf a header names.

Profiling a 28-member MKL release comparison found ~23k undeclared exports
per side each compiling a fresh regex before the per-header identifier
filter, cycling ``re``'s 512-entry cache: 95% of the function's time, while
the search the pattern exists for was ~3%. The invariant is a bound plus an
equivalence: the compile count tracks the distinct leaves some header names
(not the number of symbols queried), and the answer equals an independent
character-scan oracle on generated headers.
"""

from __future__ import annotations

import random
import re

import pytest

from abicheck.buildsource import export_declaration_evidence as ede
from abicheck.buildsource.export_declaration_evidence import (
    DeclarationHint,
    ExportDeclarationEvidence,
    _HeaderText,
    _identifier_line_index,
    textual_declaration_hint,
)

_IDENT_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
_LEAVES = [f"fn{i}" for i in range(12)] + ["fn1x", "x_fn1"]
_TAILS = ["(", "[", ";", "=", ",", " (", "\t;", ")", " x", ""]
_CONDS = [None, None, "defined(FOO)", "BAR > 1"]


def _declares(line: str, leaf: str) -> bool:
    """Oracle: *leaf* as a whole word, then optional spaces, then ``([;=,``."""
    start = 0
    while (i := line.find(leaf, start)) != -1:
        start = i + 1
        if i > 0 and line[i - 1] in _IDENT_CHARS:
            continue
        j = i + len(leaf)
        if j < len(line) and line[j] in _IDENT_CHARS:
            continue
        while j < len(line) and line[j].isspace():
            j += 1
        if j < len(line) and line[j] in "([;=,":
            return True
    return False


def _oracle(leaf: str, headers: tuple[_HeaderText, ...]) -> DeclarationHint | None:
    conditional = None
    for h in headers:
        if leaf not in h.identifiers:
            continue
        for line, cond in h.lines:
            if not _declares(line, leaf):
                continue
            if h.excluded:
                return DeclarationHint("excluded_header", h.path, cond)
            if cond is not None and conditional is None:
                conditional = DeclarationHint("conditional", h.path, cond)
    return conditional


def _header(
    path: str, excluded: bool, lines: list[tuple[str, str | None]]
) -> _HeaderText:
    index = _identifier_line_index(lines)
    return _HeaderText(path, excluded, lines, frozenset(index), index)


def _headers(rng: random.Random) -> tuple[_HeaderText, ...]:
    out = []
    for n in range(rng.randint(1, 4)):
        lines = []
        for _ in range(rng.randint(0, 6)):
            leaf = rng.choice(_LEAVES)
            lines.append((f"int {leaf}{rng.choice(_TAILS)}", rng.choice(_CONDS)))
        out.append(_header(f"h{n}.h", rng.random() < 0.3, lines))
    return tuple(out)


@pytest.mark.parametrize("seed", range(200))
def test_hint_equals_character_scan_oracle(seed: int) -> None:
    rng = random.Random(seed)
    ev = ExportDeclarationEvidence(headers=_headers(rng))
    for leaf in _LEAVES:
        assert textual_declaration_hint(leaf, ev) == _oracle(leaf, ev.headers), leaf


@pytest.mark.parametrize("n_symbols", [16, 256, 2048])
def test_compiles_only_for_leaves_a_header_names(
    n_symbols: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    named = [f"named{i}" for i in range(5)]
    header = _header("a.h", True, [(f"void {n}(int);", None) for n in named])
    ev = ExportDeclarationEvidence(headers=(header,))
    calls: list[str] = []
    real = re.compile

    def counting(pattern, *a, **kw):  # type: ignore[no-untyped-def]
        calls.append(pattern)
        return real(pattern, *a, **kw)

    ede._leaf_declaration_re.cache_clear()
    monkeypatch.setattr(ede.re, "compile", counting)
    symbols = [f"undeclared_{i}" for i in range(n_symbols)] + named * 3
    hints = [textual_declaration_hint(s, ev) for s in symbols]
    assert len(calls) == len(named)  # independent of n_symbols and repeats
    assert hints[:n_symbols] == [None] * n_symbols
    assert all(
        h == DeclarationHint("excluded_header", "a.h") for h in hints[n_symbols:]
    )
