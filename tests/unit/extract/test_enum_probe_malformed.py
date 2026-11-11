# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""The Clang enum-representation probe never turns malformed AST evidence
into a representation (ADR-028 D3: failed evidence is explicit, not a guess).

The probe parses ``-ast-dump=json`` output for ``__abicheck_enum_representation_N``
enumerators. A well-formed answer maps the enum's AST id to the confirmed
integer type; any malformed shape -- ``inner`` that is not a list, a child that
is not an object, a top-level record that is not an object -- must yield *no*
answer for any enum (the parser then records ``?``, never an assumed ``int``),
and must not escape as an uncaught ``AttributeError``/``TypeError``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from abicheck.extract.headers.backend_protocol import HeaderParseRequest
from abicheck.extract.headers.clang import enum_probe
from abicheck.extract.headers.clang.context import _Decl

_TYPES = enum_probe._TYPES


def _wrap(index: int, inner: Any) -> dict[str, Any]:
    return {
        "kind": "EnumConstantDecl",
        "name": f"{enum_probe._PREFIX}{index}",
        "inner": inner,
    }


def _answer(value: int) -> list[dict[str, Any]]:
    return [{"kind": "ConstantExpr", "value": str(value), "inner": []}]


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dump: str) -> dict[str, str]:
    monkeypatch.setattr(
        enum_probe, "_resolve_clang_system_includes", lambda *a, **k: []
    )
    monkeypatch.setattr(
        enum_probe, "_build_clang_header_command", lambda *a, **k: ["clang"]
    )
    header = tmp_path / "e.h"
    header.write_text("enum A { a0 }; enum B { b0 };\n", encoding="utf-8")
    enums = [
        _Decl({"id": "0xA", "name": "A"}, (), str(header), "public"),
        _Decl({"id": "0xB", "name": "B"}, (), str(header), "public"),
    ]

    def run_ast(cmd: list[str], *, timeout: int, on_created: Any) -> None:
        out = tmp_path / "ast.json"
        out.write_text(dump, encoding="utf-8")
        on_created(out)

    request = HeaderParseRequest(headers=[header], extra_includes=[])
    return enum_probe.probe_enum_types(
        request, enums, clang_bin="clang", force_cpp=False, run_ast=run_ast
    )


@pytest.mark.parametrize("a", range(1, len(_TYPES) + 1))
def test_well_formed_answers_map_each_enum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, a: int
) -> None:
    b = len(_TYPES) + 1 - a
    dump = json.dumps(_wrap(0, _answer(a))) + "\n" + json.dumps(_wrap(1, _answer(b)))
    assert _run(tmp_path, monkeypatch, dump) == {
        "0xA": _TYPES[a - 1],
        "0xB": _TYPES[b - 1],
    }


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(_wrap(1, "not-a-list"), id="inner-string"),
        pytest.param(
            _wrap(1, {"kind": "ConstantExpr", "value": "5"}), id="inner-mapping"
        ),
        pytest.param(_wrap(1, ["child-string"]), id="child-not-object"),
        pytest.param(_wrap(1, [7]), id="child-number"),
        pytest.param(["top-level-list"], id="record-not-object"),
    ],
)
def test_malformed_evidence_yields_no_representation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: Any
) -> None:
    # Enum A's answer is well formed, but the batch is one dump: malformed
    # evidence anywhere declines the whole probe rather than trusting part.
    dump = json.dumps(_wrap(0, _answer(5))) + "\n" + json.dumps(bad)
    assert _run(tmp_path, monkeypatch, dump) == {}


@pytest.mark.parametrize("value", [0, len(_TYPES) + 1, -1])
def test_out_of_range_answer_is_not_a_representation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: int
) -> None:
    dump = json.dumps(_wrap(0, _answer(value)))
    assert _run(tmp_path, monkeypatch, dump) == {}
