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

"""ADR-049 Phase 7: the contract-coverage exit, applied rather than reported.

Every test here leads with an **exit code**, for the same reason
`test_pack_application.py` does: the contribution has been computed and
reported since Phase 5, so asserting that the number appears in a report
proves nothing about the flip. What is new is that the process exits on it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.checker_types import DiffResult
from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


def _fn(name: str, mangled: str) -> Function:
    return Function(
        name=name, mangled=mangled, return_type="int", visibility=Visibility.PUBLIC
    )


def _write(tmp_path: Path, old: AbiSnapshot, new: AbiSnapshot) -> tuple[Path, Path]:
    old_p = tmp_path / "old.json"
    new_p = tmp_path / "new.json"
    old_p.write_text(snapshot_to_json(old), encoding="utf-8")
    new_p.write_text(snapshot_to_json(new), encoding="utf-8")
    return old_p, new_p


def _compatible_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    """Header-only and unchanged: nothing to find, nothing exported.

    Compatible is the case that isolates the axis. On a breaking pair the
    coverage floor is invisible -- `max(4, 1)` is 4 either way -- so only a
    run whose compatibility axis says 0 can show that the coverage axis is
    the thing moving the exit code.
    """
    common = {"library": "libfoo.so.1", "from_headers": True}
    fns = [_fn("pub_a", "_Z5pub_av")]
    return (
        AbiSnapshot(version="1.0", functions=fns, **common),
        AbiSnapshot(version="2.0", functions=fns, **common),
    )


def _breaking_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    common = {"library": "libfoo.so.1", "from_headers": True}
    return (
        AbiSnapshot(
            version="1.0",
            functions=[_fn("pub_a", "_Z5pub_av"), _fn("pub_b", "_Z5pub_bv")],
            **common,
        ),
        AbiSnapshot(version="2.0", functions=[_fn("pub_a", "_Z5pub_av")], **common),
    )


def _compare(tmp_path: Path, pair, *extra: str):
    old_p, new_p = _write(tmp_path, *pair)
    return CliRunner().invoke(main, ["compare", str(old_p), str(new_p), *extra])


def _compare_result(
    pair: tuple[AbiSnapshot, AbiSnapshot], *, contract_mode: str
) -> DiffResult:
    """The `DiffResult` the CLI would gate on, for the fold-level assertions.

    `fold_coverage_exit`/`_coverage_message` take the result and a base exit,
    so a test of the *fold* needs the former without going through a process
    exit that has already folded it.
    """
    from abicheck import checker

    old, new = pair
    return checker.compare(
        old, new, contract_evaluation=True, contract_mode=contract_mode
    )


class TestTheCoverageExitIsApplied:
    def test_an_unresolvable_domain_exits_1_on_an_otherwise_clean_run(
        self, tmp_path: Path
    ) -> None:
        """The flip itself. `exports` cannot close on a header-only pair, so
        the ledger fails; before Phase 7 the identical invocation exited 0
        while *reporting* a contribution of 1."""
        result = _compare(
            tmp_path,
            _compatible_pair(),
            "--contract",
            "exports",
        )
        assert result.exit_code == 1, result.output

    def test_the_forensic_rollback_domain_still_exits_0(self, tmp_path: Path) -> None:
        """The axis is evidence-driven, not a blanket new failure, and `all`
        is where that matters most: it is Phase 7's stated exact rollback
        (`--contract all` / `--no-scope-public-headers`), so it requires no
        root or closure evidence and therefore cannot be short of any. A
        rollback that could itself fail on coverage would not be one."""
        result = _compare(
            tmp_path, _compatible_pair(), "--contract", "all"
        )
        assert result.exit_code == 0, result.output

    def test_a_run_that_never_asked_the_question_is_untouched(
        self, tmp_path: Path
    ) -> None:
        """No `--contract` means no selected domain to be short of
        evidence for. Inventing a floor there would fail invocations that
        never opted in -- which is every pre-existing one."""
        result = _compare(tmp_path, _compatible_pair())
        assert result.exit_code == 0, result.output

    def test_the_coverage_axis_never_lowers_a_real_abi_break(self) -> None:
        """`max`, not replacement. A coverage failure raising 4 to 1 would
        turn a removed export into "warnings only" -- the axes are
        orthogonal and the compatibility one is more severe when it speaks.

        Asserted at :func:`fold_coverage_exit` over a run whose ledger really
        did fail, rather than end to end. Once contract relevance became
        authoritative (Phase 7) the two conditions stopped co-occurring for
        an entity finding under one domain: a domain short of the evidence
        needed to close is, by construction, also short of what it takes to
        resolve that domain's findings, so the compatibility axis is silent
        in exactly the runs whose ledger fails. The fold is where the claim
        lives, and it holds for any base the compatibility axis hands it.
        """
        from abicheck.contract_coverage_exit import fold_coverage_exit

        result = _compare_result(_compatible_pair(), contract_mode="exports")
        assert fold_coverage_exit(0, result) == 1
        assert fold_coverage_exit(2, result) == 2
        assert fold_coverage_exit(4, result) == 4

    def test_an_unresolvable_break_exits_1_not_4(self, tmp_path: Path) -> None:
        """ADR-049 D1: "uncertainty itself never becomes an ABI break".

        The other half of the pairing above, and the behaviour change Phase 7
        made: a removal the selected domain cannot resolve is
        `UNKNOWN_UNRESOLVED`, so compatibility policy does not score it and
        the run's only nonzero contribution is the coverage axis's own 1 --
        `NOT_CHECKABLE`, not `BREAKING`. The fact itself is conserved: it is
        still in the report, labelled with why it did not gate.
        """
        result = _compare(
            tmp_path,
            _breaking_pair(),
            "--format",
            "json",
            "--contract",
            "exports",
        )
        assert result.exit_code == 1, result.output
        report = json.loads(result.output)
        removals = [c for c in report["changes"] if c["kind"] == "func_removed"]
        assert removals, report["changes"]
        for c in removals:
            assert c["contract_relevance"] == "UNKNOWN_UNRESOLVED"
            assert c["compatibility_evaluation_status"] == "NOT_EVALUATED"
            assert c["compatibility_decision"] is None
            assert c["gate_contribution"] == 0

    def test_the_applied_exit_agrees_with_the_reported_contribution(
        self, tmp_path: Path
    ) -> None:
        """One derivation, two consumers. The report has stated this number
        since Phase 5; the exit status must now be the same answer, or the
        ledger a user reads is not the one that gated them."""
        old_p, new_p = _write(tmp_path, *_compatible_pair())
        out = tmp_path / "report.json"
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--format",
                "json",
                "--contract",
                "exports",
                "-o",
                str(out),
            ],
        )
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["contract_coverage_exit_contribution"] == 1
        assert result.exit_code == report["contract_coverage_exit_contribution"]


class TestTheGatingConditionIsVisible:
    """Only `--format json` carries the ledger. Without a diagnostic, a
    `--format review` run prints "safe to merge" and exits 1 with nothing
    anywhere saying why (Codex review)."""

    @pytest.mark.parametrize("fmt", ["review", "markdown", "sarif", "junit"])
    def test_every_format_without_the_ledger_explains_the_floor(
        self, tmp_path: Path, fmt: str
    ) -> None:
        result = _compare(
            tmp_path,
            _compatible_pair(),
            "--format",
            fmt,
            "--contract",
            "exports",
        )
        assert result.exit_code == 1, result.output
        # Names the providers, not just a count: "old/export_table" is
        # actionable, "2 coverage failures" is not.
        assert "export_table" in result.output, result.output
        # ...and points at the way out, so the message is actionable. Both
        # halves are things a CLI user really has: `--format json` for the
        # ledger, `--pack` for `contract.unresolved`. The MCP tool has
        # neither, which is why its own wording differs.
        assert "contract.unresolved=warn" in result.output
        assert "--format json" in result.output

    def test_json_does_not_repeat_what_its_report_already_carries(
        self, tmp_path: Path
    ) -> None:
        """`--format json` states the failures and the applied contribution in
        the report itself, so the notice would be a second copy beside the
        data -- and its "use --format json" advice would be nonsense. It also
        keeps stdout parseable for a caller piping the report onward."""
        old_p, new_p = _write(tmp_path, *_compatible_pair())
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--format",
                "json",
                "--contract",
                "exports",
            ],
        )
        assert result.exit_code == 1, result.output
        assert "Contract coverage incomplete" not in result.output
        # The report is still valid JSON and still carries the ledger.
        report = json.loads(result.output)
        assert report["contract_coverage_exit_contribution"] == 1
        assert report["contract_coverage_failures"]

    def test_quick_profile_is_ledgerless_so_it_still_explains(
        self, tmp_path: Path
    ) -> None:
        """The internal one-line format (``--profile quick``) is a summary
        that omits both ledger keys -- so a one-line run is ledgerless
        whatever else its output looks like, and suppressing on the format
        name alone left it exiting 1 with no explanation anywhere (Codex
        review, originally about ``--stat``; CLI cleanup phase two, PR 1
        moved the one-line output behind ``--profile quick`` instead of a
        boolean flag, but the ledgerless-summary property is unchanged)."""
        result = _compare(
            tmp_path,
            _compatible_pair(),
            "--profile",
            "quick",
            "--contract",
            "exports",
        )
        assert result.exit_code == 1, result.output
        # Confirms this really went through the one-line renderer (not, say,
        # a format that happens to also lack the ledger) -- the coverage
        # notice is appended after it, so the output leads with the
        # one-liner's own verdict label (CodeRabbit review). Checked on
        # `result.stdout`, not the stderr-mixed `result.output`: `quick`'s
        # `depth=binary` (ADR-063 Phase 8's ceiling fix) means this
        # unscoped-headers fixture no longer resolves a public-header
        # surface at that depth either, and that scope-fallback warning is
        # by design routed to stderr so it never corrupts this contract.
        assert result.stdout.startswith("NO_CHANGE:"), result.output
        assert "Contract coverage incomplete" in result.output

    def test_it_does_not_claim_a_floor_it_did_not_apply(self) -> None:
        """Beside a higher compatibility exit `max` keeps that code, so "Exit
        code floored to 1" would be a false statement about what happened.
        The incomplete coverage is still reported -- it is why part of the
        comparison could not be checked -- but as a contribution, not a
        status change.

        Asserted on the message itself, for the same reason
        `test_the_coverage_axis_never_lowers_a_real_abi_break` moved to the
        fold: a domain whose ledger fails no longer produces a 4 alongside
        it, so the base exit has to come from the caller here rather than
        from a fixture that cannot exist.
        """
        from abicheck.policy.contract_coverage_exit import _coverage_message

        message = _coverage_message(["old/export_table"], 1, 4)
        assert "floored" not in message
        assert "which stands" in message

    def test_the_tie_case_claims_neither_flooring_nor_being_below(self) -> None:
        """`base_exit == floor` is its own case. "Floored" would claim a change
        the axis did not make alone, and "below ... which stands" is simply
        false when the two are equal (CodeRabbit review)."""
        from abicheck.policy.contract_coverage_exit import _coverage_message

        tie = _coverage_message(["old/export_table"], 1, 1)
        assert "already 1" in tie
        assert "floored" not in tie
        assert "below" not in tie

    def test_scan_explains_its_coverage_gated_exit(self, tmp_path: Path) -> None:
        """`scan`'s text renderer ignores the ledger keys, so without this the
        command printed a clean verdict and then failed silently (Codex
        review). Built from the summary it already has -- the diff never
        reaches the scan CLI, which is why the announcement cannot live in
        the service-shared `_run_baseline_compare`."""
        old_p, new_p = _write(tmp_path, *_compatible_pair())
        result = CliRunner().invoke(
            main,
            [
                "scan",
                str(new_p),
                "--against",
                str(old_p),
                "--contract",
                "exports",
            ],
        )
        assert result.exit_code == 1, result.output
        assert "Contract coverage incomplete" in result.output
        assert "export_table" in result.output
        # It must say coverage RAISED the exit. `outcome.exit_code` has
        # already had the floor folded in, so passing that as the base made
        # the notice claim the exit "was already 1" for the very case where
        # coverage is what made it 1 (Codex review).
        assert "floored to 1" in result.output
        assert "already" not in result.output

    def test_a_non_json_secondary_report_still_gets_the_notice(
        self, tmp_path: Path
    ) -> None:
        """Staying quiet requires *every* rendered report to carry the ledger.
        With `--format json --secondary-format markdown` the markdown carries
        none of it, so answering from the primary alone let that report say
        the change is safe while the process exited 1 (Codex review)."""
        old_p, new_p = _write(tmp_path, *_compatible_pair())
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--format",
                "json",
                "-o",
                str(tmp_path / "r.json"),
                "--write",
                f"markdown={tmp_path / 'r.md'}",
                "--contract",
                "exports",
            ],
        )
        assert result.exit_code == 1, result.output
        assert "Contract coverage incomplete" in result.output

    def test_a_run_the_axis_does_not_gate_stays_quiet(self, tmp_path: Path) -> None:
        """No notice when there is nothing to explain -- otherwise the message
        becomes noise every run prints and no one reads."""
        result = _compare(
            tmp_path, _compatible_pair(), "--contract", "all"
        )
        assert result.exit_code == 0, result.output
        assert "Contract coverage incomplete" not in result.output


class TestArtifactsAgreeWithTheProcessExit:
    """An artifact that publishes its own machine-readable exit contract has
    to publish the one the process used. SARIF's
    `invocations[0].exitCode` said `0` beside a run that exited 1, so a
    consumer reading the artifact accepted a run its own coverage
    notifications said was gated (Codex review)."""

    def test_sarif_folds_the_coverage_floor_into_its_invocation(
        self, tmp_path: Path
    ) -> None:
        out = tmp_path / "report.sarif"
        result = _compare(
            tmp_path,
            _compatible_pair(),
            "--format",
            "sarif",
            "-o",
            str(out),
            "--contract",
            "exports",
        )
        assert result.exit_code == 1, result.output
        invocation = json.loads(out.read_text(encoding="utf-8"))["runs"][0][
            "invocations"
        ][0]
        assert invocation["exitCode"] == 1, invocation
        # ...and says which axis moved it, so the artifact alone distinguishes
        # a coverage floor from a gate decision.
        assert "contract coverage" in invocation["exitCodeDescription"], invocation
        # `executionSuccessful` is NOT folded: per the SARIF spec it reports
        # whether the tool ran to completion, and it did.
        assert invocation["executionSuccessful"] is True, invocation

    def test_a_clean_run_keeps_its_zero(self, tmp_path: Path) -> None:
        """The fold must not invent a floor for a run that had none."""
        out = tmp_path / "report.sarif"
        result = _compare(
            tmp_path, _compatible_pair(), "--format", "sarif", "-o", str(out)
        )
        assert result.exit_code == 0, result.output
        invocation = json.loads(out.read_text(encoding="utf-8"))["runs"][0][
            "invocations"
        ][0]
        assert invocation["exitCode"] == 0, invocation
        assert "contract coverage" not in invocation["exitCodeDescription"], invocation

    @pytest.mark.parametrize(("mode", "expected"), [("public", 4), ("exports", 1)])
    def test_the_artifact_states_the_exit_the_process_took(
        self, tmp_path: Path, mode: str, expected: int
    ) -> None:
        """The artifact's own exit contract must be the process's, whichever
        axis produced it.

        Both directions are covered by the two domains, which is what makes
        this the orthogonality assertion at the artifact level: under
        `public` the removal is in contract, so the compatibility axis
        speaks and its 4 stands over the coverage axis; under `exports` it is
        unresolvable, so compatibility is silent and the coverage 1 is the
        whole answer. A SARIF `exitCode: 0` beside a process that exited
        non-zero is the failure this guards.
        """
        out = tmp_path / "report.sarif"
        result = _compare(
            tmp_path,
            _breaking_pair(),
            "--format",
            "sarif",
            "-o",
            str(out),
            "--contract",
            mode,
        )
        assert result.exit_code == expected, result.output
        invocation = json.loads(out.read_text(encoding="utf-8"))["runs"][0][
            "invocations"
        ][0]
        assert invocation["exitCode"] == expected, invocation


class TestTheExitCodeContractIsDocumented:
    """A command's own `--help` is the exit-code contract a CI integration is
    written against. Both commands gained a new meaning for exit 1 while their
    help still enumerated 0/2/4, so a real coverage result read as an
    undocumented failure -- or, under the severity scheme, as a severity error
    (Codex review)."""

    @pytest.mark.parametrize("command", ["compare", "scan"])
    def test_the_command_help_documents_the_coverage_exit(self, command: str) -> None:
        help_text = CliRunner().invoke(main, [command, "--help"]).output
        assert "contract coverage" in help_text.lower(), help_text
        # ...and says it is orthogonal, since the whole point is that it does
        # not displace the compatibility verdict it sits beside.
        assert "--contract" in help_text, help_text

    def test_the_dry_run_scheme_banner_says_it_too(self, tmp_path: Path) -> None:
        """`compare --dry-run`'s banner states the legacy scheme's codes. It
        is the same contract in a second place, so it cannot say 0/2/4 alone
        while the run beside it can exit 1."""
        result = _compare(tmp_path, _compatible_pair(), "--dry-run")
        assert result.exit_code == 0, result.output
        assert "contract coverage" in result.output.lower(), result.output


class TestTheProgrammaticApiStaysQuiet:
    """`fold_coverage_exit` is on `service_scan.run_scan()`'s path, so it must
    stay pure. A library call that writes to stderr is an unexpected side
    effect for a caller that already gets the coverage details back in its
    result (Codex review) -- and it was a real one, since the announcement
    briefly lived inside the fold."""

    def test_folding_writes_nothing(self, tmp_path: Path, capsys) -> None:
        from abicheck.contract_coverage_exit import fold_coverage_exit

        class _Ctx:
            pass

        # A result with no context is the trivial case; the point is that the
        # fold has no output path at all, so nothing can leak from it.
        assert fold_coverage_exit(4, object()) == 4
        assert capsys.readouterr().err == ""

    def test_the_module_does_not_import_click_at_module_scope(self) -> None:
        """The import is function-local inside the announcer, so importing
        this module from a library context pulls in no CLI machinery."""
        import abicheck.contract_coverage_exit as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        module_level = [
            line for line in source.splitlines() if line.startswith("import click")
        ]
        assert module_level == [], module_level


class TestCompareAndScanFoldTheAxisIdentically:
    """§6.4's cross-command parity Gate, for the axis Phase 7 made real.

    A ledger that gated one command and not the other would be exactly the
    divergence that Gate exists to catch -- and it was the live risk here,
    since `scan --against` builds its exit code in its own module.
    """

    @pytest.mark.parametrize(
        ("pair", "mode", "expected"),
        [
            # Compatible + unresolvable domain: the coverage axis alone.
            (_compatible_pair(), "exports", 1),
            # A real break the selected domain resolves: the compatibility
            # axis alone, and both commands must agree on that too -- parity
            # is about the whole fold, not only its coverage half.
            (_breaking_pair(), "public", 4),
            # A break the selected domain cannot resolve: neither command may
            # call uncertainty a break, and neither may call it clean.
            (_breaking_pair(), "exports", 1),
        ],
    )
    def test_both_commands_reach_the_same_exit(
        self, tmp_path: Path, pair, mode: str, expected: int
    ) -> None:
        old_p, new_p = _write(tmp_path, *pair)
        common = ["--contract", mode]
        runner = CliRunner()
        compare = runner.invoke(main, ["compare", str(old_p), str(new_p), *common])
        scan = runner.invoke(
            main, ["scan", str(new_p), "--against", str(old_p), *common]
        )
        assert compare.exit_code == expected, compare.output
        assert scan.exit_code == expected, scan.output


class TestUnresolvedBehaviourAcceptsIncompleteCoverage:
    """`contract.unresolved=warn` is D9's explicit accept mechanism, and this
    is its first engine consumer -- the reason the field was listed in
    `pack_application.UNAPPLIED_PACK_FIELDS` until now."""

    @staticmethod
    def _warn_pack(tmp_path: Path) -> Path:
        path = tmp_path / "accept-unresolved.yml"
        path.write_text(
            "id: accept_unresolved\nversion: 1\nkind: contract\n"
            "assignments:\n  contract.unresolved: warn\n",
            encoding="utf-8",
        )
        return path

    def test_warn_zeroes_the_floor(self, tmp_path: Path) -> None:
        result = _compare(
            tmp_path,
            _compatible_pair(),
            "--contract",
            "exports",
            "--pack",
            str(self._warn_pack(tmp_path)),
        )
        assert result.exit_code == 0, result.output

    def test_warn_does_not_hide_the_failures_it_accepts(self, tmp_path: Path) -> None:
        """Section 6.2: it "changes only the orthogonal contract-coverage
        contribution, not GateDecision, evidence, or labels". So the ledger
        still reports every failure -- accepting incomplete assurance is not
        the same as pretending it was complete. The contribution itself does
        go to 0, because it is now applied: a reported "1" beside an exit
        status of 0 would be the disagreement this field exists to rule out.
        """
        old_p, new_p = _write(tmp_path, *_compatible_pair())
        out = tmp_path / "report.json"
        CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--format",
                "json",
                "--contract",
                "exports",
                "--pack",
                str(self._warn_pack(tmp_path)),
                "-o",
                str(out),
            ],
        )
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["contract_coverage_failures"], report
        assert report["contract_coverage_exit_contribution"] == 0

    @pytest.mark.parametrize("command", ["compare", "scan"])
    def test_it_is_rejected_without_contract_evaluation(
        self, tmp_path: Path, command: str
    ) -> None:
        """A pack whose only assignment has no consumer in *this invocation*
        is the decorative pack `pack_application` exists to prevent, one
        level in: the field is applied by this build, but nothing computes
        coverage unless a domain was selected, so the value would be recorded
        as active configuration and read back as nothing (Codex review).

        Rejected rather than silently accepted, and on both commands --
        `scan --against` resolves packs through the same check.
        """
        old_p, new_p = _write(tmp_path, *_compatible_pair())
        pack = str(self._warn_pack(tmp_path))
        argv = (
            ["compare", str(old_p), str(new_p), "--pack", pack]
            if command == "compare"
            else ["scan", str(new_p), "--against", str(old_p), "--pack", pack]
        )
        result = CliRunner().invoke(main, argv)
        assert result.exit_code == 64, result.output
        assert "contract.unresolved" in result.output
        assert "--contract" in result.output

    def test_the_dry_run_rejects_it_too(self, tmp_path: Path) -> None:
        """`--dry-run` must not approve a plan the identical real run rejects.

        Answerable that early because no layer other than a pack can state
        `contract.unresolved` -- its resolver candidate list is empty -- so
        there is no shadowing case in which the raw manifest would
        over-reject (Codex review, the same dry-run divergence raised twice
        before for manifest validity and inert values).
        """
        old_p, new_p = _write(tmp_path, *_compatible_pair())
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--dry-run",
                "--pack",
                str(self._warn_pack(tmp_path)),
            ],
        )
        assert result.exit_code == 64, result.output
        assert "contract.unresolved" in result.output

    def test_warn_still_says_coverage_was_incomplete(self, tmp_path: Path) -> None:
        """Accepting is not hiding — and for markdown/review/sarif/junit the
        notice is the *only* place it could be said, since those renderers
        omit the ledger. Basing the notice on the exit floor meant `warn`
        silenced it there entirely (Codex review)."""
        result = _compare(
            tmp_path,
            _compatible_pair(),
            "--format",
            "review",
            "--contract",
            "exports",
            "--pack",
            str(self._warn_pack(tmp_path)),
        )
        assert result.exit_code == 0, result.output
        assert "Contract coverage incomplete" in result.output
        assert "contract.unresolved=warn" in result.output
        assert "contributes 0" in result.output

    @pytest.mark.parametrize("mode", ["exports", "public"])
    def test_warn_never_moves_the_compatibility_axis(
        self, tmp_path: Path, mode: str
    ) -> None:
        """It accepts missing *coverage*, never a real break.

        Asserted as "identical to the same run without the pack, except that
        the coverage floor is gone" rather than as a fixed exit code: what
        `warn` may change is one axis, so comparing the two runs is the
        statement, and it holds in both domains -- `public`, where the break
        is in contract and the 4 must survive the pack, and `exports`, where
        the compatibility axis was already silent and only the accepted
        coverage 1 falls away.
        """
        without = _compare(
            tmp_path, _breaking_pair(), "--contract", mode
        )
        with_warn = _compare(
            tmp_path,
            _breaking_pair(),
            "--contract",
            mode,
            "--pack",
            str(self._warn_pack(tmp_path)),
        )
        expected = 4 if mode == "public" else 0
        assert with_warn.exit_code == expected, with_warn.output
        # The compatibility axis is untouched: the only difference the pack
        # is allowed to make is dropping a coverage floor of 1.
        assert without.exit_code == max(with_warn.exit_code, 1), without.output
