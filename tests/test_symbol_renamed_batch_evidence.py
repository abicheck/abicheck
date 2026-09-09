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

"""SYMBOL_RENAMED_BATCH evidence-status aggregation (Codex review, Finding
C(i)): the batch change's own ``symbol_binding`` must reflect the *weakest*
constituent pair, not be left permanently unset the way it was before this
fix -- an unset ``symbol_binding`` on an ``"elf"``-tiered run downgrades a
kind in ``_ELF_BINDING_STAMPED_KINDS`` to ``EvidenceStatus.UNATTRIBUTED``,
so before this fix a fully ELF-backed batch rename could never actually
reach ``ARTIFACT_PROVEN`` on the finding-level evidence check either --
worse, once ``SYMBOL_RENAMED_BATCH`` is added to that set, an *unweighted*
"always stamp something real" fix would have gone the other way and hidden
a genuinely weak batch behind a false ``ARTIFACT_PROVEN``. Both directions
are covered below.
"""

from __future__ import annotations

from abicheck.checker_policy import EvidenceStatus, evidence_status_for_result
from abicheck.compare.namespace_move import (
    emit_namespace_move_batches,
    find_namespace_move_groups,
)
from abicheck.diff_symbols_renames import (
    emit_prefix_batch_rename,
    find_prefix_rename_pairs,
)
from abicheck.model import Function
from abicheck.model.elf_facts import SymbolBinding


def _fn(mangled: str, bound: bool) -> Function:
    return Function(
        name=mangled,
        mangled=mangled,
        return_type="void",
        elf_binding=SymbolBinding.GLOBAL if bound else None,
    )


def _real_fn(name: str, mangled: str, bound: bool) -> Function:
    """A constituent whose demangled `name` genuinely differs from its
    mangled `mangled` spelling -- the shape `_fn` above deliberately
    collapses, which is exactly what let the mangled-key/demangled-name
    mismatch bug (Codex review, fresh evidence) go uncaught by every test
    above."""
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        elf_binding=SymbolBinding.GLOBAL if bound else None,
    )


class TestPrefixBatchRenameEvidenceAggregation:
    def test_all_constituents_elf_bound_gives_a_truthy_symbol_binding(self) -> None:
        pairs = [("foo", "mylib_foo"), ("bar", "mylib_bar")]
        old_map = {"foo": _fn("foo", bound=True), "bar": _fn("bar", bound=True)}
        changes = emit_prefix_batch_rename(pairs, old_map)
        assert len(changes) == 1
        assert changes[0].symbol_binding

    def test_one_unbound_constituent_leaves_symbol_binding_unset(self) -> None:
        # `bar` was matched purely from header-reconstructed names, never
        # against a real ELF symbol-table entry -- the batch claim is only
        # as strong as this, its weakest constituent.
        pairs = [("foo", "mylib_foo"), ("bar", "mylib_bar")]
        old_map = {"foo": _fn("foo", bound=True), "bar": _fn("bar", bound=False)}
        changes = emit_prefix_batch_rename(pairs, old_map)
        assert len(changes) == 1
        assert not changes[0].symbol_binding

    def test_no_old_map_given_is_the_conservative_prior_default(self) -> None:
        # A caller with no map to offer gets the same "assume evidence was
        # examined" default `evidence_status_for_result` itself uses for an
        # empty `evidence_tiers` -- prior behavior for every existing caller
        # that doesn't thread `old_map` through.
        pairs = [("foo", "mylib_foo"), ("bar", "mylib_bar")]
        changes = emit_prefix_batch_rename(pairs)
        assert len(changes) == 1
        assert changes[0].symbol_binding

    def test_end_to_end_weak_constituent_downgrades_to_unattributed(self) -> None:
        pairs = [("foo", "mylib_foo"), ("bar", "mylib_bar")]
        old_map = {"foo": _fn("foo", bound=True), "bar": _fn("bar", bound=False)}
        change = emit_prefix_batch_rename(pairs, old_map)[0]
        assert (
            evidence_status_for_result(change, ["header", "elf"])
            is EvidenceStatus.UNATTRIBUTED
        )

    def test_end_to_end_fully_bound_batch_stays_artifact_proven(self) -> None:
        pairs = [("foo", "mylib_foo"), ("bar", "mylib_bar")]
        old_map = {"foo": _fn("foo", bound=True), "bar": _fn("bar", bound=True)}
        change = emit_prefix_batch_rename(pairs, old_map)[0]
        assert (
            evidence_status_for_result(change, ["header", "elf"])
            is EvidenceStatus.ARTIFACT_PROVEN
        )

    def test_non_elf_tiered_run_is_unaffected_either_way(self) -> None:
        # The per-finding symbol_binding check is scoped to "elf" in
        # evidence_tiers -- a header-only run's own ARTIFACT_PROVEN->
        # UNATTRIBUTED downgrade happens earlier, via has_binary_evidence,
        # not this aggregation.
        pairs = [("foo", "mylib_foo"), ("bar", "mylib_bar")]
        old_map = {"foo": _fn("foo", bound=True), "bar": _fn("bar", bound=False)}
        change = emit_prefix_batch_rename(pairs, old_map)[0]
        assert (
            evidence_status_for_result(change, ["header", "pe"])
            is EvidenceStatus.ARTIFACT_PROVEN
        )


class TestNamespaceMoveBatchEvidenceAggregation:
    def test_all_constituents_elf_bound_gives_a_truthy_symbol_binding(self) -> None:
        groups = {
            ("d1", "d2"): [
                ("ns::d1::foo", "ns::d2::foo"),
                ("ns::d1::bar", "ns::d2::bar"),
            ]
        }
        old_map = {
            "ns::d1::foo": _fn("ns::d1::foo", bound=True),
            "ns::d1::bar": _fn("ns::d1::bar", bound=True),
        }
        changes = emit_namespace_move_batches(groups, old_map)
        assert len(changes) == 1
        assert changes[0].symbol_binding

    def test_one_unbound_constituent_leaves_symbol_binding_unset(self) -> None:
        groups = {
            ("d1", "d2"): [
                ("ns::d1::foo", "ns::d2::foo"),
                ("ns::d1::bar", "ns::d2::bar"),
            ]
        }
        old_map = {
            "ns::d1::foo": _fn("ns::d1::foo", bound=True),
            "ns::d1::bar": _fn("ns::d1::bar", bound=False),
        }
        changes = emit_namespace_move_batches(groups, old_map)
        assert len(changes) == 1
        assert not changes[0].symbol_binding

    def test_no_old_map_given_is_the_conservative_prior_default(self) -> None:
        groups = {
            ("d1", "d2"): [
                ("ns::d1::foo", "ns::d2::foo"),
                ("ns::d1::bar", "ns::d2::bar"),
            ]
        }
        changes = emit_namespace_move_batches(groups)
        assert len(changes) == 1
        assert changes[0].symbol_binding

    def test_end_to_end_weak_constituent_downgrades_to_unattributed(self) -> None:
        groups = {
            ("d1", "d2"): [
                ("ns::d1::foo", "ns::d2::foo"),
                ("ns::d1::bar", "ns::d2::bar"),
            ]
        }
        old_map = {
            "ns::d1::foo": _fn("ns::d1::foo", bound=True),
            "ns::d1::bar": _fn("ns::d1::bar", bound=False),
        }
        change = emit_namespace_move_batches(groups, old_map)[0]
        assert (
            evidence_status_for_result(change, ["header", "elf"])
            is EvidenceStatus.UNATTRIBUTED
        )


class TestRealisticMangledKeyVsDemangledNameMismatch:
    """Codex review, fresh evidence: `find_prefix_rename_pairs` emits
    *demangled* `Function.name` pairs, but `old_map` (from
    `diff_symbols._public_functions`) is keyed by *mangled* symbol -- every
    test class above used `_fn(mangled, bound)` with `name=mangled`, which
    made the two identity spaces coincide by construction and silently
    hid the real mismatch. These tests use `_real_fn`, where `name` and
    `mangled` genuinely differ, going through the actual production
    `find_prefix_rename_pairs` -> `emit_prefix_batch_rename` chain
    (not hand-built pairs), to prove the fix resolves constituents by
    their real declaration rather than vacuously matching nothing.
    """

    def test_weak_constituent_is_detected_through_the_real_matcher(self) -> None:
        old_map = {
            "_ZN3Foo3barEv": _real_fn("Foo::bar()", "_ZN3Foo3barEv", bound=True),
            "_ZN3Foo3bazEv": _real_fn("Foo::baz()", "_ZN3Foo3bazEv", bound=False),
        }
        new_map = {
            "_ZN9mylib_Foo3barEv": _real_fn(
                "mylib_Foo::bar()", "_ZN9mylib_Foo3barEv", bound=True
            ),
            "_ZN9mylib_Foo3bazEv": _real_fn(
                "mylib_Foo::baz()", "_ZN9mylib_Foo3bazEv", bound=True
            ),
        }
        removed = set(old_map.keys())
        added = set(new_map.keys())
        pairs = find_prefix_rename_pairs(removed, added, old_map, new_map)
        assert len(pairs) == 2  # sanity: the matcher found both constituents

        change = emit_prefix_batch_rename(pairs, old_map)[0]
        # Before the fix this vacuously read "all constituents bound" --
        # `Foo::baz()`'s unbound old declaration must actually be found and
        # correctly downgrade the batch.
        assert not change.symbol_binding
        assert (
            evidence_status_for_result(change, ["header", "elf"])
            is EvidenceStatus.UNATTRIBUTED
        )

    def test_fully_bound_batch_is_confirmed_through_the_real_matcher(self) -> None:
        old_map = {
            "_ZN3Foo3barEv": _real_fn("Foo::bar()", "_ZN3Foo3barEv", bound=True),
            "_ZN3Foo3bazEv": _real_fn("Foo::baz()", "_ZN3Foo3bazEv", bound=True),
        }
        new_map = {
            "_ZN9mylib_Foo3barEv": _real_fn(
                "mylib_Foo::bar()", "_ZN9mylib_Foo3barEv", bound=True
            ),
            "_ZN9mylib_Foo3bazEv": _real_fn(
                "mylib_Foo::baz()", "_ZN9mylib_Foo3bazEv", bound=True
            ),
        }
        removed = set(old_map.keys())
        added = set(new_map.keys())
        pairs = find_prefix_rename_pairs(removed, added, old_map, new_map)
        assert len(pairs) == 2

        change = emit_prefix_batch_rename(pairs, old_map)[0]
        assert change.symbol_binding
        assert (
            evidence_status_for_result(change, ["header", "elf"])
            is EvidenceStatus.ARTIFACT_PROVEN
        )

    def test_namespace_move_resolves_the_real_mangled_declaration(self) -> None:
        # Two constituents genuinely moved from ns::d1 to ns::d2 -- one
        # bound, one not -- resolved through the real
        # find_namespace_move_groups -> emit_namespace_move_batches chain.
        old_map = {
            "_ZN2ns2d13fooEv": _real_fn("ns::d1::foo()", "_ZN2ns2d13fooEv", bound=True),
            "_ZN2ns2d13barEv": _real_fn(
                "ns::d1::bar()", "_ZN2ns2d13barEv", bound=False
            ),
        }
        removed = set(old_map.keys())
        added = {"_ZN2ns2d23fooEv", "_ZN2ns2d23barEv"}
        groups = find_namespace_move_groups(removed, added)
        assert groups  # sanity: a real move was detected

        changes = emit_namespace_move_batches(groups, old_map)
        assert len(changes) == 1
        assert not changes[0].symbol_binding
        assert (
            evidence_status_for_result(changes[0], ["header", "elf"])
            is EvidenceStatus.UNATTRIBUTED
        )
