# SPDX-License-Identifier: Apache-2.0
"""One comparison product: cardinality and baseline presence are *inputs*.

``docs/contribute/plans/one-comparison-product.md``. Two properties, stated
against the real public workflow (the ``compare`` CLI and the typed API),
not against an internal helper:

1. **Baseline presence.** A ``compare --no-baseline`` audit and a two-sided
   ``compare`` of the *same candidate* observe the same candidate-side
   facts. They differ in exactly one respect -- what each can say about
   history -- and the audit says ``not_evaluated``, never an observed
   state. This is the property the old self-diff implementation violated:
   comparing the candidate against a copy of itself made every
   candidate-side hygiene finding report ``persistent`` ("present on both
   sides") about a baseline the run was told does not exist.

2. **Cardinality.** A directory ``compare`` holding exactly one library and
   a scalar ``compare`` of that same library agree on the outcome, for a
   setting whose meaning must not depend on the input shape -- here
   ``.abicheck.yml``'s ``assurance.require_complete``, which the release
   fan-out used to reject outright on the grounds that it had "no single
   analysis_assurance result to gate on".

Both are written as *equalities between two real invocations*, so a change
that alters one path and not the other fails here rather than in whichever
downstream consumer notices first. Neither asserts a fixed expected value
for the shared half: pinning "the audit reports N findings" would pass
just as well if both paths regressed together, which is the failure mode a
parity test exists to catch.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))
import example_catalog  # noqa: E402

from abicheck.checker import compare  # noqa: E402
from abicheck.cli import main  # noqa: E402
from abicheck.model import AbiSnapshot  # noqa: E402
from abicheck.policy.evidence_status import CrossSourceEvolution  # noqa: E402
from abicheck.serialization import load_snapshot, snapshot_to_json  # noqa: E402


def _invoke(*args: str):
    return CliRunner().invoke(main, list(args))


#: A committed G20 audit fixture that really does fire a cross-source
#: hygiene check. A hand-built snapshot was tried first and fired none of
#: the eleven -- every property below would then have held vacuously,
#: which is the failure mode this file exists to rule out. The
#: ``test_the_candidate_actually_fires_a_check`` guard below keeps that
#: true if the fixture or the checks ever change.
_FIXTURE = "case143_audit_accidental_export"


def _candidate_path() -> Path:
    path = example_catalog.case_dir(_FIXTURE) / "snapshot.abi.json"
    assert path.is_file(), f"missing committed G20 fixture: {path}"
    return path


def _candidate(version: str = "1.0", *, from_headers: bool = True) -> AbiSnapshot:
    """The fixture, loaded fresh each call.

    Fresh rather than shared: ``compare()`` stamps ``cross_source_
    evolution`` onto the ``Change`` objects the checks return, and two
    invocations must not be able to observe each other's stamping.
    """
    return dataclasses.replace(
        load_snapshot(_candidate_path()), version=version, from_headers=from_headers
    )


def _candidate_side(changes) -> dict[tuple, object]:
    """The candidate-side findings of a change set, keyed by identity.

    Deliberately keyed on ``(kind, symbol, new_value)`` and **not** on the
    evolution state: the evolution state is precisely the axis the two
    runs are expected to differ on, so including it would make the
    comparison below tautological.
    """
    return {
        (c.kind.value, c.symbol, c.new_value): c
        for c in changes
        if c.cross_source_evolution is not None
    }


# ---------------------------------------------------------------------------
# 1. Baseline presence
# ---------------------------------------------------------------------------


class TestBaselinePresenceIsAnInput:
    """The same candidate, audited alone and compared against a baseline."""

    def test_the_audit_observes_what_a_two_sided_run_observes(self) -> None:
        """Identical candidate-side findings, from one pipeline.

        The audit resolves no baseline at all; the two-sided run compares
        the candidate against an identical-content baseline. Whatever the
        cross-source checks can say *about the candidate* is a property of
        the candidate, so the two must find the same set -- if they
        diverge, the audit is running a different pipeline, which is the
        whole thing this plan removes.
        """
        candidate = _candidate()
        audited = compare(None, candidate)
        two_sided = compare(_candidate(), candidate)

        assert set(_candidate_side(audited.changes)) == set(
            _candidate_side(two_sided.changes)
        )

    def test_the_audit_never_claims_an_observed_history(self) -> None:
        """Every candidate-side finding reads ``not_evaluated``.

        Not "is not ``introduced``/``resolved``": ``persistent`` is the
        state the self-diff produced and permitted, and it is a claim that
        the finding was present on a baseline that does not exist.
        """
        result = compare(None, _candidate())
        states = {
            c.cross_source_evolution
            for c in result.changes
            if c.cross_source_evolution is not None
        }
        assert states <= {CrossSourceEvolution.NOT_EVALUATED}, states

    def test_a_real_baseline_still_produces_real_evolution_states(self) -> None:
        """The counterpart, so the property above cannot pass by the engine
        simply having stopped stamping the field anywhere.

        Two *identical* snapshots are a legitimate two-sided comparison
        (the same build twice), and there ``persistent`` is a real,
        observed answer -- which is exactly why it must not appear when no
        baseline was supplied.
        """
        result = compare(_candidate(), _candidate())
        states = {
            c.cross_source_evolution
            for c in result.changes
            if c.cross_source_evolution is not None
        }
        assert CrossSourceEvolution.PERSISTENT in states, states

    def test_the_audit_evaluates_no_delta_at_all(self) -> None:
        """No addition, removal or modification can exist without a
        baseline -- and, unlike under the self-diff, that is now structural
        rather than a consequence of the two sides being equal."""
        result = compare(None, _candidate())
        assert [c for c in result.changes if c.cross_source_evolution is None] == []
        assert result.old_symbol_count is None

    def test_the_candidate_actually_fires_a_check(self) -> None:
        """Guard against a vacuous suite.

        Every equality in this class is over the candidate-side finding
        set; if that set were empty, all of them would pass no matter what
        the engine did. Asserted once, here, rather than repeated.
        """
        assert _candidate_side(compare(None, _candidate()).changes)

    def test_the_cli_audit_and_the_engine_agree(self, tmp_path: Path) -> None:
        """The same statement through the real public workflow, so a
        front end that re-derives any of it is caught too."""
        result = _invoke(
            "compare", "--no-baseline", str(_candidate_path()), "-o", "json=-"
        )
        assert result.exit_code == 0, result.output
        report = json.loads(result.output)

        assert report["verdict"] is None
        assert report["changes"] == []
        assert report["findings"], "the fixture must report something to compare"
        reported = {f["evolution"] for f in report["findings"]}
        assert reported <= {"not_evaluated", None}, reported


# ---------------------------------------------------------------------------
# 2. Cardinality
# ---------------------------------------------------------------------------


def _incomplete_analysis_pair(tmp_path: Path) -> tuple[Path, Path]:
    """A pair whose analysis assurance is knowingly short of ``complete``.

    Asymmetric header evidence: the OLD side was extracted from headers and
    the NEW side was not, which is one of the asymmetries the assurance
    rollup reports (``header_context_status``) and which drops the run to
    ``partial``. That is what makes ``assurance.require_complete: true`` a
    live gate here rather than a no-op -- a symmetric pair of these
    fixtures rolls up ``complete``, and every equality below would then
    hold for the trivial reason that nothing was ever floored.
    ``test_the_fixture_really_is_short_of_complete`` pins that.
    """
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    (old_dir / "libfoo.so.abi.json").write_text(
        snapshot_to_json(_candidate(version="1.0")), encoding="utf-8"
    )
    (new_dir / "libfoo.so.abi.json").write_text(
        snapshot_to_json(_candidate(version="2.0", from_headers=False)),
        encoding="utf-8",
    )
    return old_dir, new_dir


def _project_config(tmp_path: Path, *, require_complete: bool) -> None:
    (tmp_path / ".abicheck.yml").write_text(
        "assurance:\n  require_complete: "
        + ("true" if require_complete else "false")
        + "\n",
        encoding="utf-8",
    )


class TestCardinalityIsAnInput:
    """One library, compared as a scalar pair and as a one-member release."""

    def test_the_fixture_really_is_short_of_complete(self, tmp_path: Path) -> None:
        """Guard against a vacuous suite, the cardinality half.

        ``assurance.require_complete`` only moves an exit code when the
        run's assurance is not ``complete``; if the fixture rolled up
        ``complete``, both parametrizations below would agree at ``0`` no
        matter what either path did with the setting.
        """
        result = compare(
            _candidate(version="1.0"),
            _candidate(version="2.0", from_headers=False),
        )
        assert result.analysis_assurance is not None
        assert result.analysis_assurance.status != "complete"

    def test_the_setting_actually_moves_the_scalar_exit_code(
        self, tmp_path: Path
    ) -> None:
        """The other half of the same guard, at the CLI: the scalar path
        exits ``0`` without the setting and ``1`` with it, so the equality
        below is comparing two genuinely different outcomes."""
        old_dir, new_dir = _incomplete_analysis_pair(tmp_path)
        args = (
            str(old_dir / "libfoo.so.abi.json"),
            str(new_dir / "libfoo.so.abi.json"),
        )
        _project_config(tmp_path, require_complete=False)
        off = _invoke("compare", *args, "--config", str(tmp_path / ".abicheck.yml"))
        _project_config(tmp_path, require_complete=True)
        on = _invoke("compare", *args, "--config", str(tmp_path / ".abicheck.yml"))
        assert (off.exit_code, on.exit_code) == (0, 1), (off.output, on.output)

    @pytest.mark.parametrize("require_complete", [False, True])
    def test_the_setting_gates_a_release_exactly_as_it_gates_a_pair(
        self, tmp_path: Path, require_complete: bool
    ) -> None:
        """Same exit code either way, under either setting.

        Parametrized over both settings on purpose: asserting only the
        ``true`` case would also pass if the release path had started
        flooring *every* run, and asserting only ``false`` would pass if it
        still ignored the setting entirely. The pair of cases pins that the
        setting is what moves the exit code, on both shapes.
        """
        old_dir, new_dir = _incomplete_analysis_pair(tmp_path)
        _project_config(tmp_path, require_complete=require_complete)

        release = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--config",
            str(tmp_path / ".abicheck.yml"),
            "-o",
            "json=-",
        )
        scalar = _invoke(
            "compare",
            str(old_dir / "libfoo.so.abi.json"),
            str(new_dir / "libfoo.so.abi.json"),
            "--config",
            str(tmp_path / ".abicheck.yml"),
            "-o",
            "json=-",
        )
        assert release.exit_code == scalar.exit_code, (
            f"require_complete={require_complete}: a one-library release "
            f"exited {release.exit_code} where the same library compared "
            f"alone exited {scalar.exit_code}\n"
            f"--- release ---\n{release.output}\n--- scalar ---\n{scalar.output}"
        )

    def test_the_setting_is_not_rejected_for_a_directory_operand(
        self, tmp_path: Path
    ) -> None:
        """It used to be a hard usage error (exit 64), which is what made
        the meaning of the setting depend on the input shape."""
        old_dir, new_dir = _incomplete_analysis_pair(tmp_path)
        _project_config(tmp_path, require_complete=True)

        result = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--config",
            str(tmp_path / ".abicheck.yml"),
            "-o",
            "json=-",
        )
        assert result.exit_code != 64, result.output
        assert "not supported for directory" not in result.output


# ---------------------------------------------------------------------------
# 3. The machine document is complete, or says exactly where the rest is
# ---------------------------------------------------------------------------


def _many_removals_pair(tmp_path: Path, count: int = 25) -> tuple[Path, Path]:
    """A one-library release whose findings exceed the display cap."""
    from abicheck.model import Function, Visibility

    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    old = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        functions=[
            Function(
                name=f"foo{i}",
                mangled=f"_Z4foo{i}v",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
            for i in range(count)
        ],
    )
    new = AbiSnapshot(
        library="libfoo.so", version="2.0", from_headers=True, functions=[]
    )
    (old_dir / "libfoo.json").write_text(snapshot_to_json(old), encoding="utf-8")
    (new_dir / "libfoo.json").write_text(snapshot_to_json(new), encoding="utf-8")
    return old_dir, new_dir


class TestTheMachineDocumentIsNotImplicitlyTruncated:
    """A truncated human summary is fine; a truncated machine document is
    only acceptable when the run asked for it *and* says where the rest is.
    """

    def test_json_carries_every_finding_by_default(self, tmp_path: Path) -> None:
        old_dir, new_dir = _many_removals_pair(tmp_path, count=25)
        result = _invoke("compare", str(old_dir), str(new_dir), "-o", "json=-")
        assert result.exit_code == 4, result.output
        lib = json.loads(result.output)["libraries"][0]
        assert len(lib["findings"]) == 26  # 25 removals + public_surface_shrank
        assert "findings_truncated" not in lib

    def test_the_human_summary_is_still_bounded(self, tmp_path: Path) -> None:
        """The counterpart: uncapping the shared projection must not have
        uncapped the Markdown report, which is what the cap is *for*."""
        old_dir, new_dir = _many_removals_pair(tmp_path, count=25)
        result = _invoke("compare", str(old_dir), str(new_dir), "-o", "markdown=-")
        assert result.exit_code == 4, result.output
        rendered = sum(
            1
            for line in result.output.splitlines()
            if line.startswith("- **func_removed**")
        )
        assert rendered < 25, result.output
        assert "additional findings omitted" in result.output

    def test_no_invocation_can_truncate_the_machine_document(
        self, tmp_path: Path
    ) -> None:
        """Plan slice 7m: asking for truncation is no longer expressible.

        This used to be two tests -- "an explicit cap still truncates and
        still says so" and "a truncated entry indexes its complete
        artifact" -- both of which described `--max-findings-per-library`,
        the flag whose whole purpose was to bound a document the reader
        never had to bound. It retired as a *consequence* of the export
        request rather than by deletion: complete machine data is never
        truncated, the human summary stays bounded automatically (the test
        above), and every member's own complete report is one export away
        (the test below). So the property worth pinning is the absence: no
        invocation, and no environment, produces a truncated machine
        document.
        """
        old_dir, new_dir = _many_removals_pair(tmp_path, count=25)
        result = _invoke("compare", str(old_dir), str(new_dir), "-o", "json=-")
        assert result.exit_code == 4, result.output
        lib = json.loads(result.output)["libraries"][0]
        assert len(lib["findings"]) == 26
        assert "findings_truncated" not in lib
        assert "findings_truncated_kinds" not in lib

    def test_the_retired_cap_flag_is_gone_with_no_alias(
        self, tmp_path: Path
    ) -> None:
        old_dir, new_dir = _many_removals_pair(tmp_path, count=3)
        result = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--max-findings-per-library",
            "5",
        )
        assert result.exit_code == 64, result.output
        assert "No such option" in result.output

    def test_the_complete_per_member_report_is_one_export_away(
        self, tmp_path: Path
    ) -> None:
        """What the retired cap's own help text already pointed at: the
        per-component export writes each member's complete, uncapped
        report, and the summary indexes it unambiguously rather than in
        prose a machine consumer cannot follow."""
        old_dir, new_dir = _many_removals_pair(tmp_path, count=25)
        reports = tmp_path / "reports"
        summary = tmp_path / "summary.json"
        result = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "-o",
            f"json={summary}",
            "-o",
            f"json={reports}/",
        )
        assert result.exit_code == 4, result.output
        lib = json.loads(summary.read_text(encoding="utf-8"))["libraries"][0]
        complete = Path(lib["complete_report"])
        assert complete.is_file(), lib
        full = json.loads(complete.read_text(encoding="utf-8"))
        # Both are complete now; the per-member report is the one carrying
        # the full `changes` graph rather than the summary's projection.
        assert len(full["changes"]) >= len(lib["findings"])


class TestReleaseArtifactsDoNotDependOnCardinality:
    """``--write`` and ``--format oneline`` for a directory operand."""

    def test_write_is_repeatable(self, tmp_path: Path) -> None:
        """A second ``--write`` used to be a usage error for a release
        operand ("the per-library release engine does not yet support
        multiple secondary artifacts"), while `compare` accepted it for a
        single pair -- so the input shape decided how many artifacts one
        analysis could produce."""
        old_dir, new_dir = _many_removals_pair(tmp_path, count=3)
        first = tmp_path / "a.json"
        second = tmp_path / "b.md"
        result = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "-o",
            "oneline=-",
            "-o",
            f"json={first}",
            "-o",
            f"markdown={second}",
        )
        assert result.exit_code == 4, result.output
        assert json.loads(first.read_text(encoding="utf-8"))["libraries"]
        assert "libfoo" in second.read_text(encoding="utf-8")

    def test_oneline_renders_for_a_release_and_agrees_with_the_summary(
        self, tmp_path: Path
    ) -> None:
        """The format existed for a single pair and was refused for a
        directory. Checked against the JSON summary's own counts rather than
        against a literal string, so the two renders of one analysis cannot
        disagree."""
        old_dir, new_dir = _many_removals_pair(tmp_path, count=3)
        summary = tmp_path / "s.json"
        result = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "-o",
            "oneline=-",
            "-o",
            f"json={summary}",
        )
        assert result.exit_code == 4, result.output
        line = result.output.strip()
        libraries = json.loads(summary.read_text(encoding="utf-8"))["libraries"]
        breaking = sum(lib["breaking"] for lib in libraries)
        assert (
            f"{len(libraries)} library" in line or f"{len(libraries)} libraries" in line
        )
        assert f"{breaking} breaking" in line, line


# ---------------------------------------------------------------------------
# 5. Review-round regressions (Codex, PR #1238)
# ---------------------------------------------------------------------------


class TestTheReportAgreesWithTheProcessExit:
    """P1: `assurance.require_complete` reached only the process exit.

    Every release report decision -- the JSON `exit` block, `run_outcome`,
    `effective_config_fields["gate.require_complete_analysis"]`, and the
    `--output-dir` sidecar -- was resolved with the setting defaulted off, so
    a run that exited `1` persisted `exit.code: 0` and `false`. A machine
    consumer reading the artifact would accept a run the process failed.
    """

    def test_the_json_exit_block_matches_the_real_exit(self, tmp_path: Path) -> None:
        old_dir, new_dir = _incomplete_analysis_pair(tmp_path)
        _project_config(tmp_path, require_complete=True)
        summary = tmp_path / "summary.json"

        result = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--config",
            str(tmp_path / ".abicheck.yml"),
            "-o",
            f"json={summary}",
        )
        assert result.exit_code == 1, result.output
        doc = json.loads(summary.read_text(encoding="utf-8"))
        assert doc["exit"]["code"] == result.exit_code
        assert doc["exit"]["analysis_assurance_contribution"] == 1
        assert "analysis_assurance" in doc["exit"]["reasons"]
        assert (
            doc["effective_config_fields"]["gate.require_complete_analysis"] == "True"
        )

    def test_the_output_dir_sidecar_matches_too(self, tmp_path: Path) -> None:
        """The sidecar is its own document with its own resolver call -- and
        the axis that shipped before this one was missed at exactly this
        third call site."""
        old_dir, new_dir = _incomplete_analysis_pair(tmp_path)
        _project_config(tmp_path, require_complete=True)
        reports = tmp_path / "reports"

        result = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--config",
            str(tmp_path / ".abicheck.yml"),
            "-o",
            f"json={tmp_path / 's.json'}",
            "-o",
            f"json={reports}/",
        )
        assert result.exit_code == 1, result.output
        doc = json.loads((reports / "summary.json").read_text(encoding="utf-8"))
        assert doc["exit"]["code"] == result.exit_code
        assert doc["exit"]["analysis_assurance_contribution"] == 1

    def test_without_the_setting_the_report_stays_clean(self, tmp_path: Path) -> None:
        """The negative control: the fix must not make every run report the
        floor. Same operands, setting off."""
        old_dir, new_dir = _incomplete_analysis_pair(tmp_path)
        _project_config(tmp_path, require_complete=False)
        summary = tmp_path / "summary.json"

        result = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--config",
            str(tmp_path / ".abicheck.yml"),
            "-o",
            f"json={summary}",
        )
        assert result.exit_code == 0, result.output
        doc = json.loads(summary.read_text(encoding="utf-8"))
        assert doc["exit"]["code"] == 0
        assert doc["exit"]["analysis_assurance_contribution"] == 0


class TestTheMarkdownCapIsAutomaticAndUnconfigurable:
    """P2 was: the Markdown render sliced at the built-in constant, so
    raising `--max-findings-per-library` above 10 changed nothing it
    itemized -- contradicting the option's own documented contract.

    Plan slice 7m settled that contradiction the other way: the option (and
    the environment variable beside it) retired, and the human summary's
    bound is now automatic and unconfigurable. The regression this class
    still guards is the same one, restated: the render must slice at the
    *one* cap the leaf owns, so it cannot drift from what every other
    consumer of that cap believes -- and nothing outside the leaf can move
    it.
    """

    def test_the_render_slices_at_the_leafs_own_cap(self, tmp_path: Path) -> None:
        from abicheck.report.release_display_limits import (
            MAX_RELEASE_FINDINGS_PER_LIBRARY,
        )

        old_dir, new_dir = _many_removals_pair(tmp_path, count=25)
        result = _invoke("compare", str(old_dir), str(new_dir), "-o", "markdown=-")
        assert result.exit_code == 4, result.output
        rendered = sum(
            1
            for line in result.output.splitlines()
            if line.startswith("- **func_removed**")
        )
        assert rendered == MAX_RELEASE_FINDINGS_PER_LIBRARY, result.output
        assert "additional findings omitted" in result.output

    def test_the_environment_cannot_move_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The env var retired with the flag rather than surviving it --
        Phase 7l's standing constraint that a demoted flag lands in
        `.abicheck.yml`, never in an undocumented variable. Set to a value
        that would be plainly visible if anything still read it."""
        from abicheck.report.release_display_limits import (
            MAX_RELEASE_FINDINGS_PER_LIBRARY,
        )

        monkeypatch.setenv("ABICHECK_MAX_RELEASE_FINDINGS_PER_LIBRARY", "18")
        old_dir, new_dir = _many_removals_pair(tmp_path, count=25)
        result = _invoke("compare", str(old_dir), str(new_dir), "-o", "markdown=-")
        assert result.exit_code == 4, result.output
        rendered = sum(
            1
            for line in result.output.splitlines()
            if line.startswith("- **func_removed**")
        )
        assert rendered == MAX_RELEASE_FINDINGS_PER_LIBRARY, result.output
        assert rendered != 18, result.output


class TestOnelineIncludesReleaseGlobalFindings:
    """P2: `format_release_oneline` received only `library_results`, so a
    release whose only break is a bundle or probe-matrix finding printed
    `BREAKING: no changes (0 total)` -- omitting the very findings
    responsible for the verdict it announced."""

    def test_a_release_global_finding_reaches_the_counts(self) -> None:
        """Driven at the renderer with real `Change` objects rather than
        through a staged bundle: what regressed is the fold, and a
        `BundleDiffResult` fixture would test the bundle analyser instead."""
        from abicheck.checker import Verdict
        from abicheck.checker_types import Change
        from abicheck.model.change_catalog.kinds import ChangeKind
        from abicheck.report.release_oneline import (
            format_release_oneline,
            release_global_counts,
        )

        class _FakeMatrix:
            policy = "strict_abi"
            policy_file = None
            changes = [
                Change(
                    kind=ChangeKind.FUNC_REMOVED,
                    symbol="gone",
                    description="removed under one build configuration",
                )
            ]

        counts = release_global_counts(None, _FakeMatrix())
        # Bucketed by the finding's own effective verdict, not by a second
        # classifier: `func_removed` is BREAKING under `strict_abi`.
        assert Verdict.BREAKING.name == "BREAKING"
        assert counts["breaking"] == 1
        assert counts["total"] == 1

        line = format_release_oneline("BREAKING", [], release_global=counts)
        assert "1 breaking" in line, line
        assert "(1 total)" in line, line
        assert "no changes" not in line, line

    def test_no_release_global_findings_changes_nothing(self) -> None:
        """The negative control: with neither result present the line is the
        same one it was before the fold existed."""
        from abicheck.report.release_oneline import (
            format_release_oneline,
            release_global_counts,
        )

        counts = release_global_counts(None, None)
        assert format_release_oneline("NO_CHANGE", [], release_global=counts) == (
            format_release_oneline("NO_CHANGE", [])
        )
