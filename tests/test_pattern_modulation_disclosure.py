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


"""A pattern rule's disposition is disclosed in every human output.

Split out of ``test_public_rendering_boundary.py`` at the architecture
gate's 1200-line test maximum. The grouping is real rather than a filing
convenience: ADR-027 modulation is the disposition this PR kept finding
half-wired, and each time in a *different* output -- the full Markdown
document, then the release JSON and Markdown, then ``--view root-cause``,
the ``-o review=...`` digest and the stderr ledger. What they have in
common is the claim, not the renderer, so the tests that state it belong
together.
"""

from __future__ import annotations


class TestEveryHumanMarkdownModeDisclosesAModulation:
    """A rule that demoted a finding is named in *every* human Markdown mode.

    ADR-067's record-before-disposing rule is about the artifact a user
    requested, and `--view root-cause` and `-o review=...` are two of the
    three things a user can request. Both were silent: the full document
    rendered the ledger while `render_root_cause_document` and the review
    digest had no consumer for it, so an accepted result named neither the
    rule nor its reason (Codex review, PR #1284).

    Two *different* causes behind one symptom, which is why this is a sweep
    rather than one test: the root-cause builder assembles its own
    `ReportDocument` and never populated the field, and the review digest
    carried it but lost it in the mapping round trip its document boundary
    performs. Fixing either alone leaves the other silent.
    """

    RULE = "detail_ns_rule"
    REASON = "internal detail namespace"

    def _result(self):
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change, DiffResult
        from abicheck.pattern_verdicts import PatternModulation

        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo.so",
            changes=[
                Change(
                    kind=ChangeKind.FUNC_REMOVED,
                    symbol="_ZN3lib4goneEi",
                    description="d",
                )
            ],
            verdict=Verdict.BREAKING,
        )
        result.pattern_modulations = [
            PatternModulation(
                symbol="_ZN3lib4goneEi",
                original_category="abi_breaking",
                new_category="quality",
                rule_id=self.RULE,
                reason=self.REASON,
                evidence_tier="header",
                edges_matched=("a->b",),
            ).to_dict()
        ]
        return result

    def _renderers(self):
        from abicheck.report.dispatch_markdown import to_markdown
        from abicheck.reporter import to_review_digest

        return {
            "markdown/full": lambda r: to_markdown(r, report_mode="full"),
            "markdown/root-cause": lambda r: to_markdown(r, report_mode="root-cause"),
            "review": lambda r: to_review_digest(r),
        }

    def test_every_mode_names_the_rule_and_the_reason(self):
        """Batched, so a failure names every silent mode at once."""
        result = self._result()
        silent = {}
        for name, render in self._renderers().items():
            text = render(result)
            missing = [want for want in (self.RULE, self.REASON) if want not in text]
            if missing:
                silent[name] = missing
        assert not silent, f"these modes disclosed no modulation: {silent}"

    def test_a_run_with_no_modulation_is_unchanged(self):
        """ADR-027 is opt-in and off by default, so every existing report of
        every mode must be byte-identical."""
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_types import DiffResult

        clean = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo.so",
            changes=[],
            verdict=Verdict.COMPATIBLE,
        )
        for name, render in self._renderers().items():
            text = render(clean)
            assert "Pattern-modulated" not in text, name
            assert "pattern-modulated" not in text.lower(), name

    def test_the_oracle_is_not_vacuous(self):
        """If the rule id stopped appearing for a reason unrelated to the
        ledger, the sweep above would pass against a renderer that shows
        nothing. Assert the modulated symbol reaches each mode too."""
        result = self._result()
        for name, render in self._renderers().items():
            assert "gone" in render(result), name


class TestTheStderrModulationLedgerIsReadable:
    """The transient ledger demangles, like every other human output.

    Plan slice 7o made demangling a property of the output being human, not
    a flag -- and retired the flag. This ledger kept interpolating the raw
    mangled symbol, so the same run's report read `lib::gone(int)
    [_ZN3lib4goneEi]` while its stderr read `_ZN3lib4goneEi`, with no
    surviving control to make them agree (Codex review, PR #1284).
    """

    def _rendered(self, *, edges=("_ZN3lib6callerEv -> _ZN3lib4goneEi",)):
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_types import DiffResult
        from abicheck.cli_audit import render_pattern_modulations
        from abicheck.pattern_verdicts import PatternModulation

        result = DiffResult(
            old_version="1",
            new_version="2",
            library="l",
            changes=[],
            verdict=Verdict.COMPATIBLE,
        )
        result.pattern_modulations = [
            PatternModulation(
                symbol="_ZN3lib4goneEi",
                original_category="abi_breaking",
                new_category="quality",
                rule_id="r1",
                reason="internal",
                evidence_tier="header",
                edges_matched=edges,
            ).to_dict()
        ]
        return render_pattern_modulations(result)

    def test_the_symbol_is_readable(self):
        assert "lib::gone(int)" in self._rendered()

    def test_the_mangled_spelling_is_kept_beside_it(self):
        """`demangle_text`'s annotation form: the exact spelling a user greps
        for must survive."""
        assert "_ZN3lib4goneEi" in self._rendered()

    def test_the_matched_edges_demangle_too(self):
        """Whole-section pass, not per-symbol: the edges are the evidence the
        rule fired on, and were raw for the same reason."""
        text = self._rendered()
        assert "lib::caller()" in text, text

    def test_a_run_with_no_modulation_says_so_plainly(self):
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_types import DiffResult
        from abicheck.cli_audit import render_pattern_modulations

        clean = DiffResult(
            old_version="1",
            new_version="2",
            library="l",
            changes=[],
            verdict=Verdict.COMPATIBLE,
        )
        assert render_pattern_modulations(clean).strip() == (
            "No pattern-aware modulations applied."
        )
