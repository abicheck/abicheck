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

"""``analysis_assurance.schema_staleness_status`` -- split out of
``test_analysis_assurance.py`` (which was already at the file-size hard cap;
see root ``AGENTS.md``'s "Files that are large" / "add a sibling module"
guidance) rather than folded into it.

A snapshot carrying facts ``model.snapshot_reliability.
degraded_reliability_facts`` marks stale used to load with only a
stderr-only ``UserWarning`` -- invisible to any JSON consumer of
``run_outcome.analysis_assurance``, which still read ``status="complete"``
for a comparison some detector had just declined to fully trust. See
``model/snapshot_reliability.py``'s and ``analysis_assurance.
_schema_staleness_status``'s own docstrings for the full account.
"""

from __future__ import annotations

import json
from pathlib import Path

from abicheck import checker
from abicheck.analysis_assurance import AnalysisAssurance
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.snapshot_reliability import degraded_reliability_facts
from abicheck.serialization import snapshot_from_dict


def _fn(name: str, mangled: str) -> Function:
    return Function(
        name=name, mangled=mangled, return_type="int", visibility=Visibility.PUBLIC
    )


def _header_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    """A real, header-scoped, unchanged pair -- mirrors
    ``test_analysis_assurance.py``'s own helper of the same name/shape."""
    common = {"library": "libfoo.so.1", "from_headers": True}
    fns = [_fn("pub_a", "_Z5pub_av")]
    return (
        AbiSnapshot(version="1.0", functions=fns, **common),
        AbiSnapshot(version="2.0", functions=fns, **common),
    )


class TestSchemaStalenessStatus:
    #: Which extra ``AbiSnapshot`` kwargs make each flag's one real consumer
    #: actually *consult* it, mirroring ``degraded_reliability_facts``'s own
    #: consultation table (``model/snapshot_reliability.py``) -- three flags
    #: are consulted unconditionally, the rest need confirmed header
    #: awareness (and, for two of them, one exact ``ast_producer``).
    _CONSULTED_KWARGS: dict[str, dict[str, object]] = {
        "header_cv_facts_reliable": {},
        "clang_deprecation_facts_reliable": {"from_headers": True},
        "clang_field_initializer_facts_reliable": {"from_headers": True},
        "clang_vtable_facts_reliable": {},
        "clang_restrict_facts_reliable": {"from_headers": True},
        "clang_va_list_facts_reliable": {
            "from_headers": True,
            "ast_producer": "clang",
        },
        "castxml_var_access_facts_reliable": {
            "from_headers": True,
            "ast_producer": "castxml",
        },
        "param_kind_facts_reliable": {},
    }

    def test_v4_fixture_with_degraded_schema_reports_non_complete_status(
        self,
    ) -> None:
        """Item 4's exact regression shape: round-trip-load a
        schema-version-predating-current snapshot (mirrors
        ``test_schema_compat.py``'s own fixture-degradation tests) through
        ``compute_analysis_assurance`` and assert the reported status is no
        longer honestly ``"complete"``, with the stale field named in
        ``notes``."""
        fixtures_dir = Path(__file__).parent / "fixtures" / "schema"
        d = json.loads((fixtures_dir / "v4.json").read_text())
        d["schema_version"] = 9
        d["from_headers"] = True
        d["ast_producer"] = "clang"
        old = snapshot_from_dict(d)
        new = snapshot_from_dict(d)
        assert old.clang_deprecation_facts_reliable is False

        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.schema_staleness_status == "degraded"
        assert aa.status != "complete"
        assert any("clang_deprecation_facts_reliable" in n for n in aa.notes), aa.notes

    def test_v4_fixture_roundtrip_reports_the_same_compatibility_result(
        self,
    ) -> None:
        """Item 5: this fix must only change ``assurance.status``/
        ``coverage_warnings``, never a verdict or finding (ADR-028 D3: L3+
        evidence narrows confidence, never deletes/fabricates a
        compatibility fact). The same v25-on-disk-vs-freshly-written v45
        shape (self-comparison of an identical, schema-degraded snapshot)
        must keep reporting 0 breaking / 0 ``func_params_changed`` changes."""
        fixtures_dir = Path(__file__).parent / "fixtures" / "schema"
        d = json.loads((fixtures_dir / "v4.json").read_text())
        d["schema_version"] = 25
        d["from_headers"] = True
        d["ast_producer"] = "clang"
        old = snapshot_from_dict(d)
        new = snapshot_from_dict(d)
        assert degraded_reliability_facts(old)  # this fixture is degraded

        result = checker.compare(old, new)
        assert result.analysis_assurance.schema_staleness_status == "degraded"
        assert not result.changes, result.changes
        assert not any(c.kind.value == "func_params_changed" for c in result.changes), (
            result.changes
        )

    def test_clean_pair_reports_clean_schema_staleness(self) -> None:
        old, new = _header_pair()
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.schema_staleness_status == "clean"
        assert aa.status == "complete"

    def test_one_sided_degraded_fact_is_enough_to_taint_the_pair(self) -> None:
        """A single side's stale fact already means the affected
        detector(s) declined to trust it for this comparison -- unlike the
        other context-status axes, there is no "both sides must agree"
        symmetry requirement here."""
        old, new = _header_pair()
        old.param_kind_facts_reliable = False

        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.schema_staleness_status == "degraded"
        assert aa.status != "complete"
        assert any(
            "old snapshot" in n and "param_kind_facts_reliable" in n for n in aa.notes
        ), aa.notes

    def test_new_side_alone_degraded_is_enough_to_taint_the_pair(self) -> None:
        """The mirror of the case above: the *new* side being the one with
        the stale fact (old clean) must taint the status too -- notes must
        name it as the new snapshot's own, not silently fold into the old
        snapshot's note or get skipped because old_degraded is empty."""
        old, new = _header_pair()
        new.param_kind_facts_reliable = False

        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.schema_staleness_status == "degraded"
        assert aa.status != "complete"
        assert any(
            "new snapshot" in n and "param_kind_facts_reliable" in n for n in aa.notes
        ), aa.notes
        assert not any("old snapshot" in n for n in aa.notes), aa.notes

    def test_every_degraded_reliability_flag_taints_the_reported_status(self) -> None:
        """Generalized regression, not just the one ``param_kind_facts_
        reliable``/``clang_deprecation_facts_reliable`` fixtures above: ANY
        ``AbiSnapshot`` with a consulted ``*_facts_reliable`` flag set False
        -- current schema version, hand-constructed, no fixture load
        involved at all -- must produce a non-``"complete"`` status and name
        that exact flag in ``notes``. This is the shape the underlying bug
        report named explicitly: a *mechanism*, not a single input, must
        stay covered so a sibling flag/producer combination can't silently
        reopen the same gap ``param_kind_facts_reliable`` closed."""
        for flag_name, extra_kwargs in self._CONSULTED_KWARGS.items():
            common = {"library": "libfoo.so.1", **extra_kwargs}
            fns = [_fn("pub_a", "_Z5pub_av")]
            old = AbiSnapshot(
                version="1.0", functions=fns, **{flag_name: False}, **common
            )
            new = AbiSnapshot(version="2.0", functions=fns, **common)
            assert degraded_reliability_facts(old) == [flag_name], flag_name

            result = checker.compare(old, new)
            aa = result.analysis_assurance
            assert aa.schema_staleness_status == "degraded", flag_name
            assert aa.status != "complete", flag_name
            assert any(flag_name in n for n in aa.notes), (flag_name, aa.notes)

    def test_hybrid_producer_exempt_flags_never_taint_status(self) -> None:
        """The mirror case: ``clang_va_list_facts_reliable``/
        ``castxml_var_access_facts_reliable`` are False for a "hybrid"
        producer by construction (correct for the fact's own provenance),
        but neither flag's one real consumer ever reads it for "hybrid" --
        so it must not taint the reported status either, the same
        ``test_older_version_silent_for_hybrid_producer_flags_no_detector_
        reads`` shape ``test_schema_compat.py`` already covers for the
        load-time warning."""
        common = {
            "library": "libfoo.so.1",
            "from_headers": True,
            "ast_producer": "hybrid",
        }
        fns = [_fn("pub_a", "_Z5pub_av")]
        old = AbiSnapshot(
            version="1.0",
            functions=fns,
            clang_va_list_facts_reliable=False,
            castxml_var_access_facts_reliable=False,
            **common,
        )
        new = AbiSnapshot(version="2.0", functions=fns, **common)
        assert degraded_reliability_facts(old) == []

        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.schema_staleness_status == "clean"

    def test_mixed_producer_pair_never_taints_on_the_pair_gated_flags(self) -> None:
        """Codex review, PR #1209: ``clang_va_list_facts_reliable``/
        ``castxml_var_access_facts_reliable``'s one real consumer
        (``diff_symbols._diff_param_va_list``/``_diff_var_access``) exits at
        its own both-sides-exact-producer gate -- so a degraded, confirmed-
        header "clang" old side paired with a "castxml" new side means that
        detector never even runs for this pair, and regenerating the stale
        side could not enable it. The single-snapshot ``degraded_
        reliability_facts(old)`` alone cannot see this (it only knows
        old's own producer), so ``schema_staleness_status`` must narrow it
        pair-aware rather than report a false ``"degraded"``."""
        old = AbiSnapshot(
            version="1.0",
            library="libfoo.so.1",
            functions=[_fn("pub_a", "_Z5pub_av")],
            from_headers=True,
            ast_producer="clang",
            clang_va_list_facts_reliable=False,
        )
        new = AbiSnapshot(
            version="2.0",
            library="libfoo.so.1",
            functions=[_fn("pub_a", "_Z5pub_av")],
            from_headers=True,
            ast_producer="castxml",
        )
        # Single-snapshot: old alone still reports it (it doesn't know new's
        # producer) -- the pair-aware narrowing lives in schema_staleness_
        # status, not in degraded_reliability_facts itself.
        assert degraded_reliability_facts(old) == ["clang_va_list_facts_reliable"]

        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.schema_staleness_status == "clean"
        assert not any("clang_va_list_facts_reliable" in n for n in aa.notes), aa.notes

    def test_same_producer_pair_still_taints_the_pair_gated_flags(self) -> None:
        """The mirror of the case above: when BOTH sides really are
        "clang", the detector's gate is satisfied and a degraded flag on
        either side must still taint the status -- the pair-aware
        narrowing must not over-exempt."""
        old = AbiSnapshot(
            version="1.0",
            library="libfoo.so.1",
            functions=[_fn("pub_a", "_Z5pub_av")],
            from_headers=True,
            ast_producer="clang",
            clang_va_list_facts_reliable=False,
        )
        new = AbiSnapshot(
            version="2.0",
            library="libfoo.so.1",
            functions=[_fn("pub_a", "_Z5pub_av")],
            from_headers=True,
            ast_producer="clang",
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.schema_staleness_status == "degraded"
        assert any("clang_va_list_facts_reliable" in n for n in aa.notes), aa.notes

    def test_other_side_only_inferred_header_awareness_never_taints(self) -> None:
        """Codex review, PR #1209 round 5: the other side matching the
        exact producer is not enough on its own -- ``_diff_param_va_list``
        also requires ``_both_header_aware`` (confirmed, non-inferred
        header awareness on BOTH sides). An ``other`` side that is
        ``ast_producer == "clang"`` but only ever had its ``from_headers``
        *inferred* (``from_headers_inferred=True`` -- a legacy pre-explicit-
        key snapshot) still fails that gate, so the detector never runs for
        this pair either, the same as a producer mismatch."""
        old = AbiSnapshot(
            version="1.0",
            library="libfoo.so.1",
            functions=[_fn("pub_a", "_Z5pub_av")],
            from_headers=True,
            ast_producer="clang",
            clang_va_list_facts_reliable=False,
        )
        new = AbiSnapshot(
            version="2.0",
            library="libfoo.so.1",
            functions=[_fn("pub_a", "_Z5pub_av")],
            from_headers=True,
            ast_producer="clang",
        )
        new.from_headers_inferred = True
        assert degraded_reliability_facts(old) == ["clang_va_list_facts_reliable"]

        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.schema_staleness_status == "clean"
        assert not any("clang_va_list_facts_reliable" in n for n in aa.notes), aa.notes

    def test_self_diff_never_taints_the_no_baseline_audit(self) -> None:
        """Codex review, PR #1209 round 6: ``workflows.no_baseline_compare``
        audits a candidate with no real baseline by calling ``checker.
        compare(new, new, ...)`` -- the literal same object as both sides --
        to reuse the ordinary comparison machinery, then discards the
        (asserted-empty) comparison half and keeps only the candidate-side
        hygiene findings. A stale candidate's own degraded fact must not
        make that audit read ``"partial"``/``"degraded"``: no real pairwise
        comparison ever happens (comparing a value against itself can never
        produce a false pairwise finding, reliable or not), so reporting it
        as both a stale "old snapshot" and a stale "new snapshot" would be
        double-counting the one candidate's own degradation as if it were
        two distinct sides."""
        old, new = _header_pair()
        new.param_kind_facts_reliable = False
        assert degraded_reliability_facts(new) == ["param_kind_facts_reliable"]

        result = checker.compare(new, new)
        aa = result.analysis_assurance
        assert aa.schema_staleness_status == "clean"
        assert not any("param_kind_facts_reliable" in n for n in aa.notes), aa.notes
        assert not any("old snapshot" in n or "new snapshot" in n for n in aa.notes), (
            aa.notes
        )

        # The mirror: two independently-built (merely content-identical,
        # not object-identical) snapshots must NOT take this shortcut --
        # real object identity is what this checks, never equal content.
        new_copy = AbiSnapshot(
            version=new.version,
            library=new.library,
            functions=list(new.functions),
            from_headers=new.from_headers,
            param_kind_facts_reliable=False,
        )
        result2 = checker.compare(new, new_copy)
        assert result2.analysis_assurance.schema_staleness_status == "degraded"
