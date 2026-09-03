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
(ADR-064): `CompareRequest`/`ScanRequest` now carry real
`severity_preset`/`exit_code_scheme` fields (exactly the two flags
single-pair `compare`/`scan --against` themselves expose -- neither has a
per-category `--severity-<category>` CLI flag, only the release fan-out
does, so neither typed request carries a per-category field either),
resolved through the identical `abicheck.policy.release_gate_options.
GateOptions` object the directory/package release fan-out resolves its own
gate configuration from (`resolve_release_gate_options(None, ...)`), rather
than each front end computing its own answer.

Before this: `CompareRequest` had no severity/exit-code-scheme field at
all -- a typed caller always classified through the legacy verdict-based
exit code, with no way to reach the severity-aware scheme `compare
--severity-preset` already gives the CLI. `ScanRequest` had the fields'
CLI-flag counterparts explicitly rejected as "not stated" in its own
receipt-resolution comment, and `run_scan`'s own `run_scan_core` call never
passed `sev_config`/`exit_code_scheme` at all (always the function's own
`None`/`"legacy"` defaults), regardless of `--against`.

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
            exit_code_scheme="severity",
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
            exit_code_scheme="severity",
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
            exit_code_scheme="severity",
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
                "--exit-code-scheme",
                "severity",
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
        """Both fields default to `None` -- a `CompareRequest` built before
        they existed keeps resolving the identical decision."""
        old, new = _write(tmp_path, *_breaking_pair())
        explicit_none = self._run(
            old,
            new,
            severity_preset=None,
            exit_code_scheme=None,
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
            exit_code_scheme="severity",
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
        info_only = self._run(
            old, new, exit_code_scheme="severity", severity_preset="info-only"
        )
        strict = self._run(
            old, new, exit_code_scheme="severity", severity_preset="strict"
        )
        info_cfg = info_only.diff.contract_context.evaluation_context.resolved_config
        strict_cfg = strict.diff.contract_context.evaluation_context.resolved_config
        assert info_cfg.gate.preset.id == "info-only"
        assert strict_cfg.gate.preset.id == "strict"
        assert info_cfg.gate.severity != strict_cfg.gate.severity

    def test_legacy_scheme_still_persists_a_real_severity_config(
        self, tmp_path: Path
    ) -> None:
        """An explicit `exit_code_scheme="legacy"` clears `GateOptions.
        severity` to `None` (`resolve_release_gate_options`'s own
        contract) -- `with_resolved_gate` requires a real `SeverityConfig`
        regardless, so the receipt must not crash or silently omit one."""
        old, new = _write(tmp_path, *_breaking_pair())
        result = self._run(old, new, exit_code_scheme="legacy")
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


class TestScanRequestGateOptions:
    """`ScanRequest.severity_preset`/`exit_code_scheme` -> `run_scan`'s
    `sev_config`/`exit_code_scheme` forward into `run_scan_core` (only
    meaningful with `baseline` set, matching `cli_scan.py`'s own
    `_COMPARISON_ONLY_FLAGS` rule for the identical two CLI flags)."""

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
                exit_code_scheme="severity",
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
                exit_code_scheme="severity",
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
                "--exit-code-scheme",
                "severity",
            ],
        )
        assert cli_result.exit_code == 0, cli_result.output
        assert api_result.exit_code == cli_result.exit_code == 0

    def test_rejected_without_a_baseline_like_the_cli_flags(
        self, tmp_path: Path
    ) -> None:
        """Mirrors `cli_scan._COMPARISON_ONLY_FLAGS`'s identical rejection
        of `--severity-preset`/`--exit-code-scheme` with no `--against`."""
        from abicheck.errors import ValidationError
        from abicheck.service_scan import ScanRequest, run_scan

        _, new = self._pair(tmp_path)
        with pytest.raises(ValidationError, match="severity_preset"):
            run_scan(ScanRequest(binaries=[new], severity_preset="strict"))
        with pytest.raises(ValidationError, match="exit_code_scheme"):
            run_scan(ScanRequest(binaries=[new], exit_code_scheme="severity"))

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
                exit_code_scheme=None,
            )
        )
        omitted = run_scan(ScanRequest(binaries=[new], baseline=old))
        assert explicit_none.exit_code == omitted.exit_code == 4


class TestInvalidExitCodeScheme:
    """`exit_code_scheme` reaches `resolve_release_gate_options` unchecked
    from a typed `CompareRequest`/`ScanRequest` -- unlike the CLI's own
    `--exit-code-scheme` (`click.Choice`) or a pack's `gate.exit_code_scheme`
    (validated at load time), a typed caller has no front-end validation of
    its own (Codex review, PR #1032). Regression for the bug class, not just
    the one reported spelling: a misspelled/mistyped scheme must be rejected
    outright rather than silently falling through the `"severity"`/
    `"legacy"` `==` checks -- which, combined with a `severity_preset` also
    being set, would otherwise silently select the severity algorithm for a
    scheme that was never actually `"severity"` (the exact failure mode
    Codex's finding describes: a breaking change exiting 0 instead of the
    typo being caught)."""

    @pytest.mark.parametrize(
        "bad_scheme",
        [
            "legacy ",  # trailing whitespace -- Codex's own repro
            "Legacy",  # wrong case
            "lgeacy",  # misspelling
            "strict",  # a real severity *preset* name, not a scheme
            "",  # empty string
        ],
    )
    def test_resolve_release_gate_options_rejects_unknown_schemes(
        self, bad_scheme: str
    ) -> None:
        from abicheck.policy.release_gate_options import resolve_release_gate_options

        with pytest.raises(ValueError, match="exit_code_scheme"):
            resolve_release_gate_options(
                None,
                release_exit_code_scheme=bad_scheme,
                severity_preset="info-only",
                severity_abi_breaking=None,
                severity_potential_breaking=None,
                severity_quality_issues=None,
                severity_addition=None,
            )

    @pytest.mark.parametrize("scheme", ["auto", "legacy", "severity", None])
    def test_resolve_release_gate_options_accepts_every_valid_scheme(
        self, scheme: str | None
    ) -> None:
        from abicheck.policy.release_gate_options import resolve_release_gate_options

        # Must not raise.
        gate = resolve_release_gate_options(
            None,
            release_exit_code_scheme=scheme,
            severity_preset=None,
            severity_abi_breaking=None,
            severity_potential_breaking=None,
            severity_quality_issues=None,
            severity_addition=None,
        )
        assert gate.exit_code_scheme == scheme

    def test_scan_request_with_a_misspelled_scheme_fails_fast_not_silently(
        self, tmp_path: Path
    ) -> None:
        """The end-to-end regression this fix closes: before it, a typed
        `ScanRequest(exit_code_scheme="legacy ", severity_preset=...)`
        would silently resolve `GateOptions.severity` to a real config
        (neither `==` branch in `resolve_release_gate_options` matched the
        trailing-whitespace string), so `run_scan_core` would select the
        severity algorithm for a scheme that was never actually
        `"severity"` -- exactly the misclassification risk Codex's finding
        named. It must now raise instead."""
        from abicheck.model import AbiSnapshot
        from abicheck.service_scan import ScanRequest, run_scan

        common = {"library": "libfoo.so.1", "from_headers": True}
        old = AbiSnapshot(
            version="1.0",
            functions=[_fn("pub_a", "_Z5pub_av")],
            elf=_elf("_Z5pub_av"),
            **common,
        )
        new = AbiSnapshot(version="2.0", functions=[], elf=_elf(), **common)
        old_p, new_p = _write(tmp_path, old, new)

        with pytest.raises(ValueError, match="exit_code_scheme"):
            run_scan(
                ScanRequest(
                    binaries=[new_p],
                    baseline=old_p,
                    severity_preset="info-only",
                    exit_code_scheme="legacy ",
                )
            )

    def test_compare_request_rejects_bad_scheme_before_extraction_runs(self) -> None:
        """Round-6 review (Codex, fresh evidence): the fix above closed the
        gap for `resolve_release_gate_options` itself, but
        `classify_compare_pair` only calls it *after*
        `resolve_compare_request` has already resolved both sides -- real
        extraction, which can be slow or run a project-controlled build
        step. A `CompareRequest` must fail on the bad scheme before that
        work starts, not after.

        Proven by pointing both sides at paths that don't exist: extraction
        would raise `SnapshotError`/`OSError` long before any gate option is
        resolved, so seeing `ValidationError` (raised from
        `CompareRequest.validate()`, the first line of
        `resolve_compare_request`) instead of a filesystem error is direct
        evidence validation ran first -- not just that it eventually runs
        somewhere in the pipeline."""
        from abicheck.api_types import CompareRequest, InputSpec
        from abicheck.errors import ValidationError
        from abicheck.service import run_compare_request

        missing_old = Path("/nonexistent/old.abi.json")
        missing_new = Path("/nonexistent/new.abi.json")
        assert not missing_old.exists()
        assert not missing_new.exists()

        with pytest.raises(ValidationError, match="exit_code_scheme"):
            run_compare_request(
                CompareRequest(
                    old=InputSpec(path=missing_old),
                    new=InputSpec(path=missing_new),
                    severity_preset="info-only",
                    exit_code_scheme="legacy ",
                )
            )
