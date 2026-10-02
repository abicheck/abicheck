"""Differential invariants for the pattern pre-scan's fast blanker.

``_blank_comments_and_strings`` fast-forwards over uniform runs instead of
stepping one character at a time. The oracle here is an independent copy of
the original one-step-per-character loop (it drives the same per-state step
functions, which define the semantics), so any divergence the run-skipping
introduces -- at a state boundary, a line splice, an unterminated literal,
a raw string -- shows up as unequal output. Inputs are an exhaustive
enumeration over a small alphabet of every character the state machine
treats specially, plus seeded random longer texts.
"""

from __future__ import annotations

import itertools
import random

import pytest

from abicheck.buildsource import pattern_facts as pf

_ALPHABET = ["/", "*", '"', "'", "\\", "\n", "\r", "a", "1", "R", "(", ")", " "]


def _oracle(text: str, blank_strings: bool) -> str:
    out: list[str] = []
    i, n = 0, len(text)
    state = "code"
    while i < n:
        if state == "code":
            i, state = pf._blank_scan_code(text, i, out)
        elif state == "line_comment":
            i, state = pf._blank_scan_line_comment(text, i, out)
        elif state == "block_comment":
            i, state = pf._blank_scan_block_comment(text, i, out)
        else:
            i, state = pf._blank_scan_literal(text, i, out, state, blank_strings)
    return "".join(out)


def _oracle_line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


@pytest.mark.parametrize("blank_strings", [True, False])
def test_exhaustive_short_inputs_match_the_per_character_oracle(
    blank_strings: bool,
) -> None:
    checked = 0
    for length in range(0, 5):
        for chars in itertools.product(_ALPHABET, repeat=length):
            text = "".join(chars)
            assert pf._blank_comments_and_strings(text, blank_strings) == _oracle(
                text, blank_strings
            ), repr(text)
            checked += 1
    assert checked > 30000  # vacuity guard: the enumeration really ran


@pytest.mark.parametrize("seed", range(40))
@pytest.mark.parametrize("blank_strings", [True, False])
def test_random_long_inputs_match_the_per_character_oracle(
    seed: int, blank_strings: bool
) -> None:
    rng = random.Random(seed)
    tokens = [*_ALPHABET, "//", "/*", "*/", 'R"x(', ')x"', "1'000", "abc def", "\\\n"]
    text = "".join(rng.choice(tokens) for _ in range(rng.randint(50, 600)))
    assert pf._blank_comments_and_strings(text, blank_strings) == _oracle(
        text, blank_strings
    )


@pytest.mark.parametrize("seed", range(20))
def test_scan_text_line_numbers_match_a_rescan_from_zero(seed: int) -> None:
    rng = random.Random(seed)
    lines = [
        'extern "C" {',
        "template class Foo<int>;",
        "#pragma pack(push, 1)",
        "// template class Hidden<int>;",
        "struct S { int x; };",
        "",
        '__attribute__((visibility("default"))) int f();',
    ]
    text = "\n".join(rng.choice(lines) for _ in range(rng.randint(5, 80)))
    facts = pf.scan_text(text, "x.h")
    blanked = pf._blank_comments_and_strings(text)
    expected = set()
    for rule in pf._RULES:
        hay = (
            pf._blank_comments_and_strings(text, blank_strings=False)
            if rule.scan_strings
            else blanked
        )
        for m in rule.regex.finditer(hay):
            expected.add((rule.kind, _oracle_line(blanked, m.start())))
    got = {(f.kind, f.line) for f in facts if f.kind in {r.kind for r in pf._RULES}}
    assert got == expected


def test_scan_memo_retains_digests_not_text_and_stays_bounded(monkeypatch):
    monkeypatch.setattr(
        pf, "_SCAN_MEMO", type(pf._SCAN_MEMO)("test.pattern_facts.scan", max_entries=3)
    )
    texts = [f"#pragma pack(push, {i})\n" + "x" * 10_000 for i in range(5)]
    for t in texts:
        assert pf._scan_text_memo(t, "h.h") == tuple(pf.scan_text(t, "h.h"))
    assert len(pf._SCAN_MEMO) == 3
    for key in pf._SCAN_MEMO:
        assert all(len(value) < 1000 for _, value in key)  # no source text kept
    # A hit returns the same facts, and an edited file is never served stale.
    assert pf._scan_text_memo(texts[-1], "h.h") == tuple(pf.scan_text(texts[-1], "h.h"))
    edited = texts[-1].replace("pack", "pack ")
    assert pf._scan_text_memo(edited, "h.h") == tuple(pf.scan_text(edited, "h.h"))
