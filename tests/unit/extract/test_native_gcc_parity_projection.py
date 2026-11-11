# SPDX-License-Identifier: Apache-2.0
"""The native GCC projection of the generation-55 parity facts, without GCC.

The rows are the real schema-v2 captures in ``tests/fixtures/native_gcc/``,
with the keys the generation-55 plugin adds (``complete``, ``deprecated``,
``atomic``, ``friend`` rows, a slot's ``mangled``) set the way the plugin
writes them. The end-to-end cells, which build with a real GCC, live in
``tests/test_native_gcc_parity_regressions.py`` (native-gcc CI job); these
pin the projection's reading of each key so the default lane covers it.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from abicheck.extract.headers.backend_protocol import HeaderParseRequest
from abicheck.extract.headers.gcc import backend as gcc_backend
from abicheck.extract.headers.gcc.contracts import clean, vtable_fact
from abicheck.extract.headers.gcc.projection import project
from abicheck.model import Fact, FactStatus

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "native_gcc"


def _header(tmp_path: Path, name: str) -> Path:
    header = tmp_path / ("c.h" if name == "c" else "cpp.h")
    header.write_text((_FIXTURES / header.name).read_text(encoding="utf-8"))
    return header


def _rows(name: str, header: Path) -> list[dict[str, Any]]:
    text = (_FIXTURES / f"rows_{name}.json").read_text(encoding="utf-8")
    return json.loads(text.replace("@HEADER@", str(header)))


def _record(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(r for r in rows if r["kind"] == "record_type" and r["name"] == name)


@pytest.mark.parametrize(
    ("gcc", "expected"),
    [
        ("atomic int", "_Atomic(int)"),
        ("const atomic long", "const _Atomic(long)"),
        ("int[0:]", "int[]"),
        ("const char[<unknown>]", "const char[]"),
        ("struct rec", "rec"),
        ("int[4]", "int[4]"),
        ("atomicity_t", "atomicity_t"),
    ],
)
def test_clean_respells_gcc_types_like_the_other_backends(
    gcc: str, expected: str
) -> None:
    assert clean(gcc) == expected


def _groups(*, mangled: bool) -> list[dict[str, Any]]:
    def slot(method: str, symbol: str) -> dict[str, Any]:
        return {"method": method, **({"mangled": symbol} if mangled else {})}

    return [
        {
            "type": "S",
            "offset_bytes": 0,
            "slots": [slot("S::area()", "_ZN1S4areaEv"), slot("S::~S()", "_ZN1SD1Ev")],
        },
        {"type": "Base2", "offset_bytes": 8, "slots": [slot("x", "_ZN5Base21xEv")]},
    ]


def test_vtable_reads_the_primary_groups_declared_slots() -> None:
    row = {
        "vtable_candidate_groups": _groups(mangled=True),
        # The TU's initializer spells a pure slot __cxa_pure_virtual; the
        # declared slots win over it.
        "vtable_initializers": [
            {
                "name": "_ZTV1S",
                "initializer_kind": "constructor",
                "entries": [{"target": "__cxa_pure_virtual"}],
            }
        ],
    }
    assert vtable_fact(row) == Fact.present(["_ZN1S4areaEv", "_ZN1SD1Ev"])


def test_vtable_without_slot_names_falls_back_to_the_initializer() -> None:
    row = {
        "vtable_candidate_groups": _groups(mangled=False),
        "vtable_initializers": [
            {
                "name": "_ZTV1S",
                "initializer_kind": "constructor",
                "entries": [{"target": "_ZTI1S"}, {"target": "_ZN1S4areaEv"}],
            }
        ],
    }
    assert vtable_fact(row) == Fact.present(["_ZN1S4areaEv"])


def test_vtable_of_a_non_polymorphic_record_is_empty_and_known() -> None:
    assert vtable_fact({"vtable_initializers": []}) == Fact.present([])
    assert vtable_fact({}).status is FactStatus.NOT_COLLECTED


def test_projection_reads_the_parity_keys(tmp_path: Path) -> None:
    header = _header(tmp_path, "cpp")
    rows = _rows("cpp", header)
    base = _record(rows, "n::B")
    base["complete"] = False
    base["deprecated"] = "use D"
    field = next(f for f in base["fields"] if f["name"] == "x")
    field["deprecated"] = ""
    for group in _record(rows, "n::D")["vtable_candidate_groups"]:
        for i, s in enumerate(group["slots"]):
            s["mangled"] = f"_ZslotD{i}"
    free_fn = next(r for r in rows if r.get("name") == "free_fn")
    rows.insert(-1, {"kind": "friend", "uid": free_fn["uid"], "owner": "struct n::D"})
    fields = project(rows, header, language="c++")

    types = {t.name: t for t in fields.types}
    assert types["n::B"].is_opaque is True
    assert types["n::D"].is_opaque is False
    assert types["n::B"].deprecated == "use D"
    assert {f.name: f.deprecated for f in types["n::B"].fields}["x"] == ""
    assert types["n::D"].vtable_fact == Fact.present(
        ["_ZslotD0", "_ZslotD1", "_ZslotD2"]
    )
    assert types["n::D"].vptr_offset_bits_fact == Fact.partial(0)
    fns = {f.name: f for f in fields.functions}
    assert fns["n::free_fn"].hidden_friend_owner == "n::D"
    assert fns["n::free_fn"].is_hidden_friend is True
    assert fns["n::D::s"].is_hidden_friend is False
    assert fns["n::D::s"].hidden_friend_owner_fact.status is FactStatus.NOT_APPLICABLE


def test_c_projection_leaves_hidden_friends_not_collected(tmp_path: Path) -> None:
    header = _header(tmp_path, "c")
    fields = project(_rows("c", header), header, language="c")
    assert fields.functions
    for fn in fields.functions:
        assert fn.is_hidden_friend is None
        assert fn.hidden_friend_owner_fact.status is FactStatus.NOT_COLLECTED


@pytest.mark.parametrize(
    ("text", "lang", "expected"),
    [
        ("int scan(int *restrict p);\n", None, "c"),
        ("namespace n { int f(); }\n", None, "c++"),
        ("int scan(int *p);\n", "c++", "c++"),
        ("namespace n { int f(); }\n", "c", "c"),
    ],
)
def test_language_follows_the_shared_rule_unless_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    text: str,
    lang: str | None,
    expected: str,
) -> None:
    header = tmp_path / "api.h"
    header.write_text(text)
    rows = _rows(
        "c" if expected == "c" else "cpp",
        _header(tmp_path, "c" if expected == "c" else "cpp"),
    )
    seen: list[str] = []
    monkeypatch.setattr(gcc_backend, "resolve_compiler", lambda cc: "/usr/bin/" + cc)

    def plugin(cc: str, language: str) -> Path:
        seen.append(language)
        return tmp_path

    monkeypatch.setattr(gcc_backend, "find_plugin", plugin)
    monkeypatch.setattr(gcc_backend, "acquire", lambda *a, **k: copy.deepcopy(rows))
    request = HeaderParseRequest(headers=[header], extra_includes=[], lang=lang)
    gcc_backend.GccBackend().parse(request)
    assert seen == [expected]
