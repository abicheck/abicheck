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

"""CLI cleanup phase two, PR G2's "typed-API half of the parity pass"
(ADR-064): `CompareRequest`/`ScanRequest` now carry a real `severity_preset`
field (the one flag single-pair `compare`/`scan --against` themselves
expose -- neither has a per-category `--severity-<category>` CLI flag, only
the release fan-out does, so neither typed request carries a per-category
field either), resolved through the identical `abicheck.policy.
release_gate_options.GateOptions` object the directory/package release
fan-out resolves its own gate configuration from
(`resolve_release_gate_options(None, ...)`), rather than each front end
computing its own answer.

Before this: `CompareRequest` had no severity field at all -- a typed
caller always classified through the legacy verdict-based exit code, with
no way to reach the severity-aware scheme `compare --severity-preset`
already gives the CLI. `ScanRequest` had the field's CLI-flag counterpart
explicitly rejected as "not stated" in its own receipt-resolution comment,
and `run_scan`'s own `run_scan_core` call never passed `sev_config` at all
(always the function's own `None` default, resolving to the legacy exit
code), regardless of `--against`.

This module originally also covered a sibling `exit_code_scheme` field on
both typed requests -- the manual algorithm selector, mirroring the CLI's
`--exit-code-scheme`. CLI cleanup phase two PR G2 deleted that selector
everywhere: the one automatic gate algorithm is now fully determined by
whether `severity_preset` (or any other severity setting) is in effect, so
there is no longer a second field to resolve, forward, or validate --
`TestInvalidExitCodeScheme` below is what remains of that coverage,
narrowed to the primitive `resolve_release_gate_options` itself no longer
accepting a scheme override at all.

Two things are proven per request type, per the "bug fix's regression test
targets the bug class" contract (AGENTS.md):

1. **The severity fields actually change the exit-code decision** (not just
   resolve into a receipt nobody reads) -- a real regression assertion, not
   a resolution-shaped one alone.
2. **The typed-API result agrees with the CLI's own exit code for
   equivalent input** -- the parity claim itself, checked against the real
   `compare`/`scan` CLI through `CliRunner`, not against the same helper
   the implementation uses internally.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


def _fn(name: str, mangled: str) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="int",
        visibility=Visibility.PUBLIC,
    )


def _elf(*names: str) -> ElfMetadata:
    return ElfMetadata(symbols=[ElfSymbol(name=n) for n in names])


def _breaking_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    """A removed public function -- a hard ABI break under every default."""
    common = {"library": "libfoo.so.1", "from_headers": True}
    fns_old = [_fn("pub_a", "_Z5pub_av"), _fn("pub_b", "_Z5pub_bv")]
    fns_new = [_fn("pub_a", "_Z5pub_av")]
    return (
        AbiSnapshot(
            version="1.0",
            functions=fns_old,
            elf=_elf("_Z5pub_av", "_Z5pub_bv"),
            **common,
        ),
        AbiSnapshot(version="2.0", functions=fns_new, elf=_elf("_Z5pub_av"), **common),
    )


def _write(tmp_path: Path, old: AbiSnapshot, new: AbiSnapshot) -> tuple[Path, Path]:
    old_p = tmp_path / "old.json"
    new_p = tmp_path / "new.json"
    old_p.write_text(snapshot_to_json(old), encoding="utf-8")
    new_p.write_text(snapshot_to_json(new), encoding="utf-8")
    return old_p, new_p


class TestCompareRequestGateOptions:
    """`CompareRequest.severity_preset`/`exit_code_scheme` ->
    `CompareResult.exit_decision` (`service_compare_pipeline.
    classify_compare_pair`)."""

    def _run(self, old: Path, new: Path, **kwargs):
        from abicheck.api_types import CompareRequest, InputSpec
        from abicheck.service import run_compare_request

        return run_compare_request(
            CompareRequest(old=InputSpec(path=old), new=InputSpec(path=new), **kwargs)
        )

    def test_default_reproduces_the_legacy_verdict_based_exit(
        self, tmp_path: Path
    ) -> None:
        old, new = _write(tmp_path, *_breaking_pair())
        result = self._run(old, new)
        assert result.exit_decision is not None
        assert result.exit_decision.code == 4
        assert result.diff.verdict.name == "BREAKING"

    def test_severity_scheme_actually_changes_the_decision(
        self, tmp_path: Path
    ) -> None:
        """Regression assertion #1: setting the fields is not a no-op."""
        old, new = _write(tmp_path, *_breaking_pair())
        legacy = self._run(old, new)
        demoted = self._run(
            old,
            new,
            severity_preset="info-only",
        )
        assert legacy.exit_decision.code == 4
        assert demoted.exit_decision.code == 0
        from abicheck.policy.exit_decision import ExitReason

        assert demoted.exit_decision.reasons == (ExitReason.CLEAN,)

    def test_severity_preset_strict_keeps_the_same_decision(
        self, tmp_path: Path
    ) -> None:
        """The `strict` preset floors everything at error -- proving the
        preset alone selects the severity axis and scores it correctly,
        not just "any config demotes"."""
        old, new = _write(tmp_path, *_breaking_pair())
        result = self._run(
            old,
            new,
            severity_preset="strict",
        )
        assert result.exit_decision.code == 4

    def test_agrees_with_the_cli_for_equivalent_input(self, tmp_path: Path) -> None:
        """Regression assertion #2: the parity claim itself, against the
        real `compare` CLI (not the same helper the implementation uses)."""
        old, new = _write(tmp_path, *_breaking_pair())
        api_result = self._run(
            old,
            new,
            severity_preset="info-only",
        )
        cli_result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old),
                str(new),
                "--severity-preset",
                "info-only",
                "--format",
                "json",
            ],
        )
        assert cli_result.exit_code == 0, cli_result.output
        report = json.loads(cli_result.stdout[cli_result.stdout.index("{") :])
        assert api_result.exit_decision.code == report["exit"]["code"] == 0

    def test_default_severity_fields_leave_the_pre_existing_behaviour_unchanged(
        self, tmp_path: Path
    ) -> None:
        """The field defaults to `None` -- a `CompareRequest` built before
        it existed keeps resolving the identical decision. (Its sibling
        `exit_code_scheme` field, deleted in CLI cleanup phase two PR G2,
        used to be checked here too; there is no longer a second field to
        default.)"""
        old, new = _write(tmp_path, *_breaking_pair())
        explicit_none = self._run(
            old,
            new,
            severity_preset=None,
        )
        omitted = self._run(old, new)
        assert explicit_none.exit_decision.code == omitted.exit_decision.code == 4


class TestCompareRequestContractContextGateReceipt:
    """Round-6 review (Codex, fresh evidence, PR #1032): `classify_compare_
    pair` scored `CompareResult.exit_decision` from the request's
    `severity_preset`/`exit_code_scheme`, but never installed the same gate
    onto `result.diff.contract_context.evaluation_context.resolved_config`
    -- so a `contract_evaluation=True` request combined with a non-default
    gate persisted a context whose resolved config still described
    `checker.compare`'s own built-in defaults, disagreeing with the exit
    decision actually computed. Regression proves the receipt now reflects
    the request's real gate, not just that the exit code changed (that half
    was already covered by `TestCompareRequestGateOptions` above)."""

    def _run(self, old: Path, new: Path, **kwargs):
        from abicheck.api_types import CompareRequest, InputSpec
        from abicheck.service import run_compare_request

        return run_compare_request(
            CompareRequest(
                old=InputSpec(path=old),
                new=InputSpec(path=new),
                contract_evaluation=True,
                **kwargs,
            )
        )

    def test_resolved_config_reflects_the_requests_own_gate(
        self, tmp_path: Path
    ) -> None:
        old, new = _write(tmp_path, *_breaking_pair())
        result = self._run(
            old,
            new,
            severity_preset="info-only",
        )
        ctx = result.diff.contract_context
        assert ctx is not None
        cfg = ctx.evaluation_context.resolved_config
        assert cfg.gate.exit_code_scheme == "severity"
        assert cfg.gate.preset is not None
        assert cfg.gate.preset.id == "info-only"
        # The receipt's severity config must be the one that actually
        # demoted the exit code, not a coincidentally-matching default.
        from abicheck.policy.severity import SeverityLevel

        assert cfg.gate.severity.abi_breaking == SeverityLevel.INFO
        # And it must agree with the compatibility contribution the same
        # gate actually scored (the run's overall exit code also carries an
        # orthogonal contract-coverage contribution here, since no
        # `--contract` domain was selected -- see `ExitDecision.
        # compatibility_contribution`, the axis this gate controls).
        assert result.exit_decision.compatibility_contribution == 0

    def test_resolved_config_disagrees_between_two_different_gates(
        self, tmp_path: Path
    ) -> None:
        """A second regression angle: two requests differing only in
        `severity_preset` must persist two different receipts, not the same
        default both times (which a no-op fix could still pass if the
        default preset happened to match one of the two)."""
        old, new = _write(tmp_path, *_breaking_pair())
        info_only = self._run(old, new, severity_preset="info-only")
        strict = self._run(old, new, severity_preset="strict")
        info_cfg = info_only.diff.contract_context.evaluation_context.resolved_config
        strict_cfg = strict.diff.contract_context.evaluation_context.resolved_config
        assert info_cfg.gate.preset.id == "info-only"
        assert strict_cfg.gate.preset.id == "strict"
        assert info_cfg.gate.severity != strict_cfg.gate.severity

    def test_legacy_scheme_still_persists_a_real_severity_config(
        self, tmp_path: Path
    ) -> None:
        """No severity setting in effect resolves `GateOptions.severity` to
        `None` (`resolve_release_gate_options`'s own contract) --
        `with_resolved_gate` requires a real `SeverityConfig` regardless, so
        the receipt must not crash or silently omit one. (Before CLI
        cleanup phase two PR G2 this was reached via an explicit
        `exit_code_scheme="legacy"`, which forced the identical outcome
        even with a severity setting present; that override no longer
        exists, so the no-setting-at-all case now covers it.)"""
        old, new = _write(tmp_path, *_breaking_pair())
        result = self._run(old, new)
        cfg = result.diff.contract_context.evaluation_context.resolved_config
        assert cfg.gate.exit_code_scheme == "legacy"
        from abicheck.severity import SeverityConfig

        assert isinstance(cfg.gate.severity, SeverityConfig)

    def test_default_request_leaves_the_default_receipt_unchanged(
        self, tmp_path: Path
    ) -> None:
        """No severity/exit_code_scheme fields set -> the receipt still
        resolves (this fix runs unconditionally), and reports the same
        legacy default the pre-fix built-in-default context also claimed --
        so the fix is invisible for every pre-existing `--contract`-only
        caller."""
        old, new = _write(tmp_path, *_breaking_pair())
        result = self._run(old, new)
        cfg = result.diff.contract_context.evaluation_context.resolved_config
        assert cfg.gate.exit_code_scheme == "legacy"

    @pytest.mark.parametrize(
        "severity_preset,expected_scheme",
        [
            (None, "legacy"),  # no severity in effect -> resolves to legacy
            ("info-only", "severity"),  # a real preset -> resolves to severity
        ],
    )
    def test_scheme_resolves_without_crashing_the_receipt_either_way(
        self, tmp_path: Path, severity_preset: str | None, expected_scheme: str
    ) -> None:
        """Round-8 review (Codex, fresh evidence): an earlier revision of a
        now-superseded fix installed `gate.exit_code_scheme` onto the
        receipt before it was fully resolved, and `with_resolved_gate`'s own
        `GateConfig` only accepts `"legacy"`/`"severity"` -- a request that
        should have resolved cleanly raised `ValueError` from *inside* the
        receipt-install step, after the comparison had already completed.
        Must resolve cleanly to the same value `exit_decision` itself was
        scored with, for both the no-severity-in-effect and a real-preset
        case. (CLI cleanup phase two PR G2 removed the `exit_code_scheme`
        field this test used to pass `"auto"` through -- the derivation is
        unconditional now, so there is no longer a distinct "auto" input to
        exercise, only the two outcomes.)"""
        old, new = _write(tmp_path, *_breaking_pair())
        result = self._run(old, new, severity_preset=severity_preset)
        cfg = result.diff.contract_context.evaluation_context.resolved_config
        assert cfg.gate.exit_code_scheme == expected_scheme

    def test_receipt_reuses_the_suppression_that_scored_the_comparison(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Round-8 review (Codex, fresh evidence): the gate-receipt adapter
        must not re-read `request.suppress` from disk a second time -- the
        digest could then describe different content than what actually
        scored the findings if the file changed between the two reads (or
        simply waste a redundant read every time). Proven by making a
        second read fail outright: `SuppressionSource.from_file` is
        monkeypatched to raise, so this only passes if the receipt path
        builds its `SuppressionSource` from the already-loaded
        `SuppressionList` (`.from_loaded`) instead."""
        from abicheck.compatibility_evaluation_frontend import SuppressionSource

        def _boom(*args: object, **kwargs: object) -> None:
            raise AssertionError(
                "SuppressionSource.from_file called -- the gate-receipt "
                "adapter re-read the suppression file instead of reusing "
                "the SuppressionList that already scored the comparison"
            )

        monkeypatch.setattr(SuppressionSource, "from_file", _boom)

        old, new = _write(tmp_path, *_breaking_pair())
        suppress_path = tmp_path / "suppress.yml"
        suppress_path.write_text("version: 1\n", encoding="utf-8")

        result = self._run(
            old,
            new,
            severity_preset="info-only",
            suppress=suppress_path,
        )
        # Must still resolve a real receipt, not merely avoid crashing.
        cfg = result.diff.contract_context.evaluation_context.resolved_config
        assert cfg.gate.exit_code_scheme == "severity"

    def test_a_policy_file_overrides_an_unknown_policy_name(
        self, tmp_path: Path
    ) -> None:
        """Round-10 review (Codex, fresh evidence): `load_suppression_and_
        policy` accepts a request pairing an unknown `policy` name with a
        valid `policy_file_path` -- the file wins, the name chose nothing.
        The gate-receipt installer must not forward that ignored name to
        `builtin_policy_identity`, which raises for anything outside
        `VALID_BASE_POLICIES` -- that would turn an otherwise-completed
        comparison into a receipt-install failure. Same fix already applied
        on the scan side (`test_scan_compare_parity.py`'s identically-named
        test)."""
        old, new = _write(tmp_path, *_breaking_pair())
        policy_file_path = tmp_path / "policy.yml"
        policy_file_path.write_text("base_policy: sdk_vendor\n", encoding="utf-8")
        result = self._run(
            old,
            new,
            policy="not_a_real_policy",
            policy_file_path=policy_file_path,
        )
        cfg = result.diff.contract_context.evaluation_context.resolved_config
        assert cfg.policy.base.id == "sdk_vendor"

    def test_pack_folded_overrides_do_not_falsely_claim_a_policy_file(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence, twice over: `classify_compare_pair`
        folds `CompareRequest.pack_policy_overrides` into the loaded
        `PolicyFile` for scoring. Round 1: passing that merged object to the
        receipt installer misattributed the pack's own override as
        `policy_file_path`-sourced, with the file's real digest (mis)covering
        content it never actually contained. Round 2 (an earlier fix for
        round 1): excluding the pack's contribution from the receipt entirely
        made two requests differing only in `pack_policy_overrides` render
        the *same* `effective_config_digest` despite scoring to different
        verdicts. The receipt must record the pack's override -- so the
        digest reflects it -- without claiming it came from the file."""
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_policy import ChangeKind

        old, new = _write(tmp_path, *_breaking_pair())
        policy_file_path = tmp_path / "policy.yml"
        policy_file_path.write_text(
            "base_policy: strict_abi\noverrides:\n  func_removed: ignore\n",
            encoding="utf-8",
        )
        with_pack = self._run(
            old,
            new,
            policy_file_path=policy_file_path,
            pack_policy_overrides=((ChangeKind.VAR_REMOVED, Verdict.COMPATIBLE),),
        )
        without_pack = self._run(old, new, policy_file_path=policy_file_path)
        cfg = with_pack.diff.contract_context.evaluation_context.resolved_config
        # The file's own override is real receipt content...
        assert cfg.policy.overrides.get("func_removed") == Verdict.COMPATIBLE
        # ...and so is the pack's -- the digest must be able to tell the two
        # requests apart, which requires the override to actually be there.
        assert cfg.policy.overrides.get("var_removed") == Verdict.COMPATIBLE
        cfg_without = (
            without_pack.diff.contract_context.evaluation_context.resolved_config
        )
        assert cfg.policy.overrides != cfg_without.policy.overrides

    def test_pack_folding_still_scores_the_comparison_itself(
        self, tmp_path: Path
    ) -> None:
        """The receipt fix must not regress what actually gets scored --
        `classify_compare_pair` still classifies through the pack-folded
        `PolicyFile`, so a pack override still demotes the real verdict."""
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_policy import ChangeKind

        old, new = _write(tmp_path, *_breaking_pair())
        result = self._run(
            old,
            new,
            pack_policy_overrides=((ChangeKind.FUNC_REMOVED, Verdict.COMPATIBLE),),
        )
        assert result.diff.verdict.name == "COMPATIBLE"

    def test_pack_folded_receipt_names_both_real_contributors(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence, round 3: an intermediate revision
        of this fix cleared the real file's own `path`/`sha256` whenever a
        pack also contributed, to avoid crediting the file with the pack's
        override -- but that threw away the file's own, independently-real
        identity too. The correct receipt shape (mirroring
        `_overrides_provenance`'s existing treatment of a real `--pack
        <path>` manifest) names the file *and* records the forwarded pack's
        own contribution as a distinct `selected_by` hop -- neither
        overwriting the other."""
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_policy import ChangeKind

        old, new = _write(tmp_path, *_breaking_pair())
        policy_file_path = tmp_path / "policy.yml"
        policy_file_path.write_text("base_policy: strict_abi\n", encoding="utf-8")
        result = self._run(
            old,
            new,
            policy_file_path=policy_file_path,
            pack_policy_overrides=((ChangeKind.VAR_REMOVED, Verdict.COMPATIBLE),),
        )
        cfg = result.diff.contract_context.evaluation_context.resolved_config
        prov = cfg.provenance["policy.overrides"]
        # The file's own identity is preserved, not discarded.
        assert prov.path == str(policy_file_path)
        assert prov.sha256 is not None
        # And the forwarded pack's contribution is recorded as its own hop.
        options = {hop.option for hop in prov.selected_by}
        assert "pack_policy_overrides" in options

    def test_pack_only_internal_namespaces_are_not_credited_to_the_file(
        self, tmp_path: Path
    ) -> None:
        """`surface.internal_namespaces` is a *replace*, not a merge (unlike
        `policy.overrides`): a pack that sets it overwrites the file's own
        value outright, so crediting the file's `path`/`sha256` for it would
        be false whenever a pack actually did -- unlike `policy.overrides`,
        this provenance entry carries no file identity at all."""
        old, new = _write(tmp_path, *_breaking_pair())
        policy_file_path = tmp_path / "policy.yml"
        policy_file_path.write_text(
            "base_policy: strict_abi\ninternal_namespaces:\n  - detail\n",
            encoding="utf-8",
        )
        result = self._run(
            old,
            new,
            policy_file_path=policy_file_path,
            pack_internal_namespaces=("impl",),
        )
        cfg = result.diff.contract_context.evaluation_context.resolved_config
        assert cfg.surface.internal_namespaces == ("impl",)
        prov = cfg.provenance["surface.internal_namespaces"]
        assert prov.path is None
        assert prov.sha256 is None
        assert prov.selected_by[0].option == "pack_internal_namespaces"


class TestCompareResultSeverityConfigRenderingParity:
    """Codex review, fresh evidence (PR #1032, commit 72fdf5b, file:line
    `service_compare_pipeline.py:618`): `classify_compare_pair` resolved
    `CompareResult.exit_decision` from the request's own gate, but the typed
    result carried no way for a caller to render a report that agrees with
    it -- `reporter.to_json`/`render_output` default their own
    `severity_config` argument to `None`, silently recomputing a *different*,
    legacy-scheme exit that contradicted `result.exit_decision`. Regression
    proves both that `CompareResult.severity_config` is the same object that
    scored `exit_decision` (not a default that happens to be present), and
    that passing it into `to_json` actually closes the disagreement a caller
    omitting it would still hit."""

    def _run(self, old: Path, new: Path, **kwargs):
        from abicheck.api_types import CompareRequest, InputSpec
        from abicheck.service import run_compare_request

        return run_compare_request(
            CompareRequest(old=InputSpec(path=old), new=InputSpec(path=new), **kwargs)
        )

    def test_severity_config_is_the_gate_that_scored_exit_decision(
        self, tmp_path: Path
    ) -> None:
        old, new = _write(tmp_path, *_breaking_pair())
        result = self._run(
            old,
            new,
            severity_preset="info-only",
        )
        assert result.severity_config is not None
        from abicheck.policy.severity import SeverityLevel

        assert result.severity_config.abi_breaking == SeverityLevel.INFO
        assert result.exit_decision.code == 0

    def test_default_legacy_request_leaves_severity_config_none(
        self, tmp_path: Path
    ) -> None:
        old, new = _write(tmp_path, *_breaking_pair())
        result = self._run(old, new)
        assert result.severity_config is None
        assert result.exit_decision.code == 4

    def test_omitting_severity_config_from_to_json_would_disagree(
        self, tmp_path: Path
    ) -> None:
        """The bug itself: a caller forwarding nothing (the pre-fix default
        every renderer already had) gets a report that contradicts
        `exit_decision` -- this is what makes the next test's fix a real
        fix, not a no-op the same JSON would've produced anyway."""
        old, new = _write(tmp_path, *_breaking_pair())
        result = self._run(
            old,
            new,
            severity_preset="info-only",
        )
        assert result.exit_decision.code == 0
        from abicheck.reporter import to_json

        report = json.loads(to_json(result.diff))
        assert report["exit"]["code"] == 4
        assert report["exit"]["code"] != result.exit_decision.code

    def test_passing_severity_config_into_to_json_agrees_with_exit_decision(
        self, tmp_path: Path
    ) -> None:
        old, new = _write(tmp_path, *_breaking_pair())
        result = self._run(
            old,
            new,
            severity_preset="info-only",
        )
        from abicheck.reporter import to_json

        report = json.loads(
            to_json(result.diff, severity_config=result.severity_config)
        )
        assert report["exit"]["code"] == result.exit_decision.code == 0
        assert "severity" in report


class TestRunCompareForwardsGateOptions:
    """Codex review, fresh evidence (PR #1032, `service_compare_pipeline.py:
    606`): ADR-064/PR G2 added `severity_preset`/`exit_code_scheme` to
    `CompareRequest`/`CompareResult`, but the supported `abicheck.service.
    run_compare()` kwargs shim never grew matching parameters or forwarded
    them into the `CompareRequest` it builds -- a caller of this documented
    entry point had no way to select the severity-aware gate at all: passing
    the keywords raised `TypeError`, and omitting them silently stayed on
    the legacy verdict-based exit code."""

    def test_the_keywords_are_accepted_and_change_the_decision(
        self, tmp_path: Path
    ) -> None:
        from abicheck.service import run_compare

        old, new = _write(tmp_path, *_breaking_pair())
        legacy = run_compare(old, new)
        demoted = run_compare(old, new, severity_preset="info-only")
        assert legacy.exit_decision.code == 4
        assert demoted.exit_decision.code == 0
        assert demoted.severity_config is not None

    def test_agrees_with_run_compare_request_for_equivalent_input(
        self, tmp_path: Path
    ) -> None:
        from abicheck.api_types import CompareRequest, InputSpec
        from abicheck.service import run_compare, run_compare_request

        old, new = _write(tmp_path, *_breaking_pair())
        shim_result = run_compare(old, new, severity_preset="info-only")
        typed_result = run_compare_request(
            CompareRequest(
                old=InputSpec(path=old),
                new=InputSpec(path=new),
                severity_preset="info-only",
            )
        )
        assert shim_result.exit_decision.code == typed_result.exit_decision.code == 0


class TestScanRequestGateOptions:
    """`ScanRequest.severity_preset` -> `run_scan`'s `sev_config` forwards
    into `run_scan_core` (only meaningful with `baseline` set, matching
    `cli_scan.py`'s own `_COMPARISON_ONLY_FLAGS` rule for the identical CLI
    flag). Its sibling `exit_code_scheme` field/flag, deleted in CLI cleanup
    phase two PR G2, used to be covered here too -- there is no longer a
    second field to forward, reject, or validate."""

    def _pair(self, tmp_path: Path) -> tuple[Path, Path]:
        return _write(tmp_path, *_breaking_pair())

    def test_default_reproduces_the_legacy_exit_code(self, tmp_path: Path) -> None:
        from abicheck.service_scan import ScanRequest, run_scan

        old, new = self._pair(tmp_path)
        result = run_scan(ScanRequest(binaries=[new], baseline=old))
        assert result.exit_code == 4

    def test_severity_scheme_actually_changes_the_exit_code(
        self, tmp_path: Path
    ) -> None:
        """Regression assertion #1: not a no-op field."""
        from abicheck.service_scan import ScanRequest, run_scan

        old, new = self._pair(tmp_path)
        legacy = run_scan(ScanRequest(binaries=[new], baseline=old))
        demoted = run_scan(
            ScanRequest(
                binaries=[new],
                baseline=old,
                severity_preset="info-only",
            )
        )
        assert legacy.exit_code == 4
        assert demoted.exit_code == 0

    def test_agrees_with_the_cli_for_equivalent_input(self, tmp_path: Path) -> None:
        """Regression assertion #2: parity against the real `scan --against`
        CLI invocation, not the implementation's own helper."""
        from abicheck.service_scan import ScanRequest, run_scan

        old, new = self._pair(tmp_path)
        api_result = run_scan(
            ScanRequest(
                binaries=[new],
                baseline=old,
                severity_preset="info-only",
            )
        )
        cli_result = CliRunner().invoke(
            main,
            [
                "scan",
                str(new),
                "--against",
                str(old),
                "--severity-preset",
                "info-only",
            ],
        )
        assert cli_result.exit_code == 0, cli_result.output
        assert api_result.exit_code == cli_result.exit_code == 0

    def test_rejected_without_a_baseline_like_the_cli_flags(
        self, tmp_path: Path
    ) -> None:
        """Mirrors `cli_scan._COMPARISON_ONLY_FLAGS`'s identical rejection
        of `--severity-preset` with no `--against`."""
        from abicheck.errors import ValidationError
        from abicheck.service_scan import ScanRequest, run_scan

        _, new = self._pair(tmp_path)
        with pytest.raises(ValidationError, match="severity_preset"):
            run_scan(ScanRequest(binaries=[new], severity_preset="strict"))

    def test_invalid_gate_fields_raise_validation_error(self, tmp_path: Path) -> None:
        """CodeRabbit review, fresh evidence, PR #1032: `run_scan` called
        `resolve_scan_gate_options` -> `resolve_release_gate_options` with
        no exception translation at all -- an invalid `severity_preset`
        raised `PolicyError` (a `ValueError` subclass, from
        `resolve_severity_config`), not `ValidationError`, the type every
        other malformed-`ScanRequest` field raises. A Tier-2 caller guarding
        `run_scan` with `except ValidationError` -- the documented contract
        -- would miss it and see the raw exception instead. Fixed by
        translating it at the `resolve_scan_gate_options` call site,
        mirroring the existing `_resolve_scan_contract_config` ->
        `resolve_scan_config` translation just above it in
        `service_scan.py`. (Its sibling assertion for an invalid
        `exit_code_scheme` was removed along with the field itself, CLI
        cleanup phase two PR G2.)"""
        from abicheck.errors import ValidationError
        from abicheck.service_scan import ScanRequest, run_scan

        old, new = self._pair(tmp_path)
        with pytest.raises(ValidationError):
            run_scan(
                ScanRequest(
                    binaries=[new], baseline=old, severity_preset="not-a-preset"
                )
            )

    def test_default_severity_fields_leave_the_pre_existing_behaviour_unchanged(
        self, tmp_path: Path
    ) -> None:
        from abicheck.service_scan import ScanRequest, run_scan

        old, new = self._pair(tmp_path)
        explicit_none = run_scan(
            ScanRequest(
                binaries=[new],
                baseline=old,
                severity_preset=None,
            )
        )
        omitted = run_scan(ScanRequest(binaries=[new], baseline=old))
        assert explicit_none.exit_code == omitted.exit_code == 4


class TestInvalidExitCodeScheme:
    """Historically: `exit_code_scheme` reached `resolve_release_gate_
    options` unchecked from a typed `CompareRequest`/`ScanRequest` -- unlike
    the CLI's own `--exit-code-scheme` (`click.Choice`) or a pack's
    `gate.exit_code_scheme` (validated at load time), a typed caller had no
    front-end validation of its own (Codex review, PR #1032), so a
    misspelled/mistyped scheme could silently fall through the
    `"severity"`/`"legacy"` `==` checks and, combined with a
    `severity_preset` also being set, select the severity algorithm for a
    scheme that was never actually `"severity"`.

    CLI cleanup phase two PR G2 deleted `exit_code_scheme` -- the field, the
    CLI flag, the `.abicheck.yml` key, and the pack field -- everywhere, so
    that whole misclassification class no longer has an input to trigger it
    at all: there is no longer a scheme value for a caller to misspell.
    `test_resolve_release_gate_options_no_longer_accepts_a_scheme_argument`
    below is the regression proving the capability is actually gone, not
    merely unexercised; the remaining tests here are this class's still-live
    `severity_preset`/`policy` fail-fast siblings, unaffected by the
    removal."""

    def test_resolve_release_gate_options_no_longer_accepts_a_scheme_argument(
        self,
    ) -> None:
        """The primitive itself has no scheme parameter to pass any more --
        proving the removal reaches the actual function signature, not just
        its typed-API callers."""
        import inspect

        from abicheck.policy.release_gate_options import resolve_release_gate_options

        params = inspect.signature(resolve_release_gate_options).parameters
        assert "release_exit_code_scheme" not in params
        assert "exit_code_scheme" not in params
        with pytest.raises(TypeError, match="exit_code_scheme"):
            resolve_release_gate_options(  # type: ignore[call-arg]
                None,
                release_exit_code_scheme="legacy",
                severity_preset=None,
                severity_abi_breaking=None,
                severity_potential_breaking=None,
                severity_quality_issues=None,
                severity_addition=None,
            )

    def test_compare_request_no_longer_has_an_exit_code_scheme_field(self) -> None:
        """Same proof, one layer up: a typed `CompareRequest` cannot even be
        constructed with the deleted field any more."""
        from abicheck.api_types import CompareRequest, InputSpec

        with pytest.raises(TypeError, match="exit_code_scheme"):
            CompareRequest(  # type: ignore[call-arg]
                old=InputSpec(path=Path("old.abi.json")),
                new=InputSpec(path=Path("new.abi.json")),
                exit_code_scheme="legacy",
            )

    def test_scan_request_no_longer_has_an_exit_code_scheme_field(self) -> None:
        from abicheck.service_scan import ScanRequest

        with pytest.raises(TypeError, match="exit_code_scheme"):
            ScanRequest(binaries=[Path("new.so")], exit_code_scheme="legacy")  # type: ignore[call-arg]

    def test_compare_request_rejects_bad_preset_before_extraction_runs(self) -> None:
        """CodeRabbit review, fresh evidence: a misspelled preset (e.g.
        `"strcit"`) must fail before extraction runs, not later, inside
        `classify_compare_pair`'s `resolve_release_gate_options` call, once
        extraction had already run. Proven by pointing both sides at paths
        that don't exist: extraction would raise `SnapshotError`/`OSError`
        long before any gate option is resolved, so seeing `ValidationError`
        (raised from `CompareRequest.validate()`, the first line of
        `resolve_compare_request`) instead of a filesystem error is direct
        evidence the check now runs before it."""
        from abicheck.api_types import CompareRequest, InputSpec
        from abicheck.errors import ValidationError
        from abicheck.service import run_compare_request

        missing_old = Path("/nonexistent/old.abi.json")
        missing_new = Path("/nonexistent/new.abi.json")
        assert not missing_old.exists()
        assert not missing_new.exists()

        with pytest.raises(ValidationError, match="severity_preset"):
            run_compare_request(
                CompareRequest(
                    old=InputSpec(path=missing_old),
                    new=InputSpec(path=missing_new),
                    severity_preset="strcit",
                )
            )

    def test_compare_request_rejects_bad_policy_before_extraction_runs(
        self,
    ) -> None:
        """Round-11 review (Codex, fresh evidence): the policy_file_path
        override fix (`stated_policy_base`) only covers the case where a
        file overrides an unknown `policy` name -- a `CompareRequest`
        naming an unknown policy with *no* `policy_file_path` at all still
        only failed later, inside `builtin_policy_identity`, after
        extraction had already run. Same proof structure as the sibling
        tests above: a nonexistent path would raise a filesystem error if
        extraction ran first, so seeing `ValidationError` instead is direct
        evidence the check now runs before it."""
        from abicheck.api_types import CompareRequest, InputSpec
        from abicheck.errors import ValidationError
        from abicheck.service import run_compare_request

        missing_old = Path("/nonexistent/old.abi.json")
        missing_new = Path("/nonexistent/new.abi.json")
        assert not missing_old.exists()
        assert not missing_new.exists()

        with pytest.raises(ValidationError, match="unknown policy"):
            run_compare_request(
                CompareRequest(
                    old=InputSpec(path=missing_old),
                    new=InputSpec(path=missing_new),
                    policy="not_a_policy",
                )
            )
