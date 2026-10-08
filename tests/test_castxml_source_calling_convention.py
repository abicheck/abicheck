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
