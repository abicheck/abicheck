# Copyright 2026 Nikolay Petrov
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

"""ISSUE-15: DWARF-only internal-namespace churn unreachable from public API.

Real case (conda-forge validation campaign): oneTBB 2021.5 -> 2021.9
``libtbbmalloc`` / ``libtbbmalloc_proxy`` reported BREAKING from private
``tbb::detail::*`` / ``rml::internal::*`` DWARF type churn, while libabigail
exits 0 and there are no removed dynamic exports. Such internal-namespace
layout churn that is NOT reachable from any public API root is truly private
and must not drive a hard ABI verdict — but it must remain auditable (recorded
in the out-of-surface ledger), and a genuine leak through the public API must
still break.
"""

from __future__ import annotations

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.dwarf_metadata import DwarfMetadata, FieldInfo, StructLayout
from abicheck.model import AbiSnapshot, Function, RecordType, TypeField, Visibility
from abicheck.surface import REASON_PRIVATE_INTERNAL_UNREACHABLE


def _dwarf_snap(
    version: str, structs: dict[str, StructLayout], funcs=None
) -> AbiSnapshot:
    s = AbiSnapshot(library="libtbbmalloc.so.2", version=version, functions=funcs or [])
    s.dwarf = DwarfMetadata(has_dwarf=True, structs=structs)  # type: ignore[attr-defined]
    return s


def _layout(name: str, size: int, fields: list[FieldInfo]) -> StructLayout:
    return StructLayout(name=name, byte_size=size, fields=fields)


def test_unreachable_internal_namespace_churn_is_demoted():
    # tbb::detail::* layout churn, DWARF-only, not referenced by any public API.
    old = _dwarf_snap(
        "1",
        {
            "tbb::detail::d0::atomic_backoff": _layout(
                "tbb::detail::d0::atomic_backoff", 8, [FieldInfo("count", "int", 0, 4)]
            ),
            "rml::internal::Block": _layout(
                "rml::internal::Block", 16, [FieldInfo("next", "void *", 0, 8)]
            ),
        },
    )
    new = _dwarf_snap(
        "2",
        {
            "tbb::detail::d0::atomic_backoff": _layout(
                "tbb::detail::d0::atomic_backoff",
                16,
                [FieldInfo("count", "long", 0, 8)],
            ),
            "rml::internal::Block": _layout(
                "rml::internal::Block",
                24,
                [FieldInfo("next", "void *", 0, 8), FieldInfo("pad", "long", 8, 8)],
            ),
        },
    )
    r = compare(old, new)
    assert r.verdict in (Verdict.NO_CHANGE, Verdict.COMPATIBLE), r.verdict
    # The churn is not silently dropped — it lands in the audit ledger.
    assert r.out_of_surface_count >= 1
    # No hard structural finding survives into the verdict-driving change list.
    hard = {
        ChangeKind.STRUCT_SIZE_CHANGED,
        ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
        ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
        ChangeKind.TYPE_SIZE_CHANGED,
    }
    assert not (hard & {c.kind for c in r.changes})


def test_demotion_records_the_ledger_reason():
    old = _dwarf_snap(
        "1",
        {
            "tbb::detail::Foo": _layout(
                "tbb::detail::Foo", 8, [FieldInfo("a", "int", 0, 4)]
            ),
        },
    )
    new = _dwarf_snap(
        "2",
        {
            "tbb::detail::Foo": _layout(
                "tbb::detail::Foo", 16, [FieldInfo("a", "long", 0, 8)]
            ),
        },
    )
    r = compare(old, new)
    reasons = {c.surface_exclusion_reason for c in r.out_of_surface_changes}
    assert REASON_PRIVATE_INTERNAL_UNREACHABLE in reasons


def test_public_struct_layout_change_still_breaks():
    # Negative control: a non-internal (public) struct layout change is breaking.
    old = _dwarf_snap(
        "1", {"PublicCfg": _layout("PublicCfg", 8, [FieldInfo("a", "int", 0, 4)])}
    )
    new = _dwarf_snap(
        "2", {"PublicCfg": _layout("PublicCfg", 16, [FieldInfo("a", "long", 0, 8)])}
    )
    r = compare(old, new)
    assert r.verdict == Verdict.BREAKING


def test_frozen_namespace_churn_is_not_demoted():
    # A contractually frozen namespace is an explicit user declaration that
    # changes there must not be downgraded — even when unreachable. The
    # demotion step must defer to it so the frozen-namespace policy can act.
    from abicheck.policy_file import PolicyFile

    old = AbiSnapshot(library="lib.so.1", version="1")
    new = AbiSnapshot(library="lib.so.1", version="2")
    old.dwarf = DwarfMetadata(
        has_dwarf=True,
        structs={  # type: ignore[attr-defined]
            "ns::detail::r1::Impl": _layout(
                "ns::detail::r1::Impl", 8, [FieldInfo("a", "int", 0, 4)]
            ),
        },
    )
    new.dwarf = DwarfMetadata(
        has_dwarf=True,
        structs={  # type: ignore[attr-defined]
            "ns::detail::r1::Impl": _layout(
                "ns::detail::r1::Impl", 16, [FieldInfo("a", "long", 0, 8)]
            ),
        },
    )
    pf = PolicyFile(base_policy="strict_abi", frozen_namespaces=["**::detail::r1::*"])
    r = compare(old, new, policy_file=pf)
    # The change is kept (not demoted to the ledger) and drives a verdict.
    assert r.verdict == Verdict.BREAKING
    assert any("detail::r1" in (c.symbol or "") for c in r.changes)


def _frozen_dwarf_pair(type_name: str):
    old = AbiSnapshot(library="lib.so.1", version="1")
    new = AbiSnapshot(library="lib.so.1", version="2")
    old.dwarf = DwarfMetadata(
        has_dwarf=True,
        structs={  # type: ignore[attr-defined]
            type_name: _layout(type_name, 8, [FieldInfo("a", "int", 0, 4)]),
        },
    )
    new.dwarf = DwarfMetadata(
        has_dwarf=True,
        structs={  # type: ignore[attr-defined]
            type_name: _layout(type_name, 16, [FieldInfo("a", "long", 0, 8)]),
        },
    )
    return old, new


def test_frozen_namespace_matches_via_ancestor_prefix():
    # The pattern names the namespace, not the leaf type, so it only matches
    # after walking up the ``::`` prefixes of ``ns::detail::r1::Impl``.
    from abicheck.policy_file import PolicyFile

    old, new = _frozen_dwarf_pair("ns::detail::r1::Impl")
    pf = PolicyFile(base_policy="strict_abi", frozen_namespaces=["**::detail::r1"])
    r = compare(old, new, policy_file=pf)
    assert r.verdict == Verdict.BREAKING


def test_non_matching_frozen_namespace_still_demotes():
    # A frozen namespace that does not match the internal type must not prevent
    # demotion (exercises the full prefix walk that ends without a match).
    from abicheck.policy_file import PolicyFile

    old, new = _frozen_dwarf_pair("ns::detail::Impl")
    pf = PolicyFile(
        base_policy="strict_abi", frozen_namespaces=["**::other::frozen::*"]
    )
    r = compare(old, new, policy_file=pf)
    assert r.verdict in (Verdict.NO_CHANGE, Verdict.COMPATIBLE)
    assert r.out_of_surface_count >= 1


def test_reachable_internal_type_still_leaks_and_breaks():
    # When the internal type IS reachable from the public API (embedded by value
    # in a public struct returned by a public function), the leak detector keeps
    # it in-surface and the change still drives a hard verdict — the anti-hiding
    # protection must be preserved.
    pub_fn = Function(
        name="get_widget",
        mangled="get_widget",
        return_type="Widget *",
        params=[],
        visibility=Visibility.PUBLIC,
    )
    old = AbiSnapshot(
        library="lib.so.1",
        version="1",
        functions=[pub_fn],
        types=[
            RecordType(
                name="Widget",
                kind="struct",
                size_bits=64,
                fields=[TypeField(name="impl", type="ns::detail::Impl", offset_bits=0)],
            ),
            RecordType(
                name="ns::detail::Impl",
                kind="struct",
                size_bits=64,
                fields=[TypeField(name="x", type="int", offset_bits=0)],
            ),
        ],
    )
    new = AbiSnapshot(
        library="lib.so.1",
        version="2",
        functions=[pub_fn],
        types=[
            RecordType(
                name="Widget",
                kind="struct",
                size_bits=128,
                fields=[TypeField(name="impl", type="ns::detail::Impl", offset_bits=0)],
            ),
            RecordType(
                name="ns::detail::Impl",
                kind="struct",
                size_bits=128,
                fields=[TypeField(name="x", type="long long", offset_bits=0)],
            ),
        ],
    )
    r = compare(old, new)
    assert r.verdict == Verdict.BREAKING


def test_unreachable_internal_layout_descriptor_finding_is_demoted():
    # A private ns::detail::Impl that only acquires a fine-grained layout finding
    # (TRIVIALLY_COPYABLE_LOST), unreachable from any public API, must take the
    # same internal-leak demotion path as the coarse type kinds — not survive in
    # the verdict-driving change list (Codex review #345).
    old = AbiSnapshot(
        library="lib.so.1",
        version="1",
        types=[
            RecordType(
                name="ns::detail::Impl",
                kind="struct",
                size_bits=64,
                is_trivially_copyable=True,
            )
        ],
    )
    new = AbiSnapshot(
        library="lib.so.1",
        version="2",
        types=[
            RecordType(
                name="ns::detail::Impl",
                kind="struct",
                size_bits=64,
                is_trivially_copyable=False,
            )
        ],
    )
    r = compare(old, new)
    assert r.verdict in (Verdict.NO_CHANGE, Verdict.COMPATIBLE), r.verdict
    # The layout churn is recorded in the audit ledger, not silently dropped...
    assert r.out_of_surface_count >= 1
    # ...and does not survive as a verdict-driving change.
    assert ChangeKind.TRIVIALLY_COPYABLE_LOST not in {c.kind for c in r.changes}
