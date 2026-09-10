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

    def test_project_policy_overrides_receipt_shows_project_config_provenance(
        self, tmp_path: Path
    ) -> None:
        """Findings-analysis-fixes review round 5, finding 3: a direct
        `run_compare_request()` call using `CompareRequest.project_policy_
        overrides` (ADR-068 §3 #23's typed-API channel, weaker than an
        explicit `--policy`/pack entry) used to build its receipt with no
        `project=` input at all, so the override's provenance came back
        `API_REQUEST` -- disagreeing with the `PROJECT_CONFIG` layer D7
        actually places it at (the identical fold `classify_compare_pair`
        already applies to the *scoring* `PolicyFile`). Mirrors
        `test_pack_folded_receipt_names_both_real_contributors` above for
        this sibling field."""
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_policy import ChangeKind
        from abicheck.contract_relevance_types import SelectorLayer

        old, new = _write(tmp_path, *_breaking_pair())
        result = self._run(
            old,
            new,
            project_policy_overrides=((ChangeKind.VAR_REMOVED, Verdict.COMPATIBLE),),
        )
        cfg = result.diff.contract_context.evaluation_context.resolved_config
        assert cfg.policy.overrides.get("var_removed") == Verdict.COMPATIBLE
        prov = cfg.provenance["policy.overrides"]
        assert prov.layer is SelectorLayer.PROJECT_CONFIG
        options = {hop.option for hop in prov.selected_by}
        assert "policy.overrides" in options

    def test_project_policy_overrides_still_scores_the_comparison_itself(
        self, tmp_path: Path
    ) -> None:
        """The receipt fix must not regress what actually gets scored --
        `classify_compare_pair` still classifies through the project-config-
        folded `PolicyFile`."""
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_policy import ChangeKind

        old, new = _write(tmp_path, *_breaking_pair())
        result = self._run(
            old,
            new,
            project_policy_overrides=((ChangeKind.FUNC_REMOVED, Verdict.COMPATIBLE),),
        )
        assert result.diff.verdict.name == "COMPATIBLE"

    def test_explicit_policy_file_outranks_project_policy_overrides_in_the_receipt(
        self, tmp_path: Path
    ) -> None:
        """D7: `project_config` is the *weakest* tier -- an explicit
        `--policy <file>` entry for the same kind must keep the file's own
        `EXPLICIT_CLI`/`API_REQUEST` provenance, not be overwritten by the
        project-config value (which never even reaches the merged
        `PolicyFile` for that kind, per `apply_lower_precedence_overrides`'s
        own "whatever's already stated wins" rule)."""
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_policy import ChangeKind
        from abicheck.contract_relevance_types import SelectorLayer

        old, new = _write(tmp_path, *_breaking_pair())
        policy_file_path = tmp_path / "policy.yml"
        policy_file_path.write_text(
            "base_policy: strict_abi\noverrides:\n  func_removed: break\n",
            encoding="utf-8",
        )
        result = self._run(
            old,
            new,
            policy_file_path=policy_file_path,
            project_policy_overrides=((ChangeKind.FUNC_REMOVED, Verdict.COMPATIBLE),),
        )
        cfg = result.diff.contract_context.evaluation_context.resolved_config
        # The file's own value wins -- the project-config value never scores.
        assert cfg.policy.overrides.get("func_removed") == Verdict.BREAKING
        prov = cfg.provenance["policy.overrides"]
        assert prov.layer is not SelectorLayer.PROJECT_CONFIG

    def test_no_change_project_override_is_rejected(self, tmp_path: Path) -> None:
        """Findings-analysis-fixes review round 6, finding 2: a real
        ``.abicheck.yml``/``--pack``/``--policy`` document can never assign
        ``Verdict.NO_CHANGE`` (``policy_file._SEVERITY_MAP`` has no spelling
        for it), so this is reachable only through a typed
        ``CompareRequest.project_policy_overrides`` caller constructing an
        already-parsed pair directly. Left unrejected, the scoring fold
        still applied it (no vocabulary check of its own) while
        ``severity_value_for_verdict`` silently dropped it from the
        persisted receipt -- a comparison that quietly demoted a finding to
        compatible while its own receipt showed no override at all."""
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_policy import ChangeKind
        from abicheck.errors import ValidationError

        old, new = _write(tmp_path, *_breaking_pair())
        with pytest.raises(ValidationError, match="NO_CHANGE"):
            self._run(
                old,
                new,
                project_policy_overrides=(
                    (ChangeKind.FUNC_REMOVED, Verdict.NO_CHANGE),
                ),
            )

    def test_no_change_pack_override_is_rejected(self, tmp_path: Path) -> None:
        """The identical rejection for the sibling ``pack_policy_overrides``
        field -- the same bug class, the same typed-API-only reachability."""
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_policy import ChangeKind
        from abicheck.errors import ValidationError

        old, new = _write(tmp_path, *_breaking_pair())
        with pytest.raises(ValidationError, match="NO_CHANGE"):
            self._run(
                old,
                new,
                pack_policy_overrides=((ChangeKind.FUNC_REMOVED, Verdict.NO_CHANGE),),
            )

    def test_a_real_verdict_project_override_is_not_rejected(
        self, tmp_path: Path
    ) -> None:
        """Negative control: the new validator must not reject a real,
        ordinary override -- only the nonsensical ``NO_CHANGE`` target."""
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_policy import ChangeKind

        old, new = _write(tmp_path, *_breaking_pair())
        result = self._run(
            old,
            new,
            project_policy_overrides=((ChangeKind.FUNC_REMOVED, Verdict.COMPATIBLE),),
        )
        assert result.diff.verdict.name == "COMPATIBLE"


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

    def test_no_change_project_override_is_rejected_before_extraction_runs(
        self,
    ) -> None:
        """Findings-analysis-fixes review round 7 (Codex review, fresh
        evidence): round 6's NO_CHANGE rejection lived in
        `classify_compare_pair`, which `run_compare_request` only reaches
        *after* `resolve_compare_request` has already run extraction (or
        invoked the build system, for a `build.query`-authorized request)
        against a live operand -- so an invalid request could still perform
        expensive, possibly side-effecting work before eventually failing.
        Moved to `CompareRequest.validation_errors()`, the standard
        pre-resolution hook every other invalid-request shape here already
        uses (see the two sibling tests above). Same proof structure: a
        nonexistent path would raise a filesystem error if extraction ran
        first, so seeing `ValidationError` instead is direct evidence the
        check now runs before it, for a live (not stored-snapshot) operand."""
        from abicheck.api_types import CompareRequest, InputSpec
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_policy import ChangeKind
        from abicheck.errors import ValidationError
        from abicheck.service import run_compare_request

        missing_old = Path("/nonexistent/old.so")
        missing_new = Path("/nonexistent/new.so")
        assert not missing_old.exists()
        assert not missing_new.exists()

        with pytest.raises(ValidationError, match="NO_CHANGE"):
            run_compare_request(
                CompareRequest(
                    old=InputSpec(path=missing_old),
                    new=InputSpec(path=missing_new),
                    project_policy_overrides=(
                        (ChangeKind.FUNC_REMOVED, Verdict.NO_CHANGE),
                    ),
                )
            )

    def test_no_change_pack_override_is_rejected_before_extraction_runs(
        self,
    ) -> None:
        """The identical proof for the sibling `pack_policy_overrides` field."""
        from abicheck.api_types import CompareRequest, InputSpec
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_policy import ChangeKind
        from abicheck.errors import ValidationError
        from abicheck.service import run_compare_request

        missing_old = Path("/nonexistent/old.so")
        missing_new = Path("/nonexistent/new.so")
        assert not missing_old.exists()
        assert not missing_new.exists()

        with pytest.raises(ValidationError, match="NO_CHANGE"):
            run_compare_request(
                CompareRequest(
                    old=InputSpec(path=missing_old),
                    new=InputSpec(path=missing_new),
                    pack_policy_overrides=(
                        (ChangeKind.FUNC_REMOVED, Verdict.NO_CHANGE),
                    ),
                )
            )

    def test_validation_errors_reports_no_change_with_no_resolution_at_all(
        self,
    ) -> None:
        """Even more direct than the two tests above: calling
        `CompareRequest.validation_errors()`/`.validate()` never touches the
        filesystem or any resolution machinery at all -- it's a pure,
        side-effect-free check (the method's own docstring's contract),
        so this proves the rejection is available with zero resolution
        work attempted, not merely "before the extraction step happens to
        run"."""
        from abicheck.api_types import CompareRequest, InputSpec
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_policy import ChangeKind
        from abicheck.errors import ValidationError

        request = CompareRequest(
            old=InputSpec(path=Path("/nonexistent/old.so")),
            new=InputSpec(path=Path("/nonexistent/new.so")),
            project_policy_overrides=((ChangeKind.FUNC_REMOVED, Verdict.NO_CHANGE),),
        )
        errors = request.validation_errors()
        assert any("NO_CHANGE" in e for e in errors)
        with pytest.raises(ValidationError, match="NO_CHANGE"):
            request.validate()


class TestPatternVerdictsStaysOptInAtTier2:
    """Codex review, second look (PR #1154 follow-up: "Obtain ADR approval
    before forcing verdict modulation"): an earlier fix forced
    `pattern_verdicts=True` unconditionally at `classify_compare_pair` and
    at `workflows.compare_policy.compare_snapshots` itself, citing ADR-068
    D4's "no legitimate off position" principle the same way
    `cross_source_checks` earns it. That citation doesn't hold: ADR-068 is
    "Proposed -- not implemented", not an accepted decision, while the ADR
    that *is* accepted (ADR-027) explicitly defers flipping
    `--pattern-verdicts` to default-on until a release cycle's worth of
    FP-rate and parity validation. So both chokepoints were reverted to
    forward the request's/caller's own `pattern_verdicts` value again --
    these tests pin that a bare `CompareRequest()`/`compare_snapshots()`
    call keeps modulation off, matching the accepted opt-in default, while
    `surface_metrics` (pre-existing, unaffected by this correction) stays
    unconditional."""

    def test_default_request_leaves_pattern_verdicts_off(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.service as service_mod
        from abicheck.api_types import CompareRequest, InputSpec
        from abicheck.service import run_compare_request

        old, new = _write(tmp_path, *_breaking_pair())

        real_compare_snapshots = service_mod.compare_snapshots
        seen_pattern_verdicts: list[object] = []

        def _spy_compare_snapshots(old_snap, new_snap, *args, **kwargs):
            seen_pattern_verdicts.append(kwargs.get("pattern_verdicts"))
            return real_compare_snapshots(old_snap, new_snap, *args, **kwargs)

        monkeypatch.setattr(service_mod, "compare_snapshots", _spy_compare_snapshots)

        request = CompareRequest(old=InputSpec(path=old), new=InputSpec(path=new))
        assert request.pattern_verdicts is False
        run_compare_request(request)

        assert seen_pattern_verdicts == [False]

    def test_request_can_still_opt_in(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.service as service_mod
        from abicheck.api_types import CompareRequest, InputSpec
        from abicheck.service import run_compare_request

        old, new = _write(tmp_path, *_breaking_pair())

        real_compare_snapshots = service_mod.compare_snapshots
        seen_pattern_verdicts: list[object] = []

        def _spy_compare_snapshots(old_snap, new_snap, *args, **kwargs):
            seen_pattern_verdicts.append(kwargs.get("pattern_verdicts"))
            return real_compare_snapshots(old_snap, new_snap, *args, **kwargs)

        monkeypatch.setattr(service_mod, "compare_snapshots", _spy_compare_snapshots)

        request = CompareRequest(
            old=InputSpec(path=old), new=InputSpec(path=new), pattern_verdicts=True
        )
        run_compare_request(request)

        assert seen_pattern_verdicts == [True]


class TestSurfaceMetricsIsUnconditionalAtTheTier2Verb:
    """`surface_metrics` (pre-existing, unaffected by the pattern_verdicts
    correction above) stays forced unconditionally at `workflows.
    compare_policy.compare_snapshots` -- ADR-027 Phase 5's `--surface-
    metrics` findings, so a direct caller of the public API (bypassing the
    CLI/release drivers that already forced it at their own call sites)
    still gets them."""

    def test_default_call_forwards_true_for_surface_metrics(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.workflows.compare_policy as compare_policy_module
        from abicheck.service import compare_snapshots

        old, new = _breaking_pair()

        real_compare = compare_policy_module.compare
        seen: list[object] = []

        def _spy_compare(old_snap, new_snap, *args, **kwargs):
            seen.append(kwargs.get("surface_metrics"))
            return real_compare(old_snap, new_snap, *args, **kwargs)

        monkeypatch.setattr(compare_policy_module, "compare", _spy_compare)

        # Bare call -- no surface_metrics kwarg at all, exactly a direct
        # typed-API caller that never learned about the flag.
        compare_snapshots(old, new)

        assert seen == [True]

    def test_the_off_switch_does_not_exist_at_all(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The parameter is gone entirely -- one-comparison-product.md
        Phase 5 took `surface_metrics` off this Tier-2 verb's signature
        rather than leaving it accepted-and-ignored, matching
        `cross_source_checks`'s own "no legitimate off position" precedent.
        Asserting the keyword is *rejected* is the stronger property: an
        accepted-but-ignored parameter is still public surface (ADR-068
        D5), and it is what a caller would reasonably read as an off
        switch."""
        import abicheck.workflows.compare_policy as compare_policy_module
        from abicheck.service import compare_snapshots

        old, new = _breaking_pair()

        real_compare = compare_policy_module.compare
        seen: list[object] = []

        def _spy_compare(old_snap, new_snap, *args, **kwargs):
            seen.append(kwargs.get("surface_metrics"))
            return real_compare(old_snap, new_snap, *args, **kwargs)

        monkeypatch.setattr(compare_policy_module, "compare", _spy_compare)

        with pytest.raises(TypeError, match="surface_metrics"):
            compare_snapshots(old, new, pattern_verdicts=False, surface_metrics=False)
        assert seen == []

        # The analysis itself still runs unconditionally for the same call
        # without the removed keyword.
        compare_snapshots(old, new, pattern_verdicts=False)
        assert seen == [True]
