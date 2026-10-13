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

"""Branch-level tests for the dump-time header-order witness (ADR-081).

These use a fake ``dump`` that returns snapshot-shaped objects, so every
branch of the witness is exercised without a compiler. The oracle is the
ADR's rule written out per case, not the implementation's own helpers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from abicheck.errors import SnapshotError
from abicheck.extract.binary_format import _FormatHandler
from abicheck.extract.header_order_witness import (
    canonical_header_order,
    extractions_agree,
    has_unique_resolved_paths,
    header_order_proof,
)


@dataclass
class _Rec:
    name: str


@dataclass
class _Decls:
    functions: list = field(default_factory=list)
    variables: list = field(default_factory=list)
    types: list = field(default_factory=list)
    enums: list = field(default_factory=list)
    typedefs: dict = field(default_factory=dict)
    typedefs_qualified: dict = field(default_factory=dict)
    constants: dict = field(default_factory=dict)


def _snap(headers: list[Path], *, funcs=("f",), from_headers=True, contract=True):
    """A fake extraction: its header_sequence is the declared order's names."""
    seq = json.dumps([h.name for h in headers])
    return SimpleNamespace(
        from_headers=from_headers,
        declarations=_Decls(functions=[_Rec(n) for n in funcs]),
        contract=(
            SimpleNamespace(
                profile_fields={"header_sequence": seq}, header_canonical=None
            )
            if contract
            else None
        ),
    )


def _fake_dump(
    *, funcs=("f",), from_headers=True, contract=True, raise_on_sorted=False
):
    calls: list[list[Path]] = []

    def dump(so_path, headers, extra=None, *, dump_manifest=None, marker=None):
        calls.append(list(headers))
        if (
            raise_on_sorted
            and list(headers) == canonical_header_order(headers)
            and (
                len(headers) > 1
                and [h.name for h in headers] != [h.name for h in calls[0]]
            )
        ):
            raise SnapshotError("sorted order does not parse")
        return _snap(
            list(headers), funcs=funcs, from_headers=from_headers, contract=contract
        )

    return dump, calls


def _wrapped(dump):
    return header_order_proof(dump)


def _hdrs(tmp_path: Path, *names: str) -> list[Path]:
    out = []
    for n in names:
        p = tmp_path / n
        p.write_text("// x\n")
        out.append(p)
    return out


def test_a_single_header_is_never_witnessed(tmp_path):
    dump, calls = _fake_dump()
    h = _hdrs(tmp_path, "a.h")
    snap = _wrapped(dump)(Path("lib.so"), h)
    assert len(calls) == 1
    assert snap.contract.header_canonical is None


def test_a_manifest_dump_is_never_witnessed(tmp_path):
    dump, calls = _fake_dump()
    h = _hdrs(tmp_path, "b.h", "a.h")
    snap = _wrapped(dump)(Path("lib.so"), h, dump_manifest=object())
    assert len(calls) == 1
    assert snap.contract.header_canonical is None


def test_an_already_sorted_declared_order_is_its_own_proof(tmp_path):
    """Oracle: sorted declared order needs no second dump and proves itself."""
    dump, calls = _fake_dump()
    h = _hdrs(tmp_path, "a.h", "b.h", "c.h")
    snap = _wrapped(dump)(Path("lib.so"), h)
    assert len(calls) == 1
    assert snap.contract.header_canonical == ["a.h", "b.h", "c.h"]


def test_an_unsorted_order_is_proven_when_the_declarations_agree(tmp_path):
    dump, calls = _fake_dump()
    h = _hdrs(tmp_path, "c.h", "a.h", "b.h")
    snap = _wrapped(dump)(Path("lib.so"), h)
    assert len(calls) == 2
    assert calls[1] == canonical_header_order(h)
    assert snap.contract.header_canonical == ["a.h", "b.h", "c.h"]


def test_disagreeing_declarations_leave_the_order_unproven(tmp_path):
    """Oracle: a different declaration set means the orders are not equivalent."""
    declared = _snap(_hdrs(tmp_path, "c.h", "a.h"), funcs=("f",))
    canonical = _snap(_hdrs(tmp_path, "a.h", "c.h"), funcs=("f", "g"))
    assert not extractions_agree(declared, canonical)
    calls = []

    def dump(so_path, headers, extra=None, *, dump_manifest=None, marker=None):
        calls.append(list(headers))
        return declared if len(calls) == 1 else canonical

    snap = _wrapped(dump)(Path("lib.so"), _hdrs(tmp_path, "c.h", "a.h"))
    assert len(calls) == 2
    assert snap.contract.header_canonical is None


def test_a_sorted_order_that_fails_to_parse_is_unproven_and_not_raised(tmp_path):
    """The declared snapshot is returned; the failing sorted parse is not an error."""
    declared_calls = []

    def dump(so_path, headers, extra=None, *, dump_manifest=None, marker=None):
        declared_calls.append(list(headers))
        if len(declared_calls) == 2:
            raise SnapshotError("sorted parse fails")
        return _snap(list(headers))

    snap = _wrapped(dump)(Path("lib.so"), _hdrs(tmp_path, "c.h", "a.h"))
    assert snap.contract.header_canonical is None
    assert snap.declarations.functions


def test_a_canonical_snapshot_without_a_contract_is_unproven(tmp_path):
    dump, _ = _fake_dump(contract=False)
    snap = _wrapped(dump)(Path("lib.so"), _hdrs(tmp_path, "c.h", "a.h"))
    assert snap.contract is None


def test_a_non_header_dump_is_unproven(tmp_path):
    dump, calls = _fake_dump(from_headers=False)
    snap = _wrapped(dump)(Path("lib.so"), _hdrs(tmp_path, "c.h", "a.h"))
    assert snap.contract.header_canonical is None
    assert len(calls) == 1


def test_a_duplicate_header_set_is_unproven(tmp_path):
    h = _hdrs(tmp_path, "c.h", "a.h")
    dump, calls = _fake_dump()
    snap = _wrapped(dump)(Path("lib.so"), [h[0], h[1], h[0]])
    assert snap.contract.header_canonical is None
    assert len(calls) == 1


def test_an_undecodable_declared_sequence_is_unproven(tmp_path):
    def dump(so_path, headers, extra=None, *, dump_manifest=None, marker=None):
        snap = _snap(list(headers))
        snap.contract.profile_fields["header_sequence"] = "not json"
        return snap

    snap = _wrapped(dump)(Path("lib.so"), _hdrs(tmp_path, "c.h", "a.h"))
    assert snap.contract.header_canonical is None


def test_the_witness_does_not_recurse_into_its_own_inner_dumps(tmp_path):
    """Oracle: exactly one proof pass, so the dump is called two times at most."""
    dump, calls = _fake_dump()
    _wrapped(dump)(Path("lib.so"), _hdrs(tmp_path, "c.h", "a.h", "b.h"))
    assert len(calls) == 2


def test_the_decorator_preserves_the_public_name_and_signature():
    import inspect

    def dump(so_path, headers, extra=None, *, marker=None):
        """Doc."""

    wrapped = _wrapped(dump)
    assert wrapped.__name__ == "dump"
    assert wrapped.__doc__ == "Doc."
    assert list(inspect.signature(wrapped).parameters) == [
        "so_path",
        "headers",
        "extra",
        "marker",
    ]


@pytest.mark.parametrize(
    "names", [["a.h", "b.h"], ["b.h", "a.h"], ["x.h", "y.h", "z.h"]]
)
def test_canonical_order_is_the_name_sorted_order(tmp_path, names):
    """Oracle: sorted() by name, not the helper's own output."""
    headers = _hdrs(tmp_path, *names)
    assert [h.name for h in canonical_header_order(headers)] == sorted(names)


def test_unique_paths_are_detected_through_resolution(tmp_path):
    a, b = _hdrs(tmp_path, "a.h", "b.h")
    assert has_unique_resolved_paths([a, b])
    assert not has_unique_resolved_paths([a, b, a])


def test_format_handler_recognises_exact_and_prefix_magics():
    handler = _FormatHandler(
        name="elf",
        builder=lambda *a, **k: None,
        magics=(b"\x7fELF",),
        magic_prefix=b"MZ",
    )
    assert handler.matches_magic(b"\x7fELF")
    assert handler.matches_magic(b"MZ\x90\x00")
    assert not handler.matches_magic(b"\x00\x00\x00\x00")
    bare = _FormatHandler(name="x", builder=lambda *a, **k: None, magics=(b"ABCD",))
    assert not bare.matches_magic(b"ABCX")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('["a.h", "b.h"]', ["a.h", "b.h"]),
        ("not json", None),
        ('"a.h"', None),
        ('["a.h", 1]', None),
        ('["a.h", "a.h"]', None),
        (None, None),
    ],
)
def test_the_declared_sequence_decodes_only_distinct_string_lists(raw, expected):
    from abicheck.extract.header_order_witness import _decode_sequence

    assert _decode_sequence(raw) == expected


def test_a_nested_dump_inside_a_proof_is_not_witnessed_again(tmp_path):
    """Oracle: while a proof is running, the decorator passes straight through."""
    from abicheck.extract.header_order_witness import _PROVING

    dump, calls = _fake_dump()
    token = _PROVING.set(True)
    try:
        _wrapped(dump)(Path("lib.so"), _hdrs(tmp_path, "c.h", "a.h"))
    finally:
        _PROVING.reset(token)
    assert len(calls) == 1


def test_a_canonical_dump_without_headers_or_contract_leaves_the_order_unproven(
    tmp_path,
):
    calls = []

    def dump(so_path, headers, extra=None, *, dump_manifest=None, marker=None):
        calls.append(list(headers))
        if len(calls) == 1:
            return _snap(list(headers))
        return _snap(list(headers), from_headers=False)

    snap = _wrapped(dump)(Path("lib.so"), _hdrs(tmp_path, "c.h", "a.h"))
    assert len(calls) == 2
    assert snap.contract.header_canonical is None


def test_an_undecodable_canonical_sequence_leaves_the_order_unproven(tmp_path):
    calls = []

    def dump(so_path, headers, extra=None, *, dump_manifest=None, marker=None):
        calls.append(list(headers))
        snap = _snap(list(headers))
        if len(calls) == 2:
            snap.contract.profile_fields["header_sequence"] = "garbage"
        return snap

    snap = _wrapped(dump)(Path("lib.so"), _hdrs(tmp_path, "c.h", "a.h"))
    assert snap.contract.header_canonical is None
