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

"""ADR-067 disposition disclosure in the *release* (directory/package) fan-out.

Split out of ``test_public_rendering_boundary.py`` when that file reached the
architecture gate's 1200-line test maximum. The boundary these classes guard
is a real one rather than a filing convenience: the scalar ``compare`` path
and the per-library release fan-out build their reports through different
code, and every disposition bug this PR found in the release artifacts was
one the scalar report had already disclosed correctly for months. Grouping
them here keeps that asymmetry visible instead of scattered among the
single-pair rendering tests.
"""

from __future__ import annotations


class TestTheReleaseLedgersCarryEveryDisposition:
    """A release discloses *all three* ways a finding can be disposed of.

    ADR-067's record-before-disposing rule is not satisfied by disclosing
    two of three. `disposition_ledger_blocks` carried `suppression` and
    `surface_scope` but not ADR-039 build-context reconciliation, so a
    release whose entire breaking set was cleared as context-free
    header-parse artifacts passed while its JSON and Markdown named neither
    the findings nor the reasons — with the detail reaching only a transient
    stderr echo (Codex review, PR #1284).

    Reconciliation is the one of the three that needs no settings at all: it
    runs without a suppression document and without
    `--scope-public-headers`, so both of the other loops see nothing and the
    section was absent entirely rather than merely incomplete.
    """

    def _reconciled_result(self):
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change, DiffResult

        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo.so",
            changes=[],
            verdict=Verdict.COMPATIBLE,
        )
        result.reconciled_changes = [
            Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol="_ZN3lib4goneEi",
                description="removed",
            )
        ]
        result.reconciled_count = 1
        return result

    def _blocks(self, result):
        from abicheck.reporter import disposition_ledger_blocks

        return disposition_ledger_blocks(result)

    def test_the_reconciliation_ledger_reaches_the_release_blocks(self):
        blocks = self._blocks(self._reconciled_result())
        assert "build_context_reconciled" in blocks, (
            "a reconciled finding is disposed of and must be disclosed; "
            f"got only {sorted(blocks)}"
        )

    def test_the_disposed_finding_is_named_not_merely_counted(self):
        """A count alone does not say what was disposed of."""
        blocks = self._blocks(self._reconciled_result())
        block = blocks["build_context_reconciled"]
        assert isinstance(block, dict)
        symbols = [c["symbol"] for c in block["changes"]]
        assert symbols == ["_ZN3lib4goneEi"], symbols

    def test_the_release_markdown_renders_it_too(self):
        """JSON and Markdown must not disagree about what was disposed of."""
        from abicheck.report.render_release_markdown import (
            _release_md_disposed_findings,
        )

        lib = {"library": "libfoo.so", **self._blocks(self._reconciled_result())}
        md = "\n".join(_release_md_disposed_findings([lib]))
        assert "_ZN3lib4goneEi" in md, md
        assert "reconciled" in md, md

    def test_a_release_that_disposed_of_nothing_is_unchanged(self):
        """The guard that keeps every existing release document byte-stable:
        the blocks are added only when they say something."""
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_types import DiffResult
        from abicheck.report.render_release_markdown import (
            _release_md_disposed_findings,
        )

        clean = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo.so",
            changes=[],
            verdict=Verdict.COMPATIBLE,
        )
        assert self._blocks(clean) == {}
        assert _release_md_disposed_findings([{"library": "libfoo.so"}]) == []

    def test_every_disposition_the_scalar_path_discloses_is_reachable_here(self):
        """The invariant behind the specific fix, stated so the *next*
        disposition added to the scalar JSON path cannot quietly skip the
        release fan-out the way this one did.

        Built from a result that genuinely carries all three dispositions,
        rather than comparing key *presence* between the two paths: the
        release blocks deliberately prune a disposition that says nothing
        (that is what keeps existing release documents byte-stable), while
        the scalar report emits an empty `suppression` block unconditionally,
        so a bare key-set comparison fails on a difference that is by design.
        Both sides are still derived independently — the scalar side from a
        real `to_json` render — so this compares two renderings rather than a
        function with itself.
        """
        import json

        from abicheck.reporter import to_json

        result = self._result_with_all_three_dispositions()
        scalar = json.loads(to_json(result))
        expected = {"suppression", "surface_scope", "build_context_reconciled"}
        in_scalar = {k for k in expected if scalar.get(k)}
        assert in_scalar == expected, (
            f"vacuity guard: the scalar report disclosed only {sorted(in_scalar)}"
        )
        missing = expected - set(self._blocks(result))
        assert not missing, (
            f"the scalar report discloses {sorted(missing)} but the release "
            "fan-out does not"
        )

    def _result_with_all_three_dispositions(self):
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change

        result = self._reconciled_result()
        result.suppressed_changes = [
            Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol="_ZN3lib9hushed_upEv",
                description="d",
            )
        ]
        result.suppression_file_provided = True
        result.scope_to_public_surface = True
        result.out_of_surface_changes = [
            Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol="_ZN3lib8internalEv",
                description="d",
            )
        ]
        return result


class TestTheReleaseCarriesItsPatternModulations:
    """An ADR-027 reclassification is disclosed in the release artifact.

    A pattern rule that demotes a breaking finding is a *disposition* --
    ADR-067 names reclassification alongside suppression and scope exclusion
    -- so a release whose every break a rule demoted may not render a
    compatible JSON or Markdown artifact naming neither the rule nor the
    reason. The release fan-out carried this only under a private
    `_pattern_modulations_text` key that its caller pops and writes to
    stderr, so the requested artifact had no structured block at all (Codex
    review, PR #1284). A terminal log is not a report, and retiring `--view
    patterns` removed the last way to ask for one.

    Built from a real `PatternModulation`, never a hand-made dict -- the
    sibling class for the scalar path records why: an invented fixture
    agreed with a renderer that read the wrong key, and every real report
    rendered `?`.
    """

    def _result(self):
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_types import DiffResult
        from abicheck.pattern_verdicts import PatternModulation

        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo.so",
            changes=[],
            verdict=Verdict.COMPATIBLE,
        )
        result.pattern_modulations = [
            PatternModulation(
                symbol="_ZN3lib4goneEi",
                original_category="abi_breaking",
                new_category="quality",
                rule_id="detail_ns_rule",
                reason="internal detail namespace",
                evidence_tier="header",
                edges_matched=("a->b",),
            ).to_dict()
        ]
        return result

    def _library_entry(self):
        from abicheck.reporter import disposition_ledger_blocks

        return {"library": "libfoo.so", **disposition_ledger_blocks(self._result())}

    def test_the_structured_block_reaches_the_release_entry(self):
        entry = self._library_entry()
        assert "pattern_modulations" in entry, (
            f"a reclassification must be disclosed; got {sorted(entry)}"
        )

    def test_the_release_markdown_names_the_rule_and_the_reason(self):
        """The columns the section exists for, not merely that it rendered."""
        from abicheck.report.render_release_markdown import (
            _release_md_pattern_modulations,
        )

        md = "\n".join(_release_md_pattern_modulations([self._library_entry()]))
        assert "detail_ns_rule" in md, md
        assert "internal detail namespace" in md, md
        assert "_ZN3lib4goneEi" in md, md

    def test_it_renders_one_section_not_two_headings(self):
        """The shared row renderer opens its own H2 for the scalar document;
        the release wrapper already opened one, so it must suppress it."""
        from abicheck.report.render_release_markdown import (
            _release_md_pattern_modulations,
        )

        md = _release_md_pattern_modulations([self._library_entry()])
        h2 = [line for line in md if line.startswith("## ")]
        assert len(h2) == 1, f"expected one H2 section heading, got {h2}"

    def test_a_release_with_no_modulation_renders_nothing(self):
        """ADR-027 is opt-in and off by default, so every existing release
        report must be byte-unchanged."""
        from abicheck.report.render_release_markdown import (
            _release_md_pattern_modulations,
        )

        assert _release_md_pattern_modulations([{"library": "libfoo.so"}]) == []

    def test_the_shared_renderer_still_heads_its_own_scalar_section(self):
        """The other half of the switch: suppressing the heading for the
        release must not remove it from the scalar document."""
        from abicheck.report.pattern_modulations_markdown import (
            render_pattern_modulations_from_mapping,
        )

        rows = self._result().pattern_modulations
        with_heading = render_pattern_modulations_from_mapping(rows)
        without = render_pattern_modulations_from_mapping(rows, include_heading=False)
        assert any(line.startswith("## ") for line in with_heading)
        assert not any(line.startswith("## ") for line in without)
        # The table itself is identical either way -- that is the point of
        # sharing the renderer rather than re-spelling the columns.
        assert [x for x in with_heading if x.startswith("|")] == [
            x for x in without if x.startswith("|")
        ]
