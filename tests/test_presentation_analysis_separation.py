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

"""ADR-068 D4 / one-comparison-product.md Sec 4.1/Sec 6 Phase 5: presentation
must never change analysis.

The invariant, stated so it can be tested: for a fixed set of operands,
evidence inputs, and policy/contract configuration, the canonical result --
facts, compatibility verdict, assurance, disposition ledger, gate decision,
and exit code -- is byte-identical regardless of ``--format``, ``--write``,
``--view`` (which collapses ``--report-mode``/``--show-only``/``--demangle``/
``--no-demangle``/``--explain-patterns``, and -- as of this phase's closing
slice -- ``--show-filtered`` and ``--audit-suppressions`` too, as
``--view filtered``/``--view suppressions``).

F-19 covers the general invariant across rendering permutations. F-20 is
the targeted regression: before Phase 5, ``cli_compare_helpers.run_compare``
computed ``apply_patterns = pattern_verdicts or explain_patterns`` -- asking
*why* a pattern-verdict modulation happened (``--explain-patterns``, now
``--view patterns``) also silently turned modulation *on*, which could
change the reported verdict and exit code. ``--pattern-verdicts`` is removed
entirely (modulation is unconditional, evidence-gated analysis), and
``--view patterns`` is pure rendering -- it can never again decide *whether*
modulation happened, only whether its evidence is *shown*.

Also this phase (§4.1's AUTO classification, made unconditional here):
``--surface-metrics`` computation now always runs -- every comparison always
computes the ADR-027 public-surface metric-drift findings and merges them
into the canonical result, exactly as ``--surface-metrics`` always did -- and
the flag itself is *gone*, not accepted-and-ignored (D5: an accepted spelling
is still public surface). Because this changes the *canonical* result itself
(by design -- it is no longer presentation-gated, it is now always-on
analysis), the F-19 fixture below is built so its own public-symbol count does
not change (one function removed, one function of the same kind added
elsewhere is avoided in favor of a pair that nets to a stable public count) --
see ``_write_pair``'s own docstring. This keeps the *remaining*
rendering-only selectors exercised here (every ``--view`` token) provably
inert on top of an already-fixed set of AUTO-computed findings, which is the
post-Phase-5 invariant this file states.
"""

from __future__ import annotations

import itertools
import json
import os
import random
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


def _write_pair(tmp_path: Path) -> tuple[Path, Path]:
    """A real, unambiguous BREAKING change (a public function removed) plus
    a COMPATIBLE addition of another public function -- enough surface for
    --view show=.../--report-mode-equivalent tokens to have something to
    filter, and for the suppression-audit fixture below to have a real
    stale rule to report.

    Deliberately net-zero on public function count (one removed, one
    added) so the always-on ADR-027 surface-metrics roll-up
    (PUBLIC_SURFACE_GREW/SHRANK) does not fire here -- this file's subject
    is whether *rendering* flags change the canonical result, and a fixture
    whose own public-surface delta is zero keeps that result's shape
    stable regardless of whether surface-metrics review is ever revisited
    again, rather than pinning today's exact roll-up finding as if it were
    what this file is testing.
    """
    old = AbiSnapshot(
        library="libfoo.so.1",
        version="1.0",
        functions=[
            Function(
                name="api_removed",
                mangled="_Z11api_removedv",
                return_type="int",
                visibility=Visibility.PUBLIC,
            ),
        ],
        from_headers=True,
    )
    new = AbiSnapshot(
        library="libfoo.so.1",
        version="2.0",
        functions=[
            Function(
                name="api_added",
                mangled="_Z9api_addedv",
                return_type="int",
                visibility=Visibility.PUBLIC,
            ),
        ],
        from_headers=True,
    )
    old_p = tmp_path / "old.json"
    new_p = tmp_path / "new.json"
    old_p.write_text(snapshot_to_json(old), encoding="utf-8")
    new_p.write_text(snapshot_to_json(new), encoding="utf-8")
    return old_p, new_p


def _write_suppression(tmp_path: Path) -> Path:
    """A rule that matches nothing -- a genuine "stale rule" fact for
    --audit-suppressions to report, real evidence that the audit's content
    (not just its presence) stays identical regardless of who asked for it."""
    p = tmp_path / "suppress.yml"
    p.write_text(
        "version: 1\n"
        "suppressions:\n"
        "  - symbol: never_matches_anything\n"
        "    reason: workaround\n",
        encoding="utf-8",
    )
    return p


def _canonical_facts(payload: dict[str, Any]) -> dict[str, Any]:
    """The D4 canonical-result subset of a JSON report: facts (changes,
    sorted so ordering can't masquerade as a difference), verdict,
    assurance, disposition-ledger content, and gate/exit fields. Explicitly
    excludes anything that is *legitimately* a rendering artifact of a
    format-independent axis this test does not vary (there are none here --
    every axis actually varied below is asserted byte-identical)."""
    changes = sorted(
        (c.get("kind"), c.get("symbol"), c.get("severity")) for c in payload["changes"]
    )
    return {
        "verdict": payload.get("verdict"),
        "changes": changes,
        "changes_count": len(payload["changes"]),
        "analysis_assurance": payload.get("analysis_assurance"),
        "suppressed_changes": payload.get("suppressed_changes"),
        "out_of_surface_changes": payload.get("out_of_surface_changes"),
        "suppression_audit": payload.get("suppression_audit"),
        "pattern_modulations": payload.get("pattern_modulations"),
        "exit": payload.get("exit"),
    }


class TestF19OutputFormatInvariance:
    """F-19: the canonical result is byte-identical across --format/--write/
    --view/rendering-filter permutations; the exit code is identical too."""

    def test_exit_code_identical_across_every_rendering_permutation(
        self, tmp_path: Path
    ) -> None:
        old_p, new_p = _write_pair(tmp_path)
        suppress = _write_suppression(tmp_path)

        exit_codes: set[int] = set()
        for (
            fmt,
            view_demangle,
            audit_flag,
            view_patterns,
            view_mode,
        ) in itertools.product(
            ("json", "markdown", "sarif", "html", "junit", "review"),
            ("demangle", "no-demangle"),
            (True, False),
            (True, False),
            ("full", "leaf", "impact", "root-cause"),
        ):
            args = [
                "compare",
                str(old_p),
                str(new_p),
                "-o",
                f"{fmt}=-",
                "--view",
                view_demangle,
                "--suppress",
                str(suppress),
                "--view",
                view_mode,
            ]
            if audit_flag:
                args += ["--view", "suppressions"]
            if view_patterns:
                args += ["--view", "patterns"]
            result = CliRunner().invoke(main, args)
            assert result.exit_code in (0, 4), (fmt, result.output)
            exit_codes.add(result.exit_code)

        # One real BREAKING removal: every permutation above must agree on
        # the same exit code -- none of format/--view tokens/
        # audit-suppressions may change *whether* the run gates.
        assert len(exit_codes) == 1, exit_codes

    def test_json_canonical_facts_identical_across_demangle_audit_explain(
        self, tmp_path: Path
    ) -> None:
        """Held at --format json (the only format whose full structured
        payload is directly comparable) so the comparison is over the real
        canonical fields, not a rendering artifact of format choice itself."""
        old_p, new_p = _write_pair(tmp_path)
        suppress = _write_suppression(tmp_path)

        payloads = []
        for view_demangle, audit_flag, view_patterns in itertools.product(
            ("demangle", "no-demangle"), (True, False), (True, False)
        ):
            args = [
                "compare",
                str(old_p),
                str(new_p),
                "-o",
                "json=-",
                "--view",
                view_demangle,
                "--suppress",
                str(suppress),
            ]
            if audit_flag:
                args += ["--view", "suppressions"]
            if view_patterns:
                args += ["--view", "patterns"]
            result = CliRunner().invoke(main, args)
            assert result.exit_code == 4, result.output
            payloads.append(_canonical_facts(json.loads(result.stdout)))

        first = payloads[0]
        for other in payloads[1:]:
            assert other == first

        # ADR-068 D4/Phase 5: suppression_audit is unconditional now -- it
        # must be present (and identical) whether or not `--view suppressions`
        # was passed, proving computation is no longer gated by the request
        # to render it.
        assert first["suppression_audit"] is not None
        assert first["suppression_audit"]["stale_rules"] == [
            "workaround (symbol=never_matches_anything)"
        ]

    def test_write_is_repeatable_and_does_not_change_the_primary_result(
        self, tmp_path: Path
    ) -> None:
        old_p, new_p = _write_pair(tmp_path)

        baseline = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "-o", "json=-"]
        )
        assert baseline.exit_code == 4, baseline.output
        baseline_facts = _canonical_facts(json.loads(baseline.stdout))

        out_md = tmp_path / "out.md"
        out_sarif = tmp_path / "out.sarif"
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "-o",
                "json=-",
                "-o",
                f"markdown={out_md}",
                "-o",
                f"sarif={out_sarif}",
            ],
        )
        assert result.exit_code == 4, result.output
        assert _canonical_facts(json.loads(result.stdout)) == baseline_facts
        assert out_md.exists() and out_sarif.exists()
        assert "libfoo.so.1" in out_md.read_text(encoding="utf-8")

    def test_view_filtered_token_does_not_change_canonical_result(
        self, tmp_path: Path
    ) -> None:
        """ADR-067 S1: the disposition/out-of-surface ledger is already
        unconditional -- the retired ``--show-filtered``, now ``--view
        filtered``, only ever gates whether the markdown/text rendering
        echoes it. Confirmed here at the JSON boundary (which always carries
        out_of_surface_changes/suppressed_changes) and at the exit-code
        boundary."""
        old_p, new_p = _write_pair(tmp_path)

        without = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "-o", "json=-"]
        )
        with_flag = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "-o",
                "json=-",
                "--view",
                "filtered",
            ],
        )
        assert without.exit_code == with_flag.exit_code == 4
        assert _canonical_facts(json.loads(without.stdout)) == _canonical_facts(
            json.loads(with_flag.stdout)
        )


class TestF20ExplainPatternsNeverChangesAnalysis:
    """F-20: the specific fixed bug. Before Phase 5,
    ``apply_patterns = pattern_verdicts or explain_patterns`` meant
    ``--explain-patterns`` alone silently turned pattern-verdict modulation
    on, which could change the verdict and exit code. ``--explain-patterns``
    is now ``--view patterns``."""

    def test_removed_flags_exit_64(self, tmp_path: Path) -> None:
        old_p, new_p = _write_pair(tmp_path)
        for flag in (
            "--pattern-verdicts",
            "--no-pattern-verdicts",
            "--report-mode",
            "--show-only",
            "--demangle",
            "--no-demangle",
            "--explain-patterns",
            # This phase's three (one-comparison-product.md Phase 5's
            # remaining AUTO rows), on the identical terms: removed
            # outright, no hidden alias, no deprecation window (F-27).
            "--surface-metrics",
            "--show-filtered",
            "--audit-suppressions",
        ):
            result = CliRunner().invoke(main, ["compare", str(old_p), str(new_p), flag])
            assert result.exit_code == 64, (flag, result.output)
            assert "No such option" in result.output, (flag, result.output)

    def test_view_patterns_does_not_change_verdict_or_exit_code(
        self, tmp_path: Path
    ) -> None:
        old_p, new_p = _write_pair(tmp_path)

        without = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "-o", "json=-"]
        )
        with_patterns = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "-o",
                "json=-",
                "--view",
                "patterns",
            ],
        )
        assert without.exit_code == with_patterns.exit_code == 4
        payload_without = json.loads(without.stdout)
        payload_with = json.loads(with_patterns.stdout)
        assert payload_without["verdict"] == payload_with["verdict"]
        assert _canonical_facts(payload_without) == _canonical_facts(payload_with)
        # --view patterns' only visible effect is on stderr, never stdout.
        assert without.stdout == with_patterns.stdout

    def test_pattern_verdicts_is_unconditional_on_compare_snapshots(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The most direct proof of the fix: patches the exact chokepoint
        (``service.compare_snapshots``, imported by name inside
        ``cli_compare_helpers.run_compare``) that used to receive
        ``pattern_verdicts=apply_patterns`` -- the historically buggy
        ``pattern_verdicts or explain_patterns`` expression -- and asserts
        it is now called with ``pattern_verdicts=True`` (and
        without a `surface_metrics` keyword at all -- Phase 5's second AUTO
        row went all the way to removing the parameter from the Tier-2 verb,
        which forces it on for every caller) regardless of whether
        ``--view patterns`` was given."""
        import abicheck.service as service_module

        old_p, new_p = _write_pair(tmp_path)
        captured: list[dict[str, Any]] = []
        real_compare_snapshots = service_module.compare_snapshots

        def _spy(*args: Any, **kwargs: Any) -> Any:
            captured.append(kwargs)
            return real_compare_snapshots(*args, **kwargs)

        monkeypatch.setattr(service_module, "compare_snapshots", _spy)

        for view_patterns in (False, True):
            args = ["compare", str(old_p), str(new_p), "-o", "json=-"]
            if view_patterns:
                args += ["--view", "patterns"]
            result = CliRunner().invoke(main, args)
            assert result.exit_code == 4, result.output

        assert len(captured) == 2
        for kwargs in captured:
            assert kwargs["pattern_verdicts"] is True
            # Phase 5's closing slice went one step further than the
            # forced-`True` this line used to assert: `surface_metrics` (and
            # ADR-039's `reconcile_build_context`) are not parameters of the
            # Tier-2 verb at all any more -- `compare_snapshots` forces both
            # on for every caller, so there is no keyword left for a caller
            # to get wrong. Assert the *absence*, which is the stronger
            # property: a re-introduced parameter would fail here.
            assert "surface_metrics" not in kwargs
            assert "reconcile_build_context" not in kwargs


class TestViewGrammar:
    """The --view consolidation itself (one-comparison-product.md §4.1's
    MERGE row): every capability the four retired flags provided must
    remain expressible, and combining tokens must not change the canonical
    result relative to any single token alone."""

    def test_show_token_is_a_lossless_rename_of_show_only(self, tmp_path: Path) -> None:
        old_p, new_p = _write_pair(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "-o",
                "markdown=-",
                "--view",
                "show=removed",
            ],
        )
        assert result.exit_code == 4, result.output
        assert "api_removed" in result.output
        assert "api_added" not in result.output

    def test_show_tokens_across_two_view_occurrences_are_ored_together(
        self, tmp_path: Path
    ) -> None:
        # Both `show=removed` and `show=added` are the *same* dimension
        # (action) -- a comma-joined single AND-group would still pass this
        # case (comma is also the legitimate OR-within-dimension separator),
        # so this alone does not prove groups from separate `--view show=...`
        # occurrences are ORed together rather than collapsed into one
        # AND-group. See the cross-dimension test right below for that.
        old_p, new_p = _write_pair(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "-o",
                "markdown=-",
                "--view",
                "show=removed",
                "--view",
                "show=added",
            ],
        )
        assert result.exit_code == 4, result.output
        assert "api_removed" in result.output
        assert "api_added" in result.output

    def test_show_tokens_across_different_dimensions_are_ored_not_anded(
        self, tmp_path: Path
    ) -> None:
        """CodeRabbit/Codex review, PR #1154: two ``--view show=...``
        occurrences naming *different* dimensions (severity, then action)
        must OR the two groups together -- a finding shown if it matches
        EITHER occurrence's own group -- never collapsed into one AND-group
        the way naively joining with ``,`` would (``ShowOnlyFilter``'s own
        grammar joins different-dimension tokens with AND, so
        ``"breaking,added"`` parsed as one group means "breaking AND
        added", which neither fixture change satisfies: the removal is
        breaking/removed, the addition is compatible/added).

        ``--view show=breaking`` alone matches only the removal (its own
        severity); ``--view show=added`` alone matches only the addition
        (its own action) -- combined, a correct OR-of-groups implementation
        must show both, while the AND-collapse bug this regression targets
        would show neither.
        """
        old_p, new_p = _write_pair(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "-o",
                "markdown=-",
                "--view",
                "show=breaking",
                "--view",
                "show=added",
            ],
        )
        assert result.exit_code == 4, result.output
        assert "api_removed" in result.output
        assert "api_added" in result.output

    def test_unknown_view_token_is_a_usage_error(self, tmp_path: Path) -> None:
        old_p, new_p = _write_pair(tmp_path)
        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--view", "nonsense"]
        )
        assert result.exit_code == 64, result.output
        assert "Unknown --view token" in result.output

    def test_bad_show_filter_is_a_usage_error(self, tmp_path: Path) -> None:
        old_p, new_p = _write_pair(tmp_path)
        result = CliRunner().invoke(
            main,
            ["compare", str(old_p), str(new_p), "--view", "show=not_a_real_token"],
        )
        assert result.exit_code == 64, result.output
        assert "Unknown --show-only token" in result.output

    def test_combined_view_tokens_do_not_change_canonical_result(
        self, tmp_path: Path
    ) -> None:
        """``show=...`` is deliberately excluded from this equality check --
        like the pre-existing ``--show-only`` it replaces, it genuinely
        narrows the *displayed* ``changes[]`` array (documented as "does
        not affect exit codes", never claimed to leave the array
        byte-identical); ``leaf``/``demangle``/``patterns`` change no
        content at all, only how it is grouped/spelled/explained, so those
        three combined must still leave the JSON payload untouched."""
        old_p, new_p = _write_pair(tmp_path)
        baseline = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "-o", "json=-"]
        )
        combined = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "-o",
                "json=-",
                "--view",
                "leaf",
                "--view",
                "demangle",
                "--view",
                "patterns",
            ],
        )
        assert baseline.exit_code == combined.exit_code == 4
        assert _canonical_facts(json.loads(baseline.stdout)) == _canonical_facts(
            json.loads(combined.stdout)
        )

    def test_show_token_narrows_display_but_not_verdict_or_exit_code(
        self, tmp_path: Path
    ) -> None:
        """``--view show=...`` (like the ``--show-only`` it replaces) is
        documented to narrow what is *displayed*, never the underlying
        compatibility verdict or exit code -- the actual D4 guarantee for
        this one token, distinct from the content-preserving tokens above."""
        old_p, new_p = _write_pair(tmp_path)
        baseline = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "-o", "json=-"]
        )
        narrowed = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "-o",
                "json=-",
                "--view",
                "show=breaking",
            ],
        )
        assert baseline.exit_code == narrowed.exit_code == 4
        base_payload = json.loads(baseline.stdout)
        narrow_payload = json.loads(narrowed.stdout)
        assert base_payload["verdict"] == narrow_payload["verdict"]
        assert base_payload["exit"] == narrow_payload["exit"]
        # The addition is filtered out; the removal (the only "breaking"
        # entry) survives -- proof the token actually narrowed the display.
        assert len(narrow_payload["changes"]) == 1
        assert narrow_payload["changes"][0]["kind"] == "func_removed"


class TestShowOnlyCliHintIsReRunnable:
    """Codex review (PR #1154 second follow-up: "Render repeated show
    groups as repeated view options"): the rendered "Filtered by" hint
    used to embed the internal ``;``-separated transport form directly
    (``--view show=breaking;functions``) -- not a real invocation
    (``ShowOnlyFilter.parse`` rejects the raw value as one malformed
    token, and an unquoted shell treats ``;`` as a command separator).
    This proves the fix: with two different-dimension ``--view show=...``
    groups active, the rendered hint is a *literally* re-runnable
    ``--view`` invocation that reproduces the identical filtered result.
    """

    def test_markdown_hint_round_trips_through_the_real_view_parser(
        self, tmp_path: Path
    ) -> None:
        import re

        from abicheck.frontends.cli.options.view import parse_view_tokens

        old_p, new_p = _write_pair(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "-o",
                "markdown=-",
                "--view",
                "show=breaking",
                "--view",
                "show=added",
            ],
        )
        assert result.exit_code == 4, result.output
        hint_line = next(
            line for line in result.output.splitlines() if line.startswith("> Filtered by:")
        )
        # No internal transport separator ever reaches rendered text.
        assert ";" not in hint_line
        tokens = tuple(re.findall(r"--view (\S+?)(?=[`\s]|$)", hint_line))
        assert tokens.count("show=breaking") == 1
        assert tokens.count("show=added") == 1
        parsed = parse_view_tokens(tokens)
        # Re-running with exactly the rendered tokens reproduces the same
        # filtered display the original combined invocation produced.
        rerun = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "-o",
                "markdown=-",
                *[f"--view={t}" for t in tokens],
            ],
        )
        assert rerun.exit_code == result.exit_code
        assert parsed["show_only"] == "breaking;added"
        assert "api_removed" in rerun.output
        assert "api_added" in rerun.output

    def test_html_hint_has_no_internal_separator(self, tmp_path: Path) -> None:
        old_p, new_p = _write_pair(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "-o",
                "html=-",
                "--view",
                "show=breaking",
                "--view",
                "show=added",
            ],
        )
        assert result.exit_code == 4, result.output
        assert "Filtered by" in result.output
        filter_section = result.output[result.output.index("Filtered by") :]
        filter_section = filter_section[: filter_section.index("</div>")]
        assert ";" not in filter_section
        assert filter_section.count("--view show=") == 2

    def test_html_empty_filter_state_hint_has_no_internal_separator(
        self, tmp_path: Path
    ) -> None:
        """The HTML "No changes match the current filter" empty-state note
        has the identical raw-separator problem the "Filtered by" note does
        (Codex review: "the alternate Markdown and HTML filter notes have
        the same problem") -- two different-dimension groups, neither of
        which matches anything in the fixture, exercises it."""
        old_p, new_p = _write_pair(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "-o",
                "html=-",
                "--view",
                "show=breaking,variables",
                "--view",
                "show=compatible,variables",
            ],
        )
        assert result.exit_code == 4, result.output
        assert "No changes match the current filter" in result.output
        note_section = result.output[
            result.output.index("No changes match the current filter") :
        ]
        note_section = note_section[: note_section.index("</p>")]
        assert ";" not in note_section
        assert note_section.count("--view show=") == 2


class TestSurfaceMetricsAreUnconditional:
    """ADR-068 D4/Phase 5: ADR-027's public-surface metric drift is
    unconditional (§4.1's AUTO classification), and ``--surface-metrics``
    is now *gone* rather than accepted-and-ignored -- an accepted spelling
    is still public surface (D5), so the closing slice of this phase
    deleted it. Uses a fixture whose public-function count actually changes
    (unlike ``_write_pair`` above, which is deliberately net-zero), so a
    PUBLIC_SURFACE_SHRANK finding must be present with no flag at all."""

    @staticmethod
    def _shrinking_pair(tmp_path: Path) -> tuple[Path, Path]:
        old = AbiSnapshot(
            library="libfoo.so.1",
            version="1.0",
            functions=[
                Function(
                    name="api_removed",
                    mangled="_Z11api_removedv",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                ),
            ],
            from_headers=True,
        )
        new = AbiSnapshot(library="libfoo.so.1", version="2.0", from_headers=True)
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        old_p.write_text(snapshot_to_json(old), encoding="utf-8")
        new_p.write_text(snapshot_to_json(new), encoding="utf-8")
        return old_p, new_p

    def test_removed_flag_is_a_usage_error_not_a_silent_no_op(
        self, tmp_path: Path
    ) -> None:
        old_p, new_p = self._shrinking_pair(tmp_path)
        result = CliRunner().invoke(
            main,
            ["compare", str(old_p), str(new_p), "-o", "json=-", "--surface-metrics"],
        )
        assert result.exit_code == 64, result.output
        assert "No such option" in result.output

    def test_public_surface_shrank_is_present_without_the_flag(
        self, tmp_path: Path
    ) -> None:
        old_p, new_p = self._shrinking_pair(tmp_path)
        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "-o", "json=-"]
        )
        assert result.exit_code == 4, result.output
        kinds = {c["kind"] for c in json.loads(result.stdout)["changes"]}
        assert "public_surface_shrank" in kinds


class TestAuditSuppressionsNoOpWithoutSuppress:
    """ADR-068 D4/Phase 5: asking for the suppression-audit *render* with no
    --suppress is a no-op (nothing to audit), not a usage error -- a
    rendering selector must never reject an otherwise-valid invocation.
    ``--audit-suppressions`` is gone; ``--view suppressions`` is the
    spelling, and it inherits the same rule."""

    def test_no_suppress_is_not_rejected(self, tmp_path: Path) -> None:
        old_p, new_p = _write_pair(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "-o",
                "json=-",
                "--view",
                "suppressions",
            ],
        )
        assert result.exit_code == 4, result.output
        assert json.loads(result.stdout).get("suppression_audit") is None


# ── F-19/F-16: the invariant, over the whole permutation space ───────────────
#
# one-comparison-product.md Phase 5's own definition of done ("the canonical
# result block is byte-identical across every rendering permutation", which
# that section numbers F-16 while §7's acceptance table numbers the same
# requirement F-19). What the class below replaces mattered enough to record:
# the original F-19 tests exercised a *sample* -- 6 formats x 2 demangle
# states x 2 audit flags x 2 pattern flags x 4 report modes, for the exit code
# only, plus 8 hand-listed combinations at `--format json` for the result
# block -- and checked that block with `_canonical_facts`, a projection of the
# very JSON report the implementation had just rendered. Neither half is an
# invariant test: the axes were not fully crossed (no `--write` axis at all,
# no `show=`, no format x view crossing for the *content*), and the oracle was
# the implementation's own output compared against itself under a different
# flag, which cannot catch a change that moves every rendering in the same
# wrong direction.
#
# Both halves are fixed here (AGENTS.md, "A bug fix's regression test targets
# the bug *class*, not the one reported input"):
#
# 1. **The space is the full product**, enumerated by `_permutations()`:
#    7 `--format` values x 4 `--view` report modes x 3 demangle states
#    (unset/demangle/no-demangle) x 2 `--view patterns` x 2 `--view filtered`
#    x 2 `--view suppressions` x 3 `--view show=` states x 3 extra `--write`
#    sets = **6048** invocations. The `slow`-marked test runs every one of
#    them; the default-lane test runs a seeded *random* sample of that same
#    space rather than a hand-picked list, so the fast suite still searches
#    the space instead of re-checking one corner of it.
# 2. **The oracle is not the report projection.** It is computed by calling
#    the Tier-2 engine verb (`workflows.compare_policy.compare_snapshots`)
#    directly on the two snapshots and reading the canonical facts off the
#    returned `DiffResult` object -- a different code path from
#    `report/render_json.py`, which is what every CLI invocation below goes
#    through. A renderer that dropped, reordered or re-severitied a finding
#    identically in all 6048 renderings would still fail against it.
_FORMATS: tuple[str, ...] = (
    "json", "markdown", "sarif", "html", "junit", "review", "oneline",
)
_REPORT_MODES: tuple[str, ...] = ("full", "leaf", "impact", "root-cause")
_DEMANGLE: tuple[str | None, ...] = (None, "demangle", "no-demangle")
_SHOW: tuple[str | None, ...] = (None, "show=breaking", "show=added")
#: Extra `--write` artifacts *beyond* the json extractor every case adds.
_EXTRA_WRITES: tuple[tuple[str, ...], ...] = ((), ("markdown",), ("sarif", "junit"))

#: The full space, as a product of the axes above.
_PERMUTATION_SPACE_SIZE = 6048


def _permutations() -> list[tuple[Any, ...]]:
    """The complete ``--format`` x ``--view`` x ``--write`` space."""
    return list(
        itertools.product(
            _FORMATS,
            _REPORT_MODES,
            _DEMANGLE,
            (False, True),   # --view patterns
            (False, True),   # --view filtered
            (False, True),   # --view suppressions
            _SHOW,
            _EXTRA_WRITES,
        )
    )


def _engine_oracle(old_p: Path, new_p: Path, suppress: Path) -> dict[str, Any]:
    """The canonical result, computed *without* the report projection.

    Loads the same two snapshots the CLI will and runs the documented
    Tier-2 verb over them, then reads the facts straight off the returned
    ``DiffResult``. Deliberately not ``_canonical_facts`` over a rendered
    report: an oracle that re-uses the code under test can only prove the
    renderings agree with each other, never that they agree with what was
    analyzed (AGENTS.md's "against a stated oracle that is not the same
    formula/helper the implementation itself uses").
    """
    from abicheck.serialization import load_snapshot
    from abicheck.workflows.compare_policy import compare_snapshots
    from abicheck.workflows.suppression import SuppressionList

    result = compare_snapshots(
        load_snapshot(str(old_p)),
        load_snapshot(str(new_p)),
        suppression=SuppressionList.load(suppress),
    )
    return {
        "verdict": result.verdict.value,
        "changes": sorted((c.kind.value, c.symbol) for c in result.changes),
        "changes_count": len(result.changes),
        "suppressed_count": len(result.suppressed_changes),
        "out_of_surface_count": len(result.out_of_surface_changes),
        "assurance_status": result.analysis_assurance.status,
    }


def _facts_from_report(payload: dict[str, Any]) -> dict[str, Any]:
    """The same six canonical facts, read off a rendered JSON report."""
    return {
        "verdict": payload.get("verdict"),
        "changes": sorted((c["kind"], c.get("symbol")) for c in payload["changes"]),
        "changes_count": len(payload["changes"]),
        "suppressed_count": len(payload.get("suppressed_changes") or ()),
        "out_of_surface_count": len(payload.get("out_of_surface_changes") or ()),
        "assurance_status": (payload.get("analysis_assurance") or {}).get("status"),
    }


def _run_permutation(
    case: tuple[Any, ...], old_p: Path, new_p: Path, suppress: Path, out_dir: Path
) -> tuple[int, dict[str, Any]]:
    """Run one point of the space; return ``(exit_code, canonical facts)``.

    Every case adds its own ``-o json=<path>`` export -- itself one of the
    axes under test -- so the canonical block can be read back even for a
    format (html/junit/oneline/...) whose own output is not structured. The
    extra exports on top of it are what vary the export-set axis itself.

    Plan slice 7m note: that json export is no longer *contracted
    unfiltered*. Under the one repeatable ``-o FORMAT=DESTINATION`` a
    display selector means the same thing for every export (there is no
    "secondary" to exempt), so a ``--view show=`` case reaches here with a
    narrowed ``changes`` list -- which is why the show axis is asserted
    through its own disclosure below rather than against the unfiltered
    oracle.
    """
    fmt, mode, demangle, patterns, filtered, suppressions, show, extra = case
    extractor = out_dir / "canonical.json"
    args = [
        "compare",
        str(old_p),
        str(new_p),
        "-o",
        f"{fmt}=-",
        "-o",
        f"json={extractor}",
        "--suppress",
        str(suppress),
        "--view",
        mode,
    ]
    for token, enabled in (
        ("patterns", patterns), ("filtered", filtered), ("suppressions", suppressions),
    ):
        if enabled:
            args += ["--view", token]
    for optional in (demangle, show):
        if optional is not None:
            args += ["--view", optional]
    for i, extra_fmt in enumerate(extra):
        args += ["-o", f"{extra_fmt}={out_dir / f'extra{i}.out'}"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 4, (case, result.output, result.exception)
    payload = json.loads(extractor.read_text(encoding="utf-8"))
    return result.exit_code, _facts_from_report(payload)


def _assert_agrees_with_oracle(
    case: tuple[Any, ...], facts: dict[str, Any], oracle: dict[str, Any], payload_path: Path
) -> None:
    """Check one permutation's canonical facts against the engine oracle.

    Every axis but one is a pure presentation choice, so the facts must
    equal the oracle outright. ``--view show=`` is the exception by
    definition -- it is a *display filter*, and since plan slice 7m it
    filters every export uniformly rather than exempting a "secondary" one.
    For those points the invariant is stated as disclosure rather than
    equality: the document names the filter it applied and reports the true
    pre-filter totals, so the full result stays recoverable from the
    filtered document itself. Anything the filter cannot touch (the
    verdict, the suppression and out-of-surface ledgers, the assurance
    status) must still equal the oracle exactly.
    """
    mode, show = case[1], case[6]
    if show is None:
        assert facts == oracle, case
        return
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    if mode == "full":
        # The shared document states the filter it applied and the totals
        # over the true pre-filter pool. `leaf`/`impact`/`root-cause` are
        # their own documents (ADR-061 Phase 2's own scope decision) and
        # carry their own shapes, so the disclosure is required where it is
        # defined rather than asserted everywhere by coincidence.
        assert payload["show_only_filter"] == show.removeprefix("show="), case
        assert (
            payload["filtered_summary"]["total_changes"] == facts["changes_count"]
        ), case
    for unfilterable in (
        "verdict",
        "suppressed_count",
        "out_of_surface_count",
        "assurance_status",
    ):
        assert facts[unfilterable] == oracle[unfilterable], (case, unfilterable)
    # A filter narrows; it never invents. Every displayed change is one the
    # engine really produced.
    assert set(facts["changes"]) <= set(oracle["changes"]), case


class TestF19CanonicalResultIsInvariantOverTheWholeRenderingSpace:
    """F-19 (Phase 5 numbers the same row F-16): for a fixed set of operands,
    evidence and policy, the canonical result and the exit code do not vary
    with ``--format``, ``--view`` or ``--write`` -- checked over the entire
    product of those axes, against an engine-computed oracle."""

    def test_the_permutation_space_is_the_full_product(self) -> None:
        """Guards the enumeration itself: a future axis added to one of the
        tuples above must widen the space, and a silently-shrunk space (the
        exact failure mode the sampled predecessor of this class had) fails
        here rather than passing quietly with less coverage."""
        assert len(_permutations()) == (
            len(_FORMATS)
            * len(_REPORT_MODES)
            * len(_DEMANGLE)
            * 2 * 2 * 2
            * len(_SHOW)
            * len(_EXTRA_WRITES)
        )
        assert len(_permutations()) == _PERMUTATION_SPACE_SIZE

    def test_random_sample_of_the_space_agrees_with_the_engine_oracle(
        self, tmp_path: Path
    ) -> None:
        """The default-lane half: a seeded random sample of the full space.

        Random rather than hand-picked so the cases are chosen without the
        author's own idea of which combinations matter; seeded so a failure
        is reproducible. ``ABICHECK_F19_SEED`` re-seeds it for a soak run
        without editing the test.
        """
        old_p, new_p = _write_pair(tmp_path)
        suppress = _write_suppression(tmp_path)
        oracle = _engine_oracle(old_p, new_p, suppress)

        rng = random.Random(int(os.environ.get("ABICHECK_F19_SEED", "0xF19"), 0))
        out_dir = tmp_path / "out"
        out_dir.mkdir(exist_ok=True)
        for case in rng.sample(_permutations(), 150):
            exit_code, facts = _run_permutation(case, old_p, new_p, suppress, out_dir)
            _assert_agrees_with_oracle(
                case, facts, oracle, out_dir / "canonical.json"
            )
            assert exit_code == 4, case

    @pytest.mark.slow
    def test_every_point_of_the_space_agrees_with_the_engine_oracle(
        self, tmp_path: Path
    ) -> None:
        """The exhaustive half (``slow``): all 6048 points, same oracle."""
        old_p, new_p = _write_pair(tmp_path)
        suppress = _write_suppression(tmp_path)
        oracle = _engine_oracle(old_p, new_p, suppress)

        out_dir = tmp_path / "out"
        out_dir.mkdir(exist_ok=True)
        for case in _permutations():
            exit_code, facts = _run_permutation(case, old_p, new_p, suppress, out_dir)
            _assert_agrees_with_oracle(
                case, facts, oracle, out_dir / "canonical.json"
            )
            assert exit_code == 4, case
