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

"""A closure's identity must not embed its source ``:line:col``.

Reported (real oneTBB 2021.13.0 -> 2022.3.0 comparison, AGENTS.md's
"Lambda-closure churn" entry, item 2): an unrelated edit anywhere earlier in
a header shifts every lambda declared below it to a new line/column. Since
:func:`~abicheck.name_classification.strip_anonymous_type_location` keeps
``:<line>:<col>`` as the only discriminator between two distinct lambdas in
one header, an unchanged closure-parameterized type/function then compares
as removed-plus-added purely from that line drift -- three separate noise
classes in one real report: a spurious ``type_removed``/``type_added`` pair,
a paired ``func_removed``/``func_added`` on every ctor/dtor/method of that
instantiation (via castxml's synthetic ctor/dtor keys, which embed the same
owner spelling), and a ``declaration_renamed`` RISK finding whose entire
content is the line-number text.

:func:`~abicheck.qualified_name_segments.renumber_anonymous_closure_identities` fixes this by
replacing the line:col discriminator with a stable ordinal -- "the Nth
lambda of this marker kind declared in this header" -- computed once per
snapshot. As long as an edit doesn't reorder or add/remove same-header,
same-kind lambdas relative to each other (true for every reported case,
which is unrelated line drift), both sides of a comparison assign the
identical ordinal to the identical closure.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.model import AbiSnapshot, Function, Param, RecordType, Visibility
from abicheck.name_classification import strip_anonymous_type_location
from abicheck.qualified_name_segments import (
    apply_anonymous_type_ordinals,
    collect_anonymous_type_ordinals,
    renumber_anonymous_closure_identities,
)
from abicheck.serialization import snapshot_from_dict


def _closure(header: str, line: int, col: int) -> str:
    return strip_anonymous_type_location(f"(lambda at /src/x/{header}:{line}:{col})")


class TestOrdinalsAreStableAcrossLineDrift:
    def test_two_lambdas_keep_their_relative_order(self) -> None:
        old_names = [
            f"raii_guard<{_closure('task_group.h', 522, 26)}>",
            f"raii_guard<{_closure('task_group.h', 520, 18)}>",
        ]
        new_names = [
            f"raii_guard<{_closure('task_group.h', 539, 26)}>",
            f"raii_guard<{_closure('task_group.h', 528, 18)}>",
        ]
        old_ordinals = collect_anonymous_type_ordinals(old_names)
        new_ordinals = collect_anonymous_type_ordinals(new_names)
        old_final = [apply_anonymous_type_ordinals(n, old_ordinals) for n in old_names]
        new_final = [apply_anonymous_type_ordinals(n, new_ordinals) for n in new_names]
        assert old_final == new_final

    def test_different_headers_never_collide(self) -> None:
        names = [
            f"raii_guard<{_closure('a.h', 4, 3)}>",
            f"raii_guard<{_closure('b.h', 4, 3)}>",
        ]
        ordinals = collect_anonymous_type_ordinals(names)
        rewritten = [apply_anonymous_type_ordinals(n, ordinals) for n in names]
        assert rewritten[0] != rewritten[1]

    def test_a_marker_absent_from_the_ordinal_map_is_left_untouched(self) -> None:
        name = f"raii_guard<{_closure('a.h', 4, 3)}>"
        assert apply_anonymous_type_ordinals(name, {}) == name

    def test_quoted_ntt_string_looking_like_a_marker_is_not_rewritten(self) -> None:
        quoted = 'Tag<"(lambda:a.h:1:2)">'
        ordinals = collect_anonymous_type_ordinals([quoted])
        assert ordinals == {}
        assert apply_anonymous_type_ordinals(quoted, {("(lambda", "a.h", 1, 2): 1}) == quoted


def _record(name: str, qualified: str | None = None) -> RecordType:
    return RecordType(name=name, kind="class", qualified_name=qualified, size_bits=8)


class TestSnapshotRenumbering:
    def _snapshot(self, version: str, line1: int, line2: int) -> AbiSnapshot:
        owner1 = f"tbb::detail::d1::raii_guard<{_closure('task_group.h', line1, 26)}>"
        owner2 = f"tbb::detail::d1::raii_guard<{_closure('task_group.h', line2, 18)}>"
        types = [
            _record(owner1.rsplit("::", 1)[-1], qualified=owner1),
            _record(owner2.rsplit("::", 1)[-1], qualified=owner2),
        ]
        ctor = Function(
            name="raii_guard::raii_guard",
            mangled=f"__abicheck_ctor__{owner1}()",
            return_type="void",
            visibility=Visibility.PUBLIC,
            params=[Param(name="p", type=f"{owner1} &&")],
        )
        return AbiSnapshot(
            library="libtbb.so", version=version, types=types, functions=[ctor]
        )

    def test_renumbering_makes_an_unrelated_line_shift_a_no_op(self) -> None:
        old = self._snapshot("2021.13.0", 522, 520)
        new = self._snapshot("2022.3.0", 539, 528)

        renumber_anonymous_closure_identities(old)
        renumber_anonymous_closure_identities(new)

        assert old.types[0].qualified_name == new.types[0].qualified_name
        assert old.types[1].qualified_name == new.types[1].qualified_name
        assert old.functions[0].mangled == new.functions[0].mangled
        assert old.functions[0].params[0].type == new.functions[0].params[0].type

    def test_without_renumbering_the_line_shift_is_visible(self) -> None:
        # Sanity check that the fixture actually reproduces the bug absent
        # the fix, so the assertions above are testing something real.
        old = self._snapshot("2021.13.0", 522, 520)
        new = self._snapshot("2022.3.0", 539, 528)
        assert old.types[0].qualified_name != new.types[0].qualified_name
        assert old.functions[0].mangled != new.functions[0].mangled

    def test_compare_reports_no_findings_for_pure_line_drift(self) -> None:
        """End-to-end: renumbering both sides before compare() eliminates the
        func_removed/func_added pair and the type-identity churn a plain
        line-number identity would otherwise report."""
        old = self._snapshot("2021.13.0", 522, 520)
        new = self._snapshot("2022.3.0", 539, 528)
        renumber_anonymous_closure_identities(old)
        renumber_anonymous_closure_identities(new)

        result = compare(old, new)
        noisy_kinds = {
            ChangeKind.FUNC_REMOVED,
            ChangeKind.FUNC_ADDED,
            ChangeKind.TYPE_REMOVED,
            ChangeKind.TYPE_ADDED,
        }
        assert not ({c.kind for c in result.changes} & noisy_kinds)

    def test_compare_without_renumbering_reports_the_reported_noise(self) -> None:
        old = self._snapshot("2021.13.0", 522, 520)
        new = self._snapshot("2022.3.0", 539, 528)
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.FUNC_REMOVED in kinds
        assert ChangeKind.FUNC_ADDED in kinds

    def test_renumbering_is_idempotent(self) -> None:
        snap = self._snapshot("2021.13.0", 522, 520)
        renumber_anonymous_closure_identities(snap)
        once = (snap.types[0].qualified_name, snap.functions[0].mangled)
        renumber_anonymous_closure_identities(snap)
        twice = (snap.types[0].qualified_name, snap.functions[0].mangled)
        assert once == twice

    def test_a_snapshot_with_no_closures_is_untouched(self) -> None:
        snap = AbiSnapshot(
            library="libplain.so",
            version="1.0",
            types=[_record("Widget", qualified="ns::Widget")],
            functions=[
                Function(
                    name="Widget::Widget",
                    mangled="_ZN2ns6WidgetC1Ev",
                    return_type="void",
                )
            ],
        )
        before_type = snap.types[0].qualified_name
        before_func = snap.functions[0].mangled
        renumber_anonymous_closure_identities(snap)
        assert snap.types[0].qualified_name == before_type
        assert snap.functions[0].mangled == before_func


class TestLegacyPersistedSnapshotsAreRenumberedOnLoad:
    """A snapshot persisted by a pre-fix abicheck still carries the raw
    ``:<line>:<col>`` closure identity. Comparing it (via ``compare``'s
    normal saved-baseline-vs-fresh-dump workflow) against a freshly-dumped
    snapshot -- which IS renumbered -- must not manufacture a
    removed+added pair purely from the encoding change (Codex review on
    the original PR)."""

    def _legacy_dict(self, line: int) -> dict:
        owner = f"tbb::detail::d1::raii_guard<{_closure('task_group.h', line, 26)}>"
        return {
            "library": "libtbb.so",
            "version": "2021.13.0",
            "schema_version": 25,
            "types": [
                {
                    "name": owner.rsplit("::", 1)[-1],
                    "qualified_name": owner,
                    "kind": "class",
                    "size_bits": 8,
                }
            ],
            "functions": [
                {
                    "name": "raii_guard::raii_guard",
                    "mangled": f"__abicheck_ctor__{owner}()",
                    "return_type": "void",
                }
            ],
        }

    def test_loading_a_legacy_snapshot_renumbers_it(self) -> None:
        loaded = snapshot_from_dict(self._legacy_dict(522))
        assert "#" in loaded.types[0].qualified_name
        assert ":522:" not in loaded.types[0].qualified_name

    def test_legacy_baseline_agrees_with_a_fresh_dump_across_line_drift(
        self,
    ) -> None:
        legacy_baseline = snapshot_from_dict(self._legacy_dict(522))

        fresh = AbiSnapshot(
            library="libtbb.so",
            version="2022.3.0",
            types=[
                _record(
                    f"raii_guard<{_closure('task_group.h', 539, 26)}>",
                    qualified=(
                        "tbb::detail::d1::raii_guard<"
                        f"{_closure('task_group.h', 539, 26)}>"
                    ),
                )
            ],
            functions=[
                Function(
                    name="raii_guard::raii_guard",
                    mangled=(
                        "__abicheck_ctor__tbb::detail::d1::raii_guard<"
                        f"{_closure('task_group.h', 539, 26)}>()"
                    ),
                    return_type="void",
                )
            ],
        )
        renumber_anonymous_closure_identities(fresh)

        assert legacy_baseline.types[0].qualified_name == fresh.types[0].qualified_name
        assert legacy_baseline.functions[0].mangled == fresh.functions[0].mangled

        result = compare(legacy_baseline, fresh)
        noisy_kinds = {
            ChangeKind.FUNC_REMOVED,
            ChangeKind.FUNC_ADDED,
            ChangeKind.TYPE_REMOVED,
            ChangeKind.TYPE_ADDED,
        }
        assert not ({c.kind for c in result.changes} & noisy_kinds)

    def test_a_snapshot_already_in_ordinal_form_round_trips_unchanged(self) -> None:
        already_ordinal = {
            "library": "libtbb.so",
            "version": "2022.3.0",
            "schema_version": 25,
            "types": [
                {
                    "name": "raii_guard<(lambda:task_group.h#1)>",
                    "kind": "class",
                }
            ],
        }
        loaded = snapshot_from_dict(already_ordinal)
        assert loaded.types[0].name == "raii_guard<(lambda:task_group.h#1)>"


class TestKnownLimitationDifferentFilesSharingABasename:
    """Documented, accepted limitation (Codex review): the ordinal group
    key is ``(marker, header basename)`` -- the same checkout-independent
    basename :func:`_declaring_header_discriminator` already reduces a
    full path to -- so two genuinely different files sharing a basename
    share one ordinal sequence. This test pins the documented behavior so
    a future change to the grouping key is a deliberate decision, not a
    silent regression in either direction."""

    def test_an_edit_in_one_file_can_reorder_an_unrelated_same_basename_file(
        self,
    ) -> None:
        # Two DIFFERENT physical files, both named "config.h" (a vendored
        # dependency shape), each declaring one lambda -- before either
        # snapshot has an edit, both compare identically to a snapshot of
        # themselves alone.
        before = [
            strip_anonymous_type_location("(lambda at /vendor/a/config.h:100:1)"),
            strip_anonymous_type_location("(lambda at /vendor/b/config.h:5:1)"),
        ]
        before_ordinals = collect_anonymous_type_ordinals(before)
        before_final = [
            apply_anonymous_type_ordinals(n, before_ordinals) for n in before
        ]

        # An unrelated lambda is added to vendor/b/config.h *only*, at a
        # line ahead of vendor/a's own (unedited) lambda.
        after = [
            strip_anonymous_type_location("(lambda at /vendor/a/config.h:100:1)"),
            strip_anonymous_type_location("(lambda at /vendor/b/config.h:1:1)"),
            strip_anonymous_type_location("(lambda at /vendor/b/config.h:5:1)"),
        ]
        after_ordinals = collect_anonymous_type_ordinals(after)
        after_final = [
            apply_anonymous_type_ordinals(n, after_ordinals) for n in after
        ]

        # vendor/a's own, completely unedited lambda is reassigned a
        # different ordinal purely because of the unrelated insertion in
        # vendor/b -- the documented, accepted limitation.
        assert before_final[0] != after_final[0]


class TestHybridMergeDefersRenumbering:
    """Codex review: ``--ast-frontend hybrid`` runs castxml and clang over
    the same headers and merges the two snapshots by identity key
    (``type_map_key``/mangled name). If each backend's own closure ordinal
    were computed independently -- BEFORE the merge -- a backend that sees
    a header's lambdas in a different count (one omits an earlier
    same-header lambda the other captures) would assign the SAME later
    closure a DIFFERENT ordinal on each side, and the merge's identity
    lookup would silently miss the join. Deferring renumbering until after
    the merge means both backends' RAW ``:line:col`` spellings -- which
    agree by construction, since both describe the same physical source
    location -- are what the merge actually keys on.
    """

    def test_shared_closure_merges_despite_differing_lambda_counts(self) -> None:
        from abicheck.dumper_hybrid import run_hybrid_dump

        shared = _closure("widget.h", 20, 5)
        owner_castxml = f"Foo<{shared}>"
        owner_clang = f"Foo<{shared}>"

        castxml_snap = AbiSnapshot(
            library="lib.so",
            version="old",
            from_headers=True,
            types=[
                # castxml sees an EARLIER lambda too (a template castxml
                # instantiates that clang's own leg never reaches), so its
                # own independent ordinal for the shared closure would be
                # #2, not #1.
                _record(f"Earlier<{_closure('widget.h', 5, 1)}>"),
                _record(owner_castxml, qualified=owner_castxml),
            ],
        )
        clang_snap = AbiSnapshot(
            library="lib.so",
            version="old",
            from_headers=True,
            types=[
                # clang's leg never instantiates the earlier template, so
                # its own independent ordinal for the SAME shared closure
                # would be #1 -- a real fact only clang captures rides
                # along on this type, to prove the merge actually joined.
                replace(
                    _record(owner_clang, qualified=owner_clang), is_abstract=True
                ),
            ],
        )

        def fake_dump(so_path, headers, *, header_backend, **kwargs):
            return castxml_snap if header_backend == "castxml" else clang_snap

        merged = run_hybrid_dump(fake_dump, Path("lib.so"), [])

        matched = [t for t in merged.types if t.name.startswith("Foo<")]
        assert len(matched) == 1, "the shared closure must merge into one type"
        assert matched[0].is_abstract is True, (
            "clang's is_abstract fact must have reached the merged type -- "
            "if the merge missed the join (mismatched per-leg ordinals), "
            "this would be None (castxml's own value) instead"
        )
        # And the final, merged snapshot IS in ordinal form -- renumbering
        # happened, just deferred to after the merge.
        assert "#" in matched[0].name
        assert ":20:" not in matched[0].name


class TestFactProvenanceKeysAreRenumberedToo:
    """A hybrid snapshot's ``fact_provenance`` dict is keyed by composite
    strings (``fact_provenance.type_fact_key``/``field_fact_key``) that
    embed the exact same closure-parameterized type-name spelling
    ``types``/``functions``/etc. carry. If renumbering rewrote only the
    ABI-surface fields and left these keys in ``:line:col`` form, a
    renamed type's provenance would become unreachable through
    ``fact_provenance.fact_producer()`` -- silently defeating every
    provenance-gated detector for that declaration (Codex review on
    PR #868, fresh evidence)."""

    def test_type_fact_key_is_renumbered_in_place(self) -> None:
        from abicheck.fact_provenance import type_fact_key

        owner = f"Foo<{_closure('widget.h', 20, 5)}>"
        snap = AbiSnapshot(
            library="lib.so",
            version="1",
            from_headers=True,
            ast_producer="hybrid",
            types=[_record(owner, qualified=owner)],
            fact_provenance={type_fact_key(owner, "is_abstract"): "castxml"},
        )
        renumber_anonymous_closure_identities(snap)

        new_name = snap.types[0].qualified_name
        assert new_name is not None
        assert "#" in new_name
        assert type_fact_key(new_name, "is_abstract") in snap.fact_provenance
        assert type_fact_key(owner, "is_abstract") not in snap.fact_provenance
        assert snap.fact_provenance[type_fact_key(new_name, "is_abstract")] == (
            "castxml"
        )

    def test_field_fact_key_is_renumbered_in_place(self) -> None:
        from abicheck.fact_provenance import field_fact_key

        owner = f"Foo<{_closure('widget.h', 20, 5)}>"
        snap = AbiSnapshot(
            library="lib.so",
            version="1",
            from_headers=True,
            ast_producer="hybrid",
            types=[_record(owner, qualified=owner)],
            fact_provenance={field_fact_key(owner, "x", "default"): "clang"},
        )
        renumber_anonymous_closure_identities(snap)

        new_name = snap.types[0].qualified_name
        assert new_name is not None
        assert field_fact_key(new_name, "x", "default") in snap.fact_provenance

    def test_fact_producer_resolves_after_renumbering(self) -> None:
        """End-to-end through the real reader, not just the raw dict key."""
        from abicheck.fact_provenance import fact_producer, type_fact_key

        owner = f"Foo<{_closure('widget.h', 20, 5)}>"
        snap = AbiSnapshot(
            library="lib.so",
            version="1",
            from_headers=True,
            ast_producer="hybrid",
            types=[_record(owner, qualified=owner)],
            fact_provenance={type_fact_key(owner, "is_abstract"): "castxml"},
        )
        renumber_anonymous_closure_identities(snap)
        renamed_owner = snap.types[0].qualified_name
        assert renamed_owner is not None
        key = type_fact_key(renamed_owner, "is_abstract")
        assert fact_producer(snap, key) == "castxml"
