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
            "--contract-evaluation",
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
            tmp_path, _compatible_pair(), "--contract-evaluation", "--contract", "all"
        )
        assert result.exit_code == 0, result.output

    def test_a_run_that_never_asked_the_question_is_untouched(
        self, tmp_path: Path
    ) -> None:
        """No `--contract-evaluation` means no selected domain to be short of
        evidence for. Inventing a floor there would fail invocations that
        never opted in -- which is every pre-existing one."""
        result = _compare(tmp_path, _compatible_pair())
        assert result.exit_code == 0, result.output

    def test_the_coverage_axis_never_lowers_a_real_abi_break(
        self, tmp_path: Path
    ) -> None:
        """`max`, not replacement. A coverage failure raising 4 to 1 would
        turn a removed export into "warnings only" -- the axes are
        orthogonal and the compatibility one is more severe when it speaks."""
        result = _compare(
            tmp_path,
            _breaking_pair(),
            "--contract-evaluation",
            "--contract",
            "exports",
        )
        assert result.exit_code == 4, result.output

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
                "--contract-evaluation",
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
            "--contract-evaluation",
            "--contract",
            "exports",
        )
        assert result.exit_code == 1, result.output
        # Names the providers, not just a count: "old/export_table" is
        # actionable, "2 coverage failures" is not.
        assert "export_table" in result.output, result.output
        # ...and points at the way out, so the message is actionable.
        assert "contract.unresolved=warn" in result.output

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
                "--contract-evaluation",
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

    def test_stat_json_is_ledgerless_so_it_still_explains(self, tmp_path: Path) -> None:
        """`--stat` renders `to_stat_json`, a summary that omits both ledger
        keys -- so a stat run is ledgerless whatever `--format` says, and
        suppressing on the format name alone left it exiting 1 with no
        explanation anywhere (Codex review)."""
        result = _compare(
            tmp_path,
            _compatible_pair(),
            "--stat",
            "--format",
            "json",
            "--contract-evaluation",
            "--contract",
            "exports",
        )
        assert result.exit_code == 1, result.output
        assert "Contract coverage incomplete" in result.output

    def test_it_does_not_claim_a_floor_it_did_not_apply(self, tmp_path: Path) -> None:
        """Beside a real ABI break `max` keeps the 4, so "Exit code floored to
        1" would be a false statement about what happened. The incomplete
        coverage is still reported -- it is why part of the comparison could
        not be checked -- but as a contribution, not a status change."""
        result = _compare(
            tmp_path,
            _breaking_pair(),
            "--contract-evaluation",
            "--contract",
            "exports",
        )
        assert result.exit_code == 4, result.output
        assert "Contract coverage incomplete" in result.output
        assert "floored" not in result.output
        assert "which stands" in result.output

    def test_the_tie_case_claims_neither_flooring_nor_being_below(self) -> None:
        """`base_exit == floor` is its own case. "Floored" would claim a change
        the axis did not make alone, and "below ... which stands" is simply
        false when the two are equal (CodeRabbit review)."""
        from abicheck.contract_coverage_exit import _coverage_message

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
                "--contract-evaluation",
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
                "--secondary-format",
                "markdown",
                "--secondary-output",
                str(tmp_path / "r.md"),
                "--contract-evaluation",
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
            tmp_path, _compatible_pair(), "--contract-evaluation", "--contract", "all"
        )
        assert result.exit_code == 0, result.output
        assert "Contract coverage incomplete" not in result.output


class TestEveryFrontEndFoldsTheAxis:
    """The (b)-shape failures on this PR have all been asymmetries: a rule on
    one front end and not another. `compare`, `scan --against` and the MCP
    `abi_compare` tool all return an exit code, so all three must fold."""

    def test_the_mcp_tool_folds_it_too(self, tmp_path: Path) -> None:
        """Returning a compatibility-only code beside a report stating the
        coverage contribution was 1 lets a client that gates on the code
        accept a run its own report says was gated (Codex review)."""
        pytest.importorskip("mcp")
        from abicheck.mcp_server import abi_compare

        old_p, new_p = _write(tmp_path, *_compatible_pair())
        raw = abi_compare(
            old_input=str(old_p),
            new_input=str(new_p),
            contract_evaluation=True,
        )
        result = json.loads(raw) if isinstance(raw, str) else raw
        # `abi_compare` takes no --contract parameter, so the domain is the
        # built-in default; this fixture's symbols carry no header
        # provenance, so that domain cannot close either. The pair is
        # otherwise compatible, so a returned 1 can only be the coverage axis.
        assert result["verdict"] in {"NO_CHANGE", "COMPATIBLE"}, result
        assert result["exit_code"] == 1, result

    @pytest.mark.parametrize("fmt", ["json", "markdown"])
    def test_the_mcp_response_explains_the_gate_in_every_format(
        self, tmp_path: Path, fmt: str
    ) -> None:
        """Only the JSON report carries the ledger, so a client asking for any
        other format got a change-free report beside `exit_code: 1` with
        nothing saying why (Codex review). The block is a sibling of the code
        it explains, so no client has to branch on `output_format` to find it.
        """
        pytest.importorskip("mcp")
        from abicheck.mcp_server import abi_compare

        old_p, new_p = _write(tmp_path, *_compatible_pair())
        raw = abi_compare(
            old_input=str(old_p),
            new_input=str(new_p),
            contract_evaluation=True,
            output_format=fmt,
        )
        result = json.loads(raw) if isinstance(raw, str) else raw
        assert result["exit_code"] == 1, result
        coverage = result["contract_coverage"]
        assert coverage["exit_contribution"] == 1, coverage
        assert coverage["failures"], coverage
        # ...and it says which provider fell short, not just that one did.
        assert "Contract coverage incomplete" in coverage["diagnostic"], coverage

    def test_a_run_without_contract_evaluation_gets_no_coverage_block(
        self, tmp_path: Path
    ) -> None:
        """"We looked and found nothing" and "we never looked" must not
        serialize the same, so the block is absent rather than empty when no
        contract was evaluated at all."""
        pytest.importorskip("mcp")
        from abicheck.mcp_server import abi_compare

        old_p, new_p = _write(tmp_path, *_compatible_pair())
        raw = abi_compare(old_input=str(old_p), new_input=str(new_p))
        result = json.loads(raw) if isinstance(raw, str) else raw
        assert result["exit_code"] == 0, result
        assert "contract_coverage" not in result, result


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
        ("pair", "expected"), [(_compatible_pair(), 1), (_breaking_pair(), 4)]
    )
    def test_both_commands_reach_the_same_exit(
        self, tmp_path: Path, pair, expected: int
    ) -> None:
        old_p, new_p = _write(tmp_path, *pair)
        common = ["--contract-evaluation", "--contract", "exports"]
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
            "--contract-evaluation",
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
                "--contract-evaluation",
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
        assert "--contract-evaluation" in result.output

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
            "--contract-evaluation",
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
        """It accepts missing *coverage*, never a real break."""
        result = _compare(
            tmp_path,
            _breaking_pair(),
            "--contract-evaluation",
            "--contract",
            mode,
            "--pack",
            str(self._warn_pack(tmp_path)),
        )
        assert result.exit_code == 4, result.output
