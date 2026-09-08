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
``--no-demangle``/``--explain-patterns``), ``--audit-suppressions``, or
``--show-filtered``.

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
into the canonical result, exactly as ``--surface-metrics`` always did. The
flag is accepted for backward compatibility and is a no-op. Because this
changes the *canonical* result itself (by design -- it is no longer
presentation-gated, it is now always-on analysis), the F-19 fixture below is
built so its own public-symbol count does not change (one function removed,
one function of the same kind added elsewhere is avoided in favor of a pair
that nets to a stable public count) -- see ``_write_pair``'s own docstring.
This keeps the *remaining* rendering-only flags exercised here (--view
tokens, --audit-suppressions, --show-filtered) provably inert on top of an
already-fixed set of AUTO-computed findings, which is the post-Phase-5
invariant this file states.
"""

from __future__ import annotations

import itertools
import json
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
                "--format",
                fmt,
                "--view",
                view_demangle,
                "--suppress",
                str(suppress),
                "--view",
                view_mode,
            ]
            if audit_flag:
                args.append("--audit-suppressions")
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
                "--format",
                "json",
                "--view",
                view_demangle,
                "--suppress",
                str(suppress),
            ]
            if audit_flag:
                args.append("--audit-suppressions")
            if view_patterns:
                args += ["--view", "patterns"]
            result = CliRunner().invoke(main, args)
            assert result.exit_code == 4, result.output
            payloads.append(_canonical_facts(json.loads(result.stdout)))

        first = payloads[0]
        for other in payloads[1:]:
            assert other == first

        # ADR-068 D4/Phase 5: suppression_audit is unconditional now -- it
        # must be present (and identical) whether or not --audit-suppressions
        # was passed, proving computation is no longer gated by the flag.
        assert first["suppression_audit"] is not None
        assert first["suppression_audit"]["stale_rules"] == [
            "workaround (symbol=never_matches_anything)"
        ]

    def test_write_is_repeatable_and_does_not_change_the_primary_result(
        self, tmp_path: Path
    ) -> None:
        old_p, new_p = _write_pair(tmp_path)

        baseline = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--format", "json"]
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
                "--format",
                "json",
                "--write",
                f"markdown={out_md}",
                "--write",
                f"sarif={out_sarif}",
            ],
        )
        assert result.exit_code == 4, result.output
        assert _canonical_facts(json.loads(result.stdout)) == baseline_facts
        assert out_md.exists() and out_sarif.exists()
        assert "libfoo.so.1" in out_md.read_text()

    def test_show_filtered_flag_does_not_change_canonical_result(
        self, tmp_path: Path
    ) -> None:
        """ADR-067 S1: the disposition/out-of-surface ledger is already
        unconditional -- --show-filtered only ever gates whether the
        markdown/text rendering echoes it. Confirmed here at the JSON
        boundary (which always carries out_of_surface_changes/
        suppressed_changes) and at the exit-code boundary."""
        old_p, new_p = _write_pair(tmp_path)

        without = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--format", "json"]
        )
        with_flag = CliRunner().invoke(
            main,
            ["compare", str(old_p), str(new_p), "--format", "json", "--show-filtered"],
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
        ):
            result = CliRunner().invoke(main, ["compare", str(old_p), str(new_p), flag])
            assert result.exit_code == 64, (flag, result.output)
            assert "No such option" in result.output, (flag, result.output)

    def test_view_patterns_does_not_change_verdict_or_exit_code(
        self, tmp_path: Path
    ) -> None:
        old_p, new_p = _write_pair(tmp_path)

        without = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--format", "json"]
        )
        with_patterns = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--format",
                "json",
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
        ``surface_metrics=True``, Phase 5's second AUTO flag) regardless of
        whether ``--view patterns`` was given."""
        import abicheck.service as service_module

        old_p, new_p = _write_pair(tmp_path)
        captured: list[dict[str, Any]] = []
        real_compare_snapshots = service_module.compare_snapshots

        def _spy(*args: Any, **kwargs: Any) -> Any:
            captured.append(kwargs)
            return real_compare_snapshots(*args, **kwargs)

        monkeypatch.setattr(service_module, "compare_snapshots", _spy)

        for view_patterns in (False, True):
            args = ["compare", str(old_p), str(new_p), "--format", "json"]
            if view_patterns:
                args += ["--view", "patterns"]
            result = CliRunner().invoke(main, args)
            assert result.exit_code == 4, result.output

        assert len(captured) == 2
        for kwargs in captured:
            assert kwargs["pattern_verdicts"] is True
            assert kwargs["surface_metrics"] is True


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
                "compare", str(old_p), str(new_p),
                "--format", "markdown", "--view", "show=removed",
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
                "compare", str(old_p), str(new_p), "--format", "markdown",
                "--view", "show=removed", "--view", "show=added",
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
                "compare", str(old_p), str(new_p), "--format", "markdown",
                "--view", "show=breaking", "--view", "show=added",
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
            main, ["compare", str(old_p), str(new_p), "--format", "json"]
        )
        combined = CliRunner().invoke(
            main,
            [
                "compare", str(old_p), str(new_p), "--format", "json",
                "--view", "leaf", "--view", "demangle", "--view", "patterns",
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
            main, ["compare", str(old_p), str(new_p), "--format", "json"]
        )
        narrowed = CliRunner().invoke(
            main,
            [
                "compare", str(old_p), str(new_p), "--format", "json",
                "--view", "show=breaking",
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


class TestSurfaceMetricsFlagIsVestigial:
    """ADR-068 D4/Phase 5: --surface-metrics computation is unconditional
    now (§4.1's AUTO classification) -- the flag itself no longer gates
    anything, so passing it or not must never change the canonical result.
    Uses a fixture whose public-function count actually changes (unlike
    ``_write_pair`` above, which is deliberately net-zero), so a
    PUBLIC_SURFACE_SHRANK finding is already present in the baseline; the
    flag's own presence/absence must not add, remove, or otherwise alter
    it."""

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

    def test_flag_present_or_absent_is_byte_identical(self, tmp_path: Path) -> None:
        old_p, new_p = self._shrinking_pair(tmp_path)
        without = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--format", "json"]
        )
        with_flag = CliRunner().invoke(
            main,
            ["compare", str(old_p), str(new_p), "--format", "json", "--surface-metrics"],
        )
        assert without.exit_code == with_flag.exit_code == 4
        assert without.stdout == with_flag.stdout

    def test_public_surface_shrank_is_present_without_the_flag(
        self, tmp_path: Path
    ) -> None:
        old_p, new_p = self._shrinking_pair(tmp_path)
        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--format", "json"]
        )
        assert result.exit_code == 4, result.output
        kinds = {c["kind"] for c in json.loads(result.stdout)["changes"]}
        assert "public_surface_shrank" in kinds


class TestAuditSuppressionsNoOpWithoutSuppress:
    """ADR-068 D4/Phase 5: --audit-suppressions with no --suppress is a
    no-op (nothing to audit), not a usage error -- a rendering-only flag
    must never reject an otherwise-valid invocation."""

    def test_no_suppress_is_not_rejected(self, tmp_path: Path) -> None:
        old_p, new_p = _write_pair(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "compare", str(old_p), str(new_p), "--format", "json",
                "--audit-suppressions",
            ],
        )
        assert result.exit_code == 4, result.output
        assert json.loads(result.stdout).get("suppression_audit") is None
