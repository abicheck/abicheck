"""CastXML-extracted functions keep calling conventions CastXML drops.

Regression for the GCC/Clang catalog audit (case64): CastXML 0.7 omits GNU
x86-64 ``ms_abi``/``sysv_abi`` from a function's ``attributes`` and GCC emits
no ``DW_AT_calling_convention``, so a header comparison of GCC artifacts read
``NO_CHANGE`` for a real calling-convention switch.

The invariant is exercised over every convention spelling and placement
(leading GNU attribute, trailing GNU attribute, C++11 ``[[gnu::...]]``,
MSVC keyword) and against the negative controls the extraction must not
confuse with the function's own convention: a callback *parameter* carrying
the attribute, a comment, and the neighbouring declaration.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from xml.etree.ElementTree import Element

import pytest

from abicheck.extract.headers.castxml.calling_convention import (
    calling_conventions_in,
    source_calling_conventions,
)
from abicheck.model.cc_attributes import CC_ATTRIBUTE_BASES


def _ctx(path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        id_map={"f1": Element("File", {"id": "f1", "name": str(path)})},
        source_lines_cache={},
    )


def _el(line: int) -> Element:
    return Element("Function", {"file": "f1", "line": str(line)})


_GNU_CC = sorted(b for b in CC_ATTRIBUTE_BASES if b != "regparm")


@pytest.mark.parametrize("cc", _GNU_CC)
@pytest.mark.parametrize(
    "template",
    [
        "__attribute__(({cc}))\nint target(int a, int b);\n",
        "int target(int a, int b) __attribute__(({cc}));\n",
        "__attribute__((__{cc}__)) int target(int a,\n    int b);\n",
        "[[gnu::{cc}]] int target(int a, int b);\n",
    ],
)
def test_function_convention_is_recovered(
    tmp_path: Path, cc: str, template: str
) -> None:
    hdr = tmp_path / "h.h"
    text = "int before(int);\n" + template.format(cc=cc)
    hdr.write_text(text)
    line = next(i for i, ln in enumerate(text.splitlines(), 1) if "target" in ln)
    assert source_calling_conventions(_ctx(hdr), _el(line), "target") == {cc}


@pytest.mark.parametrize(
    "kw", ["cdecl", "stdcall", "fastcall", "vectorcall", "thiscall"]
)
def test_msvc_keyword_is_recovered(tmp_path: Path, kw: str) -> None:
    hdr = tmp_path / "h.h"
    hdr.write_text(f"int __{kw} target(int a);\n")
    assert source_calling_conventions(_ctx(hdr), _el(1), "target") == {kw}


@pytest.mark.parametrize(
    "text",
    [
        # The attribute belongs to the callback parameter's type.
        "void target(void (__attribute__((ms_abi)) *cb)(int), int x);\n",
        # Only mentioned in a comment.
        "/* __attribute__((ms_abi)) */ int target(int x);\n",
        "int target(int x); // __attribute__((sysv_abi))\n",
        # The previous declaration's attribute.
        "__attribute__((ms_abi)) int other(int);\nint target(int x);\n",
        # No convention at all.
        "int target(int x);\n",
    ],
)
def test_no_false_convention(tmp_path: Path, text: str) -> None:
    hdr = tmp_path / "h.h"
    hdr.write_text(text)
    line = next(i for i, ln in enumerate(text.splitlines(), 1) if "target" in ln)
    assert source_calling_conventions(_ctx(hdr), _el(line), "target") == set()


def test_unreadable_or_unlocated_declaration_yields_nothing(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path / "missing.h")
    assert source_calling_conventions(ctx, _el(1), "target") == set()
    assert source_calling_conventions(ctx, Element("Function", {}), "target") == set()


def test_regparm_keeps_its_argument() -> None:
    assert calling_conventions_in("__attribute__((regparm(3))) int f(int);") == {
        "regparm(3)"
    }


@pytest.mark.parametrize("cc", ["ms_abi", "sysv_abi"])
@pytest.mark.parametrize(
    "decoy",
    [
        # A string literal spelling the name and an open paren (Codex
        # security review, PR #1519): a first-match search stopped there.
        'static const char *k = "target(";',
        "static const char k = '(';  static const char *n = \"target (x)\";",
        # A comment that opens a paren after the name.
        "/* target( */",
        # An expression calling a same-named macro-like token first.
        "enum { E = sizeof(int) }; int target_count(int);",
        # Another, attribute-free declaration of the name earlier on the line.
        "int target(int);",
    ],
)
def test_earlier_decoy_on_the_line_cannot_shadow_the_declaration(
    tmp_path: Path, cc: str, decoy: str
) -> None:
    hdr = tmp_path / "h.h"
    hdr.write_text(f"{decoy} int target(int a) __attribute__(({cc}));\n")
    assert source_calling_conventions(_ctx(hdr), _el(1), "target") == {cc}


def test_attribute_inside_a_string_is_not_a_convention(tmp_path: Path) -> None:
    hdr = tmp_path / "h.h"
    hdr.write_text(
        'int target(const char *s); const char *d = "__attribute__((ms_abi))";\n'
    )
    assert source_calling_conventions(_ctx(hdr), _el(1), "target") == set()


def test_neighbouring_declaration_attribute_is_not_borrowed(tmp_path: Path) -> None:
    hdr = tmp_path / "h.h"
    hdr.write_text("int target(int); int other(int) __attribute__((ms_abi));\n")
    assert source_calling_conventions(_ctx(hdr), _el(1), "target") == set()


@pytest.mark.parametrize(
    "text",
    [
        # Name never followed by a parameter list on the reported line.
        "int target;\n",
        # Unterminated parameter list: nothing past it can be attributed.
        "int target(int a,\n",
    ],
)
def test_malformed_or_non_function_line_yields_nothing(
    tmp_path: Path, text: str
) -> None:
    hdr = tmp_path / "h.h"
    hdr.write_text(text)
    assert source_calling_conventions(_ctx(hdr), _el(1), "target") == set()


def test_location_edge_cases_yield_nothing(tmp_path: Path) -> None:
    hdr = tmp_path / "h.h"
    hdr.write_text("int target(int) __attribute__((ms_abi));\n")
    ctx = _ctx(hdr)
    # Line past the end of the file, line 0, a non-numeric line, no name.
    for line in ("9", "0", "x"):
        el = Element("Function", {"file": "f1", "line": line})
        assert source_calling_conventions(ctx, el, "target") == set()
    assert source_calling_conventions(ctx, _el(1), "") == set()
    # Unknown file id, and a File element without a name.
    assert (
        source_calling_conventions(
            ctx, Element("Function", {"file": "f9", "line": "1"}), "target"
        )
        == set()
    )
    ctx.id_map["f2"] = Element("File", {"id": "f2"})
    assert (
        source_calling_conventions(
            ctx, Element("Function", {"file": "f2", "line": "1"}), "target"
        )
        == set()
    )
    # A non-UTF-8 file is unreadable, not a crash.
    bad = tmp_path / "bad.h"
    bad.write_bytes(b"\xff\xfe int target(int);\n")
    assert source_calling_conventions(_ctx(bad), _el(1), "target") == set()


def test_cached_lines_are_reused(tmp_path: Path) -> None:
    hdr = tmp_path / "h.h"
    hdr.write_text("int target(int) __attribute__((ms_abi));\n")
    ctx = _ctx(hdr)
    assert source_calling_conventions(ctx, _el(1), "target") == {"ms_abi"}
    hdr.unlink()  # a second lookup must not re-read the file
    assert source_calling_conventions(ctx, _el(1), "target") == {"ms_abi"}


def test_castxml_reported_convention_is_not_second_guessed(tmp_path: Path) -> None:
    from abicheck.extract.headers.castxml.functions import (
        _contract_attributes_with_source_cc,
    )

    hdr = tmp_path / "h.h"
    hdr.write_text("int __stdcall target(int) __attribute__((ms_abi));\n")
    ctx = _ctx(hdr)
    # CastXML reported __stdcall__: kept as is, the source is not read.
    el = Element("Function", {"file": "f1", "line": "1", "attributes": "__stdcall__"})
    reported = _contract_attributes_with_source_cc(ctx, el, "target")
    assert "ms_abi" not in reported and any("stdcall" in a for a in reported)
    # CastXML reported nothing: the declaration's own convention is added.
    el = Element("Function", {"file": "f1", "line": "1", "attributes": ""})
    assert "ms_abi" in _contract_attributes_with_source_cc(ctx, el, "target")
    # Nothing in the source either: attributes unchanged.
    plain = tmp_path / "p.h"
    plain.write_text("int target(int);\n")
    assert _contract_attributes_with_source_cc(_ctx(plain), el, "target") == []
