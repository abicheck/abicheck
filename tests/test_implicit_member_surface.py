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

"""Issue #1713: castxml's implicit special members of a record that only one
side declares surfaced as ``FUNC_ADDED``/``INLINE_FUNCTION_REMOVED`` findings
keyed on abicheck's internal ``__abicheck_ctor__`` placeholders.

Invariant: for every record present on exactly one side, no reported finding
-- in the result's changes, its JSON, Markdown or SARIF rendering -- names an
unexported compiler-generated member of that record, in either direction;
while the record's own ``TYPE_ADDED``/``TYPE_REMOVED``, real user-declared
functions, and implicit-member changes on a record surviving on both sides
(case30's shape) are still reported.

The snapshots mirror what a real castxml dump of a C header records for
``typedef struct _b_t { int y; } b_t;`` (checked against castxml 0.7.0):
three synthetic-key constructors, a ``~_b_t`` destructor and two mangled
``operator=``, all ``artificial="1"``, each promoted into the public contract
by the public-header scope pass while no export table confirms it.
"""

from __future__ import annotations

import json

import pytest

from abicheck.checker import compare
from abicheck.compare.implicit_member_surface import implicit_member_owner
from abicheck.model import AbiSnapshot, Fact, RecordType
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.declarations import Function, Param, Visibility
from abicheck.model.synthetic_key import SYNTHETIC_CTOR_KEY_PREFIX
from abicheck.report.dispatch_markdown import to_markdown
from abicheck.reporter import to_json
from abicheck.sarif import to_sarif_str


def _facts(*, exported: bool | None) -> dict[str, object]:
    return {
        "declared_in_headers_fact": Fact.present(True, producer="castxml"),
        "in_public_contract_fact": Fact.present(
            True, "declared in a supplied public header"
        ),
        "binary_exported_fact": (
            Fact.not_collected("no binary in this dump", producer="castxml")
            if exported is None
            else Fact.present(exported, producer="castxml")
        ),
    }


def _implicit_members(record: str, scope: str = "") -> list[Function]:
    """Every implicit special member castxml emits for *record*."""
    q = f"{scope}::{record}" if scope else record
    mangled_scope = (
        "".join(f"{len(p)}{p}" for p in q.split("::"))
        if scope
        else f"{len(record)}{record}"
    )
    ctor = SYNTHETIC_CTOR_KEY_PREFIX + q
    members = [
        (record, f"{ctor}()", None),
        (record, f"{ctor}(const {q}&)", Param(name="", type=f"const {q}&")),
        (record, f"{ctor}({q}&&)", Param(name="", type=f"{q}&&")),
        (f"~{record}", f"~{q}", None),
        ("operator=", f"_ZN{mangled_scope}aSERKS_", Param(name="", type=f"const {q}&")),
        ("operator=", f"_ZN{mangled_scope}aSEOS_", Param(name="", type=f"{q}&&")),
    ]
    return [
        Function(
            name=name,
            mangled=key,
            return_type="",
            params=[p] if p else [],
            visibility=Visibility.HIDDEN,
            is_compiler_generated=True,
            is_inline=True,
            **_facts(exported=None if key.startswith(("__", "~")) else False),  # type: ignore[arg-type]
        )
        for name, key, p in members
    ]


def _user_fn(name: str) -> Function:
    return Function(
        name=name,
        mangled=name,
        return_type="int",
        visibility=Visibility.PUBLIC,
        is_extern_c=True,
        **_facts(exported=True),  # type: ignore[arg-type]
    )


def _record(name: str, scope: str = "") -> RecordType:
    return RecordType(
        name=name,
        kind="struct",
        size_bits=32,
        qualified_name=f"{scope}::{name}" if scope else None,
    )


def _snap(version: str, records: list[tuple[str, str]], fns: list[str]) -> AbiSnapshot:
    functions = [_user_fn(f) for f in fns]
    for name, scope in records:
        functions.extend(_implicit_members(name, scope))
    return AbiSnapshot(
        library="libx.so",
        version=version,
        functions=functions,
        types=[_record(n, s) for n, s in records],
    )


def _implicit_keys(records: list[tuple[str, str]]) -> set[str]:
    return {f.mangled for n, s in records for f in _implicit_members(n, s)}


_SURVIVOR = [("_a_t", "")]
#: Several sibling shapes: a C struct, a second one, and a namespaced class.
_NEW_RECORDS = [
    [("_b_t", "")],
    [("_b_t", ""), ("_c_t", "")],
    [("Widget", "ns")],
]


def _rendered(result: object) -> dict[str, str]:
    return {
        "json": to_json(result),  # type: ignore[arg-type]
        "markdown": to_markdown(result),  # type: ignore[arg-type]
        "sarif": to_sarif_str(result),  # type: ignore[arg-type]
    }


@pytest.mark.parametrize("new_records", _NEW_RECORDS)
@pytest.mark.parametrize("direction", ["added", "removed"])
def test_implicit_members_of_one_sided_record_never_reported(
    new_records: list[tuple[str, str]], direction: str
) -> None:
    small = _snap("1", _SURVIVOR, ["f"])
    big = _snap("2", _SURVIVOR + new_records, ["f", "g"])
    old, new = (small, big) if direction == "added" else (big, small)
    result = compare(old, new)

    orphan_keys = _implicit_keys(new_records)
    reported = {c.symbol for c in result.changes}
    assert not reported & orphan_keys, sorted(reported & orphan_keys)
    assert not any(
        str(c.symbol).startswith(SYNTHETIC_CTOR_KEY_PREFIX) for c in result.changes
    )
    # The record itself and the real function are still reported.
    type_kind = (
        ChangeKind.TYPE_ADDED if direction == "added" else ChangeKind.TYPE_REMOVED
    )
    type_symbols = {c.symbol for c in result.changes if c.kind is type_kind}
    for name, scope in new_records:
        assert (f"{scope}::{name}" if scope else name) in type_symbols or (
            name in type_symbols
        )
    assert "g" in reported

    for fmt, text in _rendered(result).items():
        assert SYNTHETIC_CTOR_KEY_PREFIX not in text, fmt
        for key in orphan_keys:
            assert key not in text, (fmt, key)
    payload = json.loads(_rendered(result)["json"])
    assert all(
        not str(c.get("symbol", "")).startswith(SYNTHETIC_CTOR_KEY_PREFIX)
        for c in payload.get("changes", [])
    )


def test_implicit_member_change_on_surviving_record_is_kept() -> None:
    """case30's shape: the record survives and loses its implicit default
    constructor -- a real source break that must stay reported."""
    old = _snap("1", _SURVIVOR, ["f"])
    new = _snap("2", _SURVIVOR, ["f"])
    lost = f"{SYNTHETIC_CTOR_KEY_PREFIX}_a_t()"
    new.declarations.functions[:] = [
        f for f in new.declarations.functions if f.mangled != lost
    ]
    new.__dict__.pop("function_map", None)
    result = compare(old, new)
    assert lost in {c.symbol for c in result.changes}


def test_exported_compiler_generated_member_of_new_record_is_kept() -> None:
    """An export table that confirms the member makes it a real symbol."""
    old = _snap("1", _SURVIVOR, ["f"])
    new = _snap("2", _SURVIVOR + [("_b_t", "")], ["f"])
    exported = "_ZN4_b_taSERKS_"
    new.declarations.functions[:] = [
        Function(
            name=f.name,
            mangled=f.mangled,
            return_type=f.return_type,
            params=f.params,
            visibility=Visibility.PUBLIC,
            is_compiler_generated=True,
            **_facts(exported=True),  # type: ignore[arg-type]
        )
        if f.mangled == exported
        else f
        for f in new.declarations.functions
    ]
    new.__dict__.pop("function_map", None)
    result = compare(old, new)
    assert exported in {c.symbol for c in result.changes}


@pytest.mark.parametrize(
    ("key", "name", "owner"),
    [
        (f"{SYNTHETIC_CTOR_KEY_PREFIX}ns::W(int)", "W", "ns::W"),
        ("~ns::W", "~W", "ns::W"),
        ("~_b_t", "~_b_t", "_b_t"),
        ("_ZN2ns1WaSERKS0_", "operator=", "ns::W"),
        ("plain_c", "plain_c", None),
    ],
)
def test_implicit_member_owner(key: str, name: str, owner: str | None) -> None:
    f = Function(name=name, mangled=key, return_type="")
    assert implicit_member_owner(key, f) == owner
