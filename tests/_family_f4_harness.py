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

"""FP/FN corpus and oracle for harness H4 (family F4, "Structure over spelling").

Each :class:`Cell` is an in-process ``(old, new)`` snapshot pair plus the
expectation the *family* invariant imposes, written as raw
``(ChangeKind value, symbol)`` pairs and a verdict band -- never through a
predicate the implementation uses to reach its decision:

* an **FP** cell spells a name the heuristic matches while the structural
  fact vetoes it: the heuristic's finding/demotion must not appear;
* an **FN** cell has the structural fact positive (a real change) under a
  near-miss name: the real finding must still be reported;
* a **control** cell has name and structure agree, proving the heuristic is
  alive (otherwise every FN cell would pass with the heuristic deleted).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass, field

from _family_f1_harness import Side, _enum, _fn, _rec, _snapshot

from abicheck.checker import Verdict, compare
from abicheck.model import AbiSnapshot, ScopeOrigin

_BROKEN = frozenset({Verdict.BREAKING, Verdict.API_BREAK})


@dataclass(frozen=True)
class Cell:
    build: Callable[[], tuple[AbiSnapshot, AbiSnapshot]]
    must: frozenset[tuple[str, str]] = frozenset()
    must_not_kinds: frozenset[str] = frozenset()
    breaking: bool | None = None
    notes: str = field(default="", compare=False)


def _pair(old: Side, new: Side) -> tuple[AbiSnapshot, AbiSnapshot]:
    return _snapshot("1", old), _snapshot("2", new)


def _enum_pair(
    name: str,
    old: tuple[tuple[str, int], ...],
    new: tuple[tuple[str, int], ...],
) -> Callable[[], tuple[AbiSnapshot, AbiSnapshot]]:
    def build() -> tuple[AbiSnapshot, AbiSnapshot]:
        return _pair(
            Side(functions=(_fn("foo"),), enums=(_enum(name, old),)),
            Side(functions=(_fn("foo"),), enums=(_enum(name, new),)),
        )

    return build


def _fn_pair(
    old: tuple[tuple[str, tuple[str, ...]], ...],
    new: tuple[tuple[str, tuple[str, ...]], ...],
) -> Callable[[], tuple[AbiSnapshot, AbiSnapshot]]:
    def side(fs: tuple[tuple[str, tuple[str, ...]], ...]) -> Side:
        return Side(functions=(_fn("foo"), *(_fn(n, "int", p) for n, p in fs)))

    return lambda: _pair(side(old), side(new))


def _record(name: str, fields: tuple[tuple[str, str], ...], **kw: object):
    return dataclasses.replace(_rec(name, fields), qualified_name=name, **kw)


def _reached_by_pointer(qname: str) -> Callable[[], tuple[AbiSnapshot, AbiSnapshot]]:
    """*qname* is reached from an exported public function's signature by
    pointer; its field changes type. Structure says: in the contract."""

    def side(ftype: str) -> Side:
        return Side(
            functions=(_fn("foo"), _fn("use", "void", (f"{qname}*",))),
            types=(_record(qname, (("x", ftype),)),),
        )

    return lambda: _pair(side("int"), side("long"))


def _header_seed(qname: str) -> Callable[[], tuple[AbiSnapshot, AbiSnapshot]]:
    """*qname* is public only by header origin (no signature reaches it)."""

    def side(fields: tuple[tuple[str, str], ...]) -> Side:
        return Side(
            functions=(_fn("foo"),),
            types=(_record(qname, fields, origin=ScopeOrigin.PUBLIC_HEADER),),
        )

    return lambda: _pair(side((("x", "int"),)), side((("x", "long"), ("y", "int"))))


def _near_miss_sentinel(member: str) -> Cell:
    # Last-declared, maximum-valued, but the name is not an end marker: the
    # structural fact alone must not demote a real value change.
    return Cell(
        _enum_pair("E", (("A", 0), (member, 1)), (("A", 0), (member, 5))),
        must=frozenset({("enum_member_value_changed", f"E::{member}")}),
        must_not_kinds=frozenset({"enum_last_member_value_changed"}),
        breaking=True,
    )


_FORMAT_TAG = "dnnl::memory::format_tag"

CELLS: dict[str, Cell] = {
    # -- #1411 enum end-sentinel ------------------------------------------
    "sentinel.fp.mid_list_max_name": Cell(
        _enum_pair(
            "E",
            (("A", 0), ("E_MAX", 1), ("B", 2)),
            (("A", 0), ("E_MAX", 7), ("B", 2)),
        ),
        must=frozenset({("enum_member_value_changed", "E::E_MAX")}),
        must_not_kinds=frozenset({"enum_last_member_value_changed"}),
        breaking=True,
        notes="E_MAX is neither last nor the maximum value: not a sentinel",
    ),
    "sentinel.fn.near_miss_MAXIMUM_SIZE": _near_miss_sentinel("MAXIMUM_SIZE"),
    "sentinel.fn.near_miss_BACKEND": _near_miss_sentinel("BACKEND"),
    "sentinel.fn.near_miss_BLAST": _near_miss_sentinel("BLAST"),
    "sentinel.control.true_sentinel": Cell(
        _enum_pair(
            "E",
            (("A", 0), ("B", 1), ("E_MAX", 2)),
            (("A", 0), ("B", 1), ("C", 2), ("E_MAX", 3)),
        ),
        must=frozenset({("enum_last_member_value_changed", "E::E_MAX")}),
        breaking=False,
    ),
    # -- #1411 `_tag` serialization heuristic -----------------------------
    "tag.fp.format_tag_lookalike": Cell(
        _enum_pair(
            _FORMAT_TAG,
            (("undef", 0), ("abcd", 1), ("abdc", 2), ("format_tag_last", 3)),
            (
                ("undef", 0),
                ("abcd", 1),
                ("acbd", 2),
                ("abdc", 3),
                ("format_tag_last", 4),
            ),
        ),
        must=frozenset({("enum_member_value_changed", f"{_FORMAT_TAG}::abdc")}),
        must_not_kinds=frozenset({"serialization_tag_changed"}),
        notes="a layout-format enum named *_tag is not a persisted tag registry",
    ),
    "tag.fn.untagged_value_swap": Cell(
        _enum_pair("Kinds", (("A", 1), ("B", 2)), (("A", 2), ("B", 1))),
        must=frozenset(
            {
                ("enum_member_value_changed", "Kinds::A"),
                ("enum_member_value_changed", "Kinds::B"),
            }
        ),
        breaking=True,
    ),
    "tag.control.registry_swap": Cell(
        _enum_pair("SerializationTag", (("A", 1), ("B", 2)), (("A", 2), ("B", 1))),
        must=frozenset(
            {
                ("serialization_tag_changed", "SerializationTag::A"),
                ("serialization_tag_changed", "SerializationTag::B"),
            }
        ),
        breaking=True,
    ),
    # -- #1411 experimental-namespace promotion ---------------------------
    "experimental.fp.promotion_signature_mismatch": Cell(
        _fn_pair(
            (("ccl::preview::split", ("int",)),),
            (("ccl::v1::split", ("double",)),),
        ),
        must=frozenset(
            {("experimental_removed_without_replacement", "ccl::preview::split")}
        ),
        breaking=True,
        notes="same leaf, different parameter signature: not a promotion",
    ),
    "experimental.fn.segment_near_miss_suffix": Cell(
        _fn_pair((("ccl::experimentalish::split", ("int",)),), ()),
        must=frozenset({("func_removed", "_Z27ccl::experimentalish::spliti")}),
        must_not_kinds=frozenset(
            {"experimental_removed_without_replacement", "experimental_graduated"}
        ),
        breaking=True,
    ),
    "experimental.fn.segment_near_miss_camel": Cell(
        _fn_pair((("ccl::ExperimentalView::split", ("int",)),), ()),
        must=frozenset({("func_removed", "_Z28ccl::ExperimentalView::spliti")}),
        must_not_kinds=frozenset(
            {"experimental_removed_without_replacement", "experimental_graduated"}
        ),
        breaking=True,
    ),
    "experimental.control.promotion": Cell(
        _fn_pair(
            (("ccl::preview::split", ("int",)),),
            (("ccl::v1::split", ("int",)),),
        ),
        must_not_kinds=frozenset({"experimental_removed_without_replacement"}),
    ),
    # -- #1231 contract membership from name shape ------------------------
    "internal_ns.fp.reached_by_pointer_detail": Cell(
        _reached_by_pointer("ns::detail::Cfg"),
        must=frozenset({("type_field_type_changed", "ns::detail::Cfg")}),
        breaking=True,
        notes="reachable from an exported signature: in contract whatever its namespace",
    ),
    "internal_ns.fp.reached_by_pointer_impl": Cell(
        _reached_by_pointer("ns::impl::Cfg"),
        must=frozenset({("type_field_type_changed", "ns::impl::Cfg")}),
        breaking=True,
    ),
    "internal_ns.fn.segment_near_miss": Cell(
        _header_seed("ns::detailed::H"),
        must=frozenset({("type_field_type_changed", "ns::detailed::H")}),
        breaking=True,
    ),
    "internal_ns.control.header_seed_vetoed": Cell(
        _header_seed("ns::detail::H"),
        breaking=False,
    ),
}

#: The structurally identical twin of each reached-by-pointer cell, spelled
#: in a namespace no convention names: the metamorphic reference.
NEUTRAL_TWIN: dict[str, str] = {
    "internal_ns.fp.reached_by_pointer_detail": "ns::priv::Cfg",
    "internal_ns.fp.reached_by_pointer_impl": "ns::priv::Cfg",
}


def run_cell(cell: Cell) -> tuple[Verdict, frozenset[tuple[str, str]]]:
    old, new = cell.build()
    r = compare(old, new)
    return r.verdict, frozenset((c.kind.value, c.symbol) for c in r.changes)


def oracle_violations(cell_id: str, cell: Cell) -> list[str]:
    """The F4 oracle for one cell; empty when the invariant holds."""
    verdict, found = run_cell(cell)
    out = [f"{cell_id}: missing {k}" for k in sorted(cell.must - found)]
    kinds = {k for k, _ in found}
    out += [f"{cell_id}: unexpected {k}" for k in sorted(cell.must_not_kinds & kinds)]
    if cell.breaking is not None and (verdict in _BROKEN) != cell.breaking:
        out.append(f"{cell_id}: verdict {verdict.value} (breaking={cell.breaking})")
    twin = NEUTRAL_TWIN.get(cell_id)
    if twin is not None:
        twin_verdict, _ = run_cell(Cell(_reached_by_pointer(twin)))
        if twin_verdict != verdict:
            out.append(
                f"{cell_id}: verdict {verdict.value} differs from structurally "
                f"identical {twin} ({twin_verdict.value}) -- decided by the name"
            )
    return out


def _scope_path_for_mutant(item: object) -> tuple[str, ...]:
    """The detector's own scope-path helper, re-exported for the mutant that
    drops the signature check (the mutant keeps everything else intact)."""
    from abicheck.diff_namespaces import _scope_path

    return _scope_path(item)  # type: ignore[arg-type]
