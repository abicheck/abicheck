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

"""ADR-068 D4 / one-comparison-product.md §7 F-19/F-20: presentation must
never change analysis.

The invariant, stated so it can be tested: for a fixed set of operands,
evidence inputs, and policy/contract configuration, the canonical result --
facts, compatibility verdict, assurance, disposition ledger, gate decision,
and exit code -- is byte-identical regardless of ``--format``, ``--write``,
demangling, or any rendering filter.

F-19 covers the general invariant across rendering permutations. F-20 is
the targeted regression: before this phase, ``cli_compare_helpers.run_compare``
computed ``apply_patterns = pattern_verdicts or explain_patterns`` -- asking
*why* a pattern-verdict modulation happened (``--explain-patterns``) also
silently turned modulation *on*, which could change the reported verdict and
exit code. ``--pattern-verdicts`` is now removed entirely (modulation is
unconditional, evidence-gated analysis), and ``--explain-patterns`` is pure
rendering -- it can never again decide *whether* modulation happened, only
whether its evidence is *shown*. ``--surface-metrics`` stays a real,
independent opt-in flag (see ``adr027_compare_options``'s own docstring for
why it wasn't folded into the same automatic treatment this phase) and is
untouched by either fix.
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
    a COMPATIBLE addition -- enough surface for --show-only/--report-mode to
    have something to filter, and for the suppression-audit fixture below to
    have a real stale rule to report."""
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
    demangle/rendering-filter permutations; the exit code is identical too."""

    def test_exit_code_identical_across_every_rendering_permutation(
        self, tmp_path: Path
    ) -> None:
        old_p, new_p = _write_pair(tmp_path)
        suppress = _write_suppression(tmp_path)

        exit_codes: set[int] = set()
        for (
            fmt,
            demangle_flag,
            audit_flag,
            explain_flag,
            report_mode,
        ) in itertools.product(
            ("json", "markdown", "sarif", "html", "junit", "review"),
            ("--demangle", "--no-demangle"),
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
                demangle_flag,
                "--suppress",
                str(suppress),
                "--report-mode",
                report_mode,
            ]
            if audit_flag:
                args.append("--audit-suppressions")
            if explain_flag:
                args.append("--explain-patterns")
            result = CliRunner().invoke(main, args)
            assert result.exit_code in (0, 4), (fmt, result.output)
            exit_codes.add(result.exit_code)

        # One real BREAKING removal: every permutation above must agree on
        # the same exit code -- none of format/demangle/audit-suppressions/
        # explain-patterns/report-mode may change *whether* the run gates.
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
        for demangle_flag, audit_flag, explain_flag in itertools.product(
            ("--demangle", "--no-demangle"), (True, False), (True, False)
        ):
            args = [
                "compare",
                str(old_p),
                str(new_p),
                "--format",
                "json",
                demangle_flag,
                "--suppress",
                str(suppress),
            ]
            if audit_flag:
                args.append("--audit-suppressions")
            if explain_flag:
                args.append("--explain-patterns")
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


class TestF20ExplainPatternsNeverChangesAnalysis:
    """F-20: the specific fixed bug. Before this phase,
    ``apply_patterns = pattern_verdicts or explain_patterns`` meant
    ``--explain-patterns`` alone silently turned pattern-verdict modulation
    on, which could change the verdict and exit code."""

    def test_removed_flags_exit_64(self, tmp_path: Path) -> None:
        old_p, new_p = _write_pair(tmp_path)
        for flag in ("--pattern-verdicts", "--no-pattern-verdicts"):
            result = CliRunner().invoke(main, ["compare", str(old_p), str(new_p), flag])
            assert result.exit_code == 64, (flag, result.output)
            assert "No such option" in result.output, (flag, result.output)

    def test_explain_patterns_does_not_change_verdict_or_exit_code(
        self, tmp_path: Path
    ) -> None:
        old_p, new_p = _write_pair(tmp_path)

        without = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--format", "json"]
        )
        with_explain = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--format",
                "json",
                "--explain-patterns",
            ],
        )
        assert without.exit_code == with_explain.exit_code == 4
        payload_without = json.loads(without.stdout)
        payload_with = json.loads(with_explain.stdout)
        assert payload_without["verdict"] == payload_with["verdict"]
        assert _canonical_facts(payload_without) == _canonical_facts(payload_with)
        # --explain-patterns' only visible effect is on stderr, never stdout.
        assert without.stdout == with_explain.stdout

    def test_pattern_verdicts_is_unconditional_on_compare_snapshots(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The most direct proof of the fix: patches the exact chokepoint
        (``service.compare_snapshots``, imported by name inside
        ``cli_compare_helpers.run_compare``) that used to receive
        ``pattern_verdicts=apply_patterns`` -- the historically buggy
        ``pattern_verdicts or explain_patterns`` expression -- and asserts
        it is now called with ``pattern_verdicts=True`` regardless of
        whether ``--explain-patterns`` was given."""
        import abicheck.service as service_module

        old_p, new_p = _write_pair(tmp_path)
        captured: list[dict[str, Any]] = []
        real_compare_snapshots = service_module.compare_snapshots

        def _spy(*args: Any, **kwargs: Any) -> Any:
            captured.append(kwargs)
            return real_compare_snapshots(*args, **kwargs)

        monkeypatch.setattr(service_module, "compare_snapshots", _spy)

        for explain_flag in (False, True):
            args = ["compare", str(old_p), str(new_p), "--format", "json"]
            if explain_flag:
                args.append("--explain-patterns")
            result = CliRunner().invoke(main, args)
            assert result.exit_code == 4, result.output

        assert len(captured) == 2
        for kwargs in captured:
            assert kwargs["pattern_verdicts"] is True
