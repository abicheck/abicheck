"""Unit tests for the preprocessor-excluded declaration scan.

The scanner never evaluates a condition: which lines survived, and which
macros were defined, is the compiler's answer. Here that answer is either
derived from a hand-written truth table (:data:`_TRUTH`, the test author's
reading of the preprocessor rules, evaluated in plain Python for each define
set) or, in the ``integration`` cross-check, produced by a real ``g++ -E``.
"""

from __future__ import annotations

import itertools
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from xml.etree.ElementTree import Element

import pytest

from abicheck.extract.headers.castxml.hidden_declarations import (
    attach_hidden_declarations,
    read_hidden_declarations,
)
from abicheck.extract.headers.castxml.preprocessed_text import parse_preprocessed
from abicheck.extract.headers.preprocessor_branches import (
    hidden_declarations,
    hidden_declarations_for_headers,
    required_macros,
    scan_branches,
)

_HEADER = """\
#ifndef API_H
#define API_H
int visible(int);
#ifdef FOO
int foo_a(int);
#  if defined(BAR) && defined(BAZ)
int nested(void);
#  endif
#elif defined(OTHER)
int other(void);
#else
int fallback(void);
#endif
#if !defined(NOPE)
int not_nope(void);
#endif
#ifndef NEG
int neg(void);
#endif
#ifdef WRAP
#  ifdef INNER
int wrapped(void);
#  endif
#endif
#endif
"""

Defines = frozenset[str]

#: Per declaration: (is it compiled under these defines?, which macros the
#: configuration lacks that would enable it). Written from the C
#: preprocessor rules, independently of the scanner.
_TRUTH: dict[str, tuple[Callable[[Defines], bool], Callable[[Defines], set[str]]]] = {
    "visible": (lambda d: True, lambda d: set()),
    "foo_a": (lambda d: "FOO" in d, lambda d: {"FOO"} - d),
    "nested": (
        lambda d: {"FOO", "BAR", "BAZ"} <= d,
        lambda d: {"FOO", "BAR", "BAZ"} - d,
    ),
    # Needs FOO undefined: FOO is never advice, only OTHER is.
    "other": (lambda d: "FOO" not in d and "OTHER" in d, lambda d: {"OTHER"} - d),
    # #else / negated arms: no "pass -D" advice exists for them.
    "fallback": (lambda d: not {"FOO", "OTHER"} & d, lambda d: set()),
    "not_nope": (lambda d: "NOPE" not in d, lambda d: set()),
    "neg": (lambda d: "NEG" not in d, lambda d: set()),
    "wrapped": (lambda d: {"WRAP", "INNER"} <= d, lambda d: {"WRAP", "INNER"} - d),
}
_MACROS = ("FOO", "BAR", "BAZ", "OTHER", "NOPE", "NEG", "WRAP", "INNER")
_ALL_DEFINE_SETS = [
    frozenset(c)
    for n in range(len(_MACROS) + 1)
    for c in itertools.combinations(_MACROS, n)
]


def _line_of(text: str) -> dict[str, int]:
    """Line declaring each name (the test's own index, not the scanner's)."""
    return {
        line.split()[1].split("(")[0]: no
        for no, line in enumerate(text.splitlines(), start=1)
        if line.startswith("int ")
    }


def _expected(defines: Defines) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for name, (compiled, lacking) in _TRUTH.items():
        if compiled(defines):
            continue
        for macro in lacking(defines):
            out.setdefault(macro, set()).add(name)
    return out


def _simulated_present(defines: Defines) -> set[int]:
    """The surviving lines the compiler would report: the include guard's
    ``#define`` (kept by ``-dD``) and every compiled declaration."""
    lines = _line_of(_HEADER)
    return {2} | {lines[n] for n, (compiled, _) in _TRUTH.items() if compiled(defines)}


@pytest.mark.parametrize(
    "defines", _ALL_DEFINE_SETS, ids=lambda d: "+".join(sorted(d)) or "none"
)
def test_hidden_declarations_match_the_truth_table(defines: Defines) -> None:
    got = hidden_declarations(_HEADER, _simulated_present(defines), defines | {"API_H"})
    assert got == _expected(defines)


def test_include_guard_is_transparent() -> None:
    branches = scan_branches(_HEADER)
    assert [b.include_guard for b in branches].count(True) == 1
    assert all("API_H" not in b.guard_macros for b in branches)
    # Even with nothing present, the include guard names nothing.
    assert "API_H" not in hidden_declarations(_HEADER, set())


def test_a_defined_but_false_guard_names_nothing() -> None:
    text = "#if FOO > 2\nint f(void);\n#endif\n"
    assert hidden_declarations(text, set()) == {"FOO": {"f"}}
    assert hidden_declarations(text, set(), {"FOO"}) == {}


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("defined(A) && defined(B)", ("A", "B")),
        ("defined A || defined(B)", ("A", "B")),
        ("!defined(A) && defined(B)", ("B",)),
        ("!(defined(A) && defined(B)) || C", ("C",)),
        ("!!defined(A)", ("A",)),
        ("A > 2 && B", ("A", "B")),
        ("__has_include(<sycl/sycl.hpp>) && defined(X)", ("X",)),
        ('__has_include("cfg.h")', ()),
        ("0", ()),
        ("1 || true", ()),
        ("(A)", ("A",)),
        ("!(A || !B)", ("B",)),
        ("A == 202001L", ("A",)),
    ],
)
def test_required_macros(expr: str, expected: tuple[str, ...]) -> None:
    assert required_macros(expr) == expected


def test_comments_and_continuations_are_ignored() -> None:
    text = (
        "/* #ifdef FAKE\n int fake(void);\n#endif */\n"
        "#if defined(A) || \\\n    __has_include(<x.h>) || 2 > 1L\n"
        "int g(void); // if (x)\n"
        "#endif\n"
    )
    assert hidden_declarations(text, set()) == {"A": {"g"}}


def test_elifdef_and_else_attribution() -> None:
    text = "#ifdef A\nint a(void);\n#elifdef B\nint b(void);\n#else\nint c(void);\n#endif\n"
    # Nothing defined: the #else arm (line 6) survived.
    assert hidden_declarations(text, {6}) == {"A": {"a"}, "B": {"b"}}
    # A defined: B's arm needs A *undefined*, so only B is advice for b.
    assert hidden_declarations(text, {2}, {"A"}) == {"B": {"b"}}


def test_headers_merge_skip_unentered_and_unreadable(tmp_path: Path) -> None:
    a = tmp_path / "a.h"
    a.write_text("#ifdef X\nint a(void);\n#endif\n", encoding="utf-8")
    b = tmp_path / "b.h"
    b.write_text("#ifdef X\nint b(void);\n#endif\n", encoding="utf-8")
    entered = {str(a): set(), str(b): set()}
    assert hidden_declarations_for_headers([a, b], entered) == {"X": ("a", "b")}
    assert hidden_declarations_for_headers([a], {str(a): {2}}) == {}
    # A header the run never entered is unknown, never "all excluded".
    assert hidden_declarations_for_headers([a, b], {str(b): set()}) == {"X": ("b",)}
    assert hidden_declarations_for_headers([tmp_path / "missing.h"], {}) is None


def test_xml_round_trip() -> None:
    root = Element("CastXML")
    assert read_hidden_declarations(root) is None
    attach_hidden_declarations(root, {})
    assert read_hidden_declarations(root) == {}
    attach_hidden_declarations(root, {"FOO": ("a", "b"), "BAR": ()})
    assert read_hidden_declarations(root) == {"FOO": ("a", "b")}


@pytest.mark.integration
@pytest.mark.skipif(not shutil.which("g++"), reason="needs g++")
@pytest.mark.parametrize(
    "defines", _ALL_DEFINE_SETS[::7], ids=lambda d: "+".join(sorted(d)) or "none"
)
def test_against_the_real_preprocessor(tmp_path: Path, defines: Defines) -> None:
    """Cross-check against ``g++ -E -dD``: its surviving lines and defined
    macros, fed to the scanner, give the truth table's answer."""
    header = tmp_path / "api.h"
    header.write_text(_HEADER, encoding="utf-8")
    out = subprocess.run(
        [
            "g++",
            "-E",
            "-dD",
            "-x",
            "c++",
            *(f"-D{d}" for d in sorted(defines)),
            str(header),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    files, defined = parse_preprocessed(out)
    assert defines <= defined
    present = files[os.path.normpath(str(header))]
    assert {n for n, line in _line_of(_HEADER).items() if line in present} == {
        n for n, (compiled, _) in _TRUTH.items() if compiled(defines)
    }
    assert hidden_declarations_for_headers([header], files, defined) == {
        m: tuple(sorted(n)) for m, n in sorted(_expected(defines).items())
    }
