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

"""Synthetic ``compare()`` workloads whose size is one integer.

Shared by the deterministic call-count complexity gate
(``test_compare_call_complexity.py``, unit lane) and the wall-clock scaling
exponents (``test_compare_scaling_shapes.py``, ``slow`` lane), so both
measure the *same* shapes. Each builder returns an ``(old, new)`` pair whose
entity count grows linearly in ``n`` and in which **every** entity changes,
so a run cannot shortcut past the detectors under test. Declarations carry
the v46 surface facts a real header-AST dump records (``_stamp_header_facts``);
``legacy_signature_churn`` keeps the pre-v46 fallback path measured too.

``tag`` salts every symbol/type name. Several helpers on the compare path
keep process-wide caches (demangling, canonical spellings); without a salt a
smaller run made by an earlier test pre-warms a larger one, and a call-count
comparison between the two sizes would measure cache state rather than
algorithmic shape.

The shapes are chosen to reach different detector families:

* ``signature_churn`` -- every function's return and parameter type change
  (``diff_symbols`` signature checks, parameter diffing).
* ``rename_churn`` -- every function removed and a same-shaped one added
  (rename / fingerprint matching, removed-vs-added pairing).
* ``enum_churn`` -- enums whose member values move, used by functions
  (``diff_types`` enum checks, affected-symbol enrichment).
* ``variable_churn`` -- every variable's type changes.
* ``nested_type_churn`` -- a chain of structs embedding each other whose
  innermost member grows (transitive type-reachability propagation).
* ``type_churn`` -- many functions taking changed structs by pointer (the
  functions x types post-processing path ``test_performance.py`` guards).
* ``add_remove`` -- half the functions removed, a quarter added.
"""

from __future__ import annotations

from collections.abc import Callable

from abicheck.extract.surface_fact_producers import header_ast_surface_facts
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)
from abicheck.model.entities import EnumMember, EnumType

Workload = Callable[..., tuple[AbiSnapshot, AbiSnapshot]]


def _fn(
    name: str, mangled: str, ret: str = "int", params: list[Param] | None = None
) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type=ret,
        params=list(params or []),
        visibility=Visibility.PUBLIC,
    )


def _stamp_header_facts(snap: AbiSnapshot) -> AbiSnapshot:
    """Give every function/variable the v46 surface facts a real header-AST
    dump records (``extract.surface_fact_producers.header_ast_surface_facts``,
    exported through the dynamic table), so a workload exercises the path a
    fresh dump takes rather than the legacy ``visibility`` fallback."""
    snap.from_headers = True
    for decl in (*snap.declarations.functions, *snap.declarations.variables):
        for name, value in header_ast_surface_facts(
            exported=True, producer="castxml"
        ).items():
            setattr(decl, name, value)
    return snap


def _pair(
    old: dict, new: dict, *, legacy: bool = False
) -> tuple[AbiSnapshot, AbiSnapshot]:
    pair = (
        AbiSnapshot(library="libshape.so", version="1.0", **old),
        AbiSnapshot(library="libshape.so", version="2.0", **new),
    )
    if legacy:
        return pair
    return _stamp_header_facts(pair[0]), _stamp_header_facts(pair[1])


def signature_churn(n: int, tag: str = "") -> tuple[AbiSnapshot, AbiSnapshot]:
    def name(i: int) -> str:
        return f"{tag}f{i}"

    old = [
        _fn(
            name(i), f"_Z{len(name(i))}{name(i)}i", params=[Param(name="a", type="int")]
        )
        for i in range(n)
    ]
    new = [
        _fn(
            name(i),
            f"_Z{len(name(i))}{name(i)}i",
            ret="long",
            params=[Param(name="a", type="long")],
        )
        for i in range(n)
    ]
    return _pair({"functions": old}, {"functions": new})


def legacy_signature_churn(n: int, tag: str = "") -> tuple[AbiSnapshot, AbiSnapshot]:
    """:func:`signature_churn` without v46 facts: the pre-v46 / hand-built
    declaration path through the legacy ``visibility`` fallback."""
    old, new = signature_churn(n, tag)
    for snap in (old, new):
        snap.from_headers = False
        for decl in snap.declarations.functions:
            decl.declared_in_headers_fact = decl.in_public_contract_fact = (
                decl.binary_exported_fact
            ) = None
    return old, new


def rename_churn(n: int, tag: str = "") -> tuple[AbiSnapshot, AbiSnapshot]:
    def side(stem: str) -> list[Function]:
        out = []
        for i in range(n):
            nm = f"{tag}{stem}_{i}"
            out.append(
                _fn(
                    nm,
                    f"_Z{len(nm)}{nm}P2T{i % 7}",
                    params=[Param(name="a", type=f"T{i % 7} *")],
                )
            )
        return out

    return _pair({"functions": side("old_name")}, {"functions": side("new_name")})


def enum_churn(n: int, tag: str = "") -> tuple[AbiSnapshot, AbiSnapshot]:
    k = max(2, n // 10)

    def enums(shift: int) -> list[EnumType]:
        return [
            EnumType(
                name=f"{tag}E{j}",
                members=[
                    EnumMember(f"{tag}E{j}_{m}", m + shift * (m % 2)) for m in range(20)
                ],
            )
            for j in range(k)
        ]

    funcs = []
    for i in range(n):
        nm, en = f"{tag}g{i}", f"{tag}E{i % k}"
        funcs.append(
            _fn(nm, f"_Z{len(nm)}{nm}{len(en)}{en}", params=[Param(name="e", type=en)])
        )
    return _pair(
        {"functions": funcs, "enums": enums(0)},
        {"functions": list(funcs), "enums": enums(1)},
    )


def variable_churn(n: int, tag: str = "") -> tuple[AbiSnapshot, AbiSnapshot]:
    old = [
        Variable(name=f"{tag}v{i}", mangled=f"{tag}v{i}", type="int") for i in range(n)
    ]
    new = [
        Variable(name=f"{tag}v{i}", mangled=f"{tag}v{i}", type="long") for i in range(n)
    ]
    return _pair({"variables": old}, {"variables": new})


def nested_type_churn(n: int, tag: str = "") -> tuple[AbiSnapshot, AbiSnapshot]:
    """A chain ``S_i { int x; S_{i-1} inner; }`` whose innermost record grows
    by one ``int``: every record embedding it by value grows too, so the
    change propagates through the whole chain (one size finding per link)."""
    k = max(4, n // 4)

    def types(grow: bool) -> list[RecordType]:
        out = []
        inner_bits = 0
        for i in range(k):
            fields = [TypeField(name="x", type="int", offset_bits=0)]
            if i == 0:
                if grow:
                    fields.append(TypeField(name="z", type="int", offset_bits=32))
                size = 64 if grow else 32
            else:
                fields.append(
                    TypeField(name="inner", type=f"{tag}S{i - 1}", offset_bits=32)
                )
                size = 32 + inner_bits
            inner_bits = size
            out.append(
                RecordType(
                    name=f"{tag}S{i}", kind="struct", size_bits=size, fields=fields
                )
            )
        return out

    funcs = []
    for i in range(n):
        nm, st = f"{tag}h{i}", f"{tag}S{i % k}"
        funcs.append(
            _fn(
                nm,
                f"_Z{len(nm)}{nm}P{len(st)}{st}",
                params=[Param(name="p", type=f"{st} *")],
            )
        )
    return _pair(
        {"functions": funcs, "types": types(False)},
        {"functions": list(funcs), "types": types(True)},
    )


def type_churn(n: int, tag: str = "") -> tuple[AbiSnapshot, AbiSnapshot]:
    k = max(2, n // 10)

    def types(grow: bool) -> list[RecordType]:
        out = []
        for i in range(k):
            fields = [
                TypeField(name="a", type="int", offset_bits=0),
                TypeField(name="b", type="int", offset_bits=32),
            ]
            if grow:
                fields.append(TypeField(name="c", type="int", offset_bits=64))
            out.append(
                RecordType(
                    name=f"{tag}Type_{i}",
                    kind="struct",
                    size_bits=96 if grow else 64,
                    fields=fields,
                )
            )
        return out

    funcs = []
    for i in range(n):
        nm, st = f"{tag}use_{i}", f"{tag}Type_{i % k}"
        funcs.append(
            _fn(
                nm,
                f"_Z{len(nm)}{nm}P{len(st)}{st}",
                params=[Param(name="p", type=f"{st} *")],
            )
        )
    return _pair(
        {"functions": funcs, "types": types(False)},
        {"functions": list(funcs), "types": types(True)},
    )


def add_remove(n: int, tag: str = "") -> tuple[AbiSnapshot, AbiSnapshot]:
    def fn(stem: str, i: int) -> Function:
        nm = f"{tag}{stem}_{i}"
        return _fn(nm, f"_Z{len(nm)}{nm}v")

    old = [fn("func", i) for i in range(n)]
    new = [fn("func", i) for i in range(n // 2)] + [
        fn("added", i) for i in range(n // 4)
    ]
    return _pair({"functions": old}, {"functions": new})


WORKLOADS: dict[str, Workload] = {
    "signature_churn": signature_churn,
    "legacy_signature_churn": legacy_signature_churn,
    "rename_churn": rename_churn,
    "enum_churn": enum_churn,
    "variable_churn": variable_churn,
    "nested_type_churn": nested_type_churn,
    "type_churn": type_churn,
    "add_remove": add_remove,
}
