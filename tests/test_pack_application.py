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

"""ADR-049 D8: a selected pack must configure the run, not just the receipt.

The `--pack` flag was written and reverted once before merge because it
reached the resolved configuration and never the engine -- and the parity
tests written alongside it passed, because they asserted the two commands
*resolve* packs identically and never that a pack changes a result. A flag
that does nothing satisfies that.

So the first assertion of every behavioural test here is an **exit code or a
verdict that differs with and without the pack**. Resolution-shaped
assertions (provenance, receipt equality) come second, and only ever
alongside one of those.
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


@pytest.fixture
def pair(tmp_path: Path) -> tuple[Path, Path]:
    """A pair whose only change is one removed exported function.

    Deliberately the plainest possible break: every test below is about what
    the *configuration* does to it, so the finding itself must not be in
    question.
    """
    old = AbiSnapshot(
        library="libfoo.so.1",
        version="1.0",
        functions=[_fn("api_a", "_Z5api_av"), _fn("api_b", "_Z5api_bv")],
        from_headers=True,
    )
    new = AbiSnapshot(
        library="libfoo.so.1",
        version="2.0",
        functions=[_fn("api_a", "_Z5api_av")],
        from_headers=True,
    )
    old_p = tmp_path / "old.json"
    new_p = tmp_path / "new.json"
    old_p.write_text(snapshot_to_json(old), encoding="utf-8")
    new_p.write_text(snapshot_to_json(new), encoding="utf-8")
    return old_p, new_p


def _pack(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture
def ignore_removals(tmp_path: Path) -> Path:
    return _pack(
        tmp_path,
        "ignore-removals.yml",
        "id: relax_removals\nversion: 1\nkind: policy\n"
        "assignments:\n  func_removed: ignore\n",
    )


def _compare(runner: CliRunner, pair: tuple[Path, Path], *extra: str):
    old, new = pair
    return runner.invoke(main, ["compare", str(old), str(new), *extra])


def _field_kind(field: str):
    """The `PackKind` whose route table declares *field*."""
    from abicheck.compatibility_evaluation_wiring import PACK_FIELD_ROUTES_BY_KIND

    for kind, routes in PACK_FIELD_ROUTES_BY_KIND.items():
        if field in routes:
            return kind
    raise AssertionError(f"{field} is in no pack route table")


#: One route-valid YAML value per unapplied field. Hand-written because each
#: route accepts a different shape, but the *keys* are asserted against
#: `UNAPPLIED_PACK_FIELDS` below, so a registry entry without a value here
#: fails rather than silently dropping out of the parametrize list.
_UNAPPLIED_FIELD_VALUES: dict[str, str] = {
    "contract.unresolved": "warn",
    "contract.overlays": "[post_manifest]",
    "assurance.require_evidence": "false",
}


class TestAPackActuallyConfiguresTheRun:
    """The reason the first `--pack` was reverted: it configured nothing."""

    def test_a_policy_pack_changes_the_verdict_and_the_exit_code(
        self, pair: tuple[Path, Path], ignore_removals: Path
    ) -> None:
        runner = CliRunner()
        without = _compare(runner, pair, "--format", "json")
        assert without.exit_code == 4, without.output

        with_pack = _compare(
            runner, pair, "--format", "json", "--pack", str(ignore_removals)
        )
        assert with_pack.exit_code == 0, with_pack.output
        assert json.loads(with_pack.output)["verdict"] == "COMPATIBLE"

    def test_a_policy_pack_changes_a_scan_against_the_same_way(
        self, pair: tuple[Path, Path], ignore_removals: Path
    ) -> None:
        old, new = pair
        runner = CliRunner()
        base = ["scan", str(new), "--against", str(old), "--format", "json"]
        assert runner.invoke(main, base).exit_code == 4
        assert (
            runner.invoke(main, [*base, "--pack", str(ignore_removals)]).exit_code == 0
        )

    def test_a_gate_pack_severity_moves_the_run_onto_the_severity_scheme(
        self, pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """A severity level *is* severity being configured.

        Without flipping `severity_active`, a gate pack's level would resolve,
        be reported, and then be scored under the legacy scheme that never
        reads it -- decoration again, one layer deeper.
        """
        gate = _pack(
            tmp_path,
            "lenient.yml",
            "id: lenient\nversion: 1\nkind: gate\n"
            "assignments:\n  gate.severity.abi_breaking: warning\n",
        )
        result = _compare(CliRunner(), pair, "--format", "json", "--pack", str(gate))
        assert result.exit_code == 0, result.output
        # The finding is still reported -- only the gate moved.
        assert json.loads(result.output)["verdict"] == "BREAKING"

    def test_a_contract_pack_internal_namespace_reaches_the_comparison(
        self, tmp_path: Path
    ) -> None:
        """`surface.internal_namespaces` is the one contract-pack field with a
        live consumer, so it is the one a contract pack may assign."""
        from abicheck.compatibility_evaluation_frontend import (
            ExplicitCompatibilityInputs,
            FrontEnd,
            resolve_compatibility_evaluation_config,
        )
        from abicheck.compatibility_evaluation_packs import PackKind
        from abicheck.pack_application import (
            applied_pack_fields,
            pack_application,
            policy_file_with_packs,
        )

        assert applied_pack_fields(PackKind.CONTRACT) == {"surface.internal_namespaces"}
        pack = _pack(
            tmp_path,
            "ns.yml",
            "id: ns\nversion: 1\nkind: contract\n"
            "assignments:\n  surface.internal_namespaces: [priv]\n",
        )
        config = resolve_compatibility_evaluation_config(
            front_end=FrontEnd.CLI,
            explicit=ExplicitCompatibilityInputs(pack_paths=(str(pack),)),
        )
        folded = policy_file_with_packs(
            None, pack_application(config, policy_file=None), base_policy="strict_abi"
        )
        assert folded is not None
        assert folded.internal_namespaces == ["priv"]
        # An explicit empty list and an unset field are different statements,
        # and a pack that supplied the field made the statement.
        assert folded.internal_namespaces_stated is True


class TestD8Precedence:
    """D8 composition: explicit per-kind override > selected packs > base."""

    def test_an_explicit_policy_file_override_outranks_a_pack(
        self, pair: tuple[Path, Path], ignore_removals: Path, tmp_path: Path
    ) -> None:
        policy_file = tmp_path / "policy.yml"
        policy_file.write_text("overrides:\n  func_removed: break\n", encoding="utf-8")
        result = _compare(
            CliRunner(),
            pair,
            "--format",
            "json",
            "--pack",
            str(ignore_removals),
            "--policy-file",
            str(policy_file),
        )
        assert result.exit_code == 4, result.output

    def test_an_explicit_severity_flag_outranks_a_gate_pack(
        self, pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        gate = _pack(
            tmp_path,
            "lenient.yml",
            "id: lenient\nversion: 1\nkind: gate\n"
            "assignments:\n  gate.severity.abi_breaking: warning\n",
        )
        result = _compare(
            CliRunner(),
            pair,
            "--format",
            "json",
            "--pack",
            str(gate),
            "--severity-abi-breaking",
            "error",
        )
        assert result.exit_code == 4, result.output

    @pytest.mark.parametrize(
        ("scheme_flag", "expected"),
        [
            # An explicitly selected scheme is a stated value; a pack may not
            # move it, whichever way the pack's own levels would point.
            (["--exit-code-scheme", "legacy"], 4),
            (["--exit-code-scheme", "severity"], 0),
            # Nothing stated it, so the pack's own level decides `auto`.
            ([], 0),
        ],
    )
    def test_a_gate_pack_never_overrides_a_stated_exit_code_scheme(
        self,
        pair: tuple[Path, Path],
        tmp_path: Path,
        scheme_flag: list[str],
        expected: int,
    ) -> None:
        """A warning-level gate pack must not silently un-fail a legacy run.

        The first revision re-derived the scheme locally (`"severity" if
        severity_active`), which turned an explicit `--exit-code-scheme
        legacy` BREAKING run's exit 4 into 0 — a pack overriding a stated
        value, which is exactly what D8 forbids (Codex review). The fix reads
        the resolver's own answer instead, so this parametrization pins all
        three directions rather than only the one that regressed.
        """
        gate = _pack(
            tmp_path,
            "lenient.yml",
            "id: lenient\nversion: 1\nkind: gate\n"
            "assignments:\n  gate.severity.abi_breaking: warning\n",
        )
        result = _compare(
            CliRunner(), pair, "--format", "json", "--pack", str(gate), *scheme_flag
        )
        assert result.exit_code == expected, result.output

    def test_two_packs_disagreeing_on_one_field_is_a_usage_error(
        self, pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        first = _pack(
            tmp_path,
            "a.yml",
            "id: a\nversion: 1\nkind: policy\nassignments:\n  func_removed: ignore\n",
        )
        second = _pack(
            tmp_path,
            "b.yml",
            "id: b\nversion: 1\nkind: policy\nassignments:\n  func_removed: warn\n",
        )
        result = _compare(
            CliRunner(), pair, "--pack", str(first), "--pack", str(second)
        )
        assert result.exit_code == 64, result.output

    def test_two_packs_agreeing_on_one_field_is_not_a_conflict(
        self, pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        first = _pack(
            tmp_path,
            "a.yml",
            "id: a\nversion: 1\nkind: policy\nassignments:\n  func_removed: ignore\n",
        )
        second = _pack(
            tmp_path,
            "b.yml",
            "id: b\nversion: 1\nkind: policy\nassignments:\n  func_removed: ignore\n",
        )
        result = _compare(
            CliRunner(),
            pair,
            "--format",
            "json",
            "--pack",
            str(first),
            "--pack",
            str(second),
        )
        assert result.exit_code == 0, result.output

    def test_a_pack_never_resets_an_explicitly_chosen_base_policy(self) -> None:
        """With no `--policy-file` there is nothing to fold into, so one is
        synthesized -- and `checker` reads a present file's `base_policy`
        *instead of* the `policy` argument. Defaulting it would silently move
        a `--policy plugin_abi` run back to `strict_abi` for every kind the
        pack did not mention.
        """
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_policy import ChangeKind
        from abicheck.pack_application import PackApplication, policy_file_with_packs

        application = PackApplication(
            policy_overrides={ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE}
        )
        folded = policy_file_with_packs(None, application, base_policy="plugin_abi")
        assert folded is not None
        assert folded.base_policy == "plugin_abi"
        assert folded.overrides == {ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE}


class TestOnlyAppliedFieldsAreAccepted:
    """A pack assignment that changes nothing is rejected, not recorded."""

    @pytest.mark.parametrize("field", sorted(_UNAPPLIED_FIELD_VALUES))
    def test_an_unapplied_field_is_a_usage_error(
        self, pair: tuple[Path, Path], tmp_path: Path, field: str
    ) -> None:
        """Every registry entry, not just the first one.

        The parametrize list is derived from `UNAPPLIED_PACK_FIELDS` itself
        (via `_UNAPPLIED_FIELD_VALUES`, which pairs each with a value its own
        route accepts), so a field added to the registry later arrives with a
        rejection test instead of an untested branch. Each manifest must be
        *routable* — otherwise the loader would reject it for the wrong
        reason and the test would pass without exercising this rule at all.
        """
        pack = _pack(
            tmp_path,
            "future.yml",
            f"id: future\nversion: 1\nkind: {_field_kind(field).value}\n"
            f"assignments:\n  {field}: {_UNAPPLIED_FIELD_VALUES[field]}\n",
        )
        result = _compare(CliRunner(), pair, "--pack", str(pack))
        assert result.exit_code == 64, result.output
        assert field in result.output

    def test_the_registry_partitions_the_routable_vocabulary(self) -> None:
        """`UNAPPLIED_PACK_FIELDS` is the *complement* of what is applied, so
        a newly-routable field is applied or listed -- never neither, which is
        how a decorative assignment would slip back in."""
        from abicheck.compatibility_evaluation_wiring import PACK_FIELD_ROUTES_BY_KIND
        from abicheck.pack_application import (
            UNAPPLIED_PACK_FIELDS,
            applied_pack_fields,
        )

        routable: set[str] = set()
        for kind, routes in PACK_FIELD_ROUTES_BY_KIND.items():
            routable |= set(routes)
            assert applied_pack_fields(kind) <= set(routes)
        assert set(UNAPPLIED_PACK_FIELDS) <= routable
        # ...and every registry entry is actually exercised above, so a new
        # one cannot join the registry without a rejection test.
        assert set(_UNAPPLIED_FIELD_VALUES) == set(UNAPPLIED_PACK_FIELDS)

    def test_a_gate_pack_is_rejected_by_scan_which_has_no_gate(
        self, pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        old, new = pair
        gate = _pack(
            tmp_path,
            "gate.yml",
            "id: g\nversion: 1\nkind: gate\n"
            "assignments:\n  gate.exit_code_scheme: severity\n",
        )
        result = CliRunner().invoke(
            main, ["scan", str(new), "--against", str(old), "--pack", str(gate)]
        )
        assert result.exit_code == 64, result.output
        assert "gate" in result.output

    def test_scan_rejects_an_unapplied_field_from_the_resolution(
        self, pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """`scan` validates the resolved configuration, like `compare`.

        It previously re-read the manifests after `resolve_scan_config` had
        already loaded them, so the revision it validated need not be the one
        recorded and applied (Codex review, raised for `compare` first and
        then for this path). Both of its questions are answerable from the
        resolution: an unapplied field from provenance, a selected gate pack
        from `gate.packs`.
        """
        old, new = pair
        pack = _pack(
            tmp_path,
            "future.yml",
            "id: future\nversion: 1\nkind: contract\n"
            "assignments:\n  contract.unresolved: warn\n",
        )
        result = CliRunner().invoke(
            main, ["scan", str(new), "--against", str(old), "--pack", str(pack)]
        )
        assert result.exit_code == 64, result.output
        assert "contract.unresolved" in result.output

    def test_pack_without_a_baseline_is_a_usage_error_on_scan(
        self, pair: tuple[Path, Path], ignore_removals: Path
    ) -> None:
        _old, new = pair
        result = CliRunner().invoke(
            main, ["scan", str(new), "--pack", str(ignore_removals)]
        )
        assert result.exit_code == 64, result.output
        assert "--pack" in result.output

    @pytest.mark.parametrize(
        "body",
        [
            "kind: nonsense\nassignments:\n  x: y\n",
            "kind: contract\nassignments:\n  contract.unresolved: warn\n",
        ],
    )
    def test_a_dry_run_rejects_what_the_real_run_rejects(
        self, pair: tuple[Path, Path], tmp_path: Path, body: str
    ) -> None:
        """`compare` validates flag combinations ahead of its `--dry-run`
        emit precisely so a dry run cannot report "ok" for an invocation the
        identical real run rejects. Manifest validity is answerable that
        early, so it is answered there (Codex/CodeRabbit review)."""
        pack = _pack(tmp_path, "bad.yml", f"id: bad\nversion: 1\n{body}")
        for extra in ([], ["--dry-run"]):
            result = _compare(CliRunner(), pair, "--pack", str(pack), *extra)
            assert result.exit_code == 64, (extra, result.output)

    def test_a_manifest_swapped_after_the_early_check_is_still_rejected(
        self, pair: tuple[Path, Path], ignore_removals: Path, monkeypatch
    ) -> None:
        """The applied-field rule must hold for the version actually applied.

        `validate_pack_manifests` runs before the dry-run emit, so it reads
        whatever is on disk *then* — with a full snapshot resolution in
        between, a manifest edited afterwards would reach the resolver
        unvalidated and its unapplied field would be silently ignored rather
        than rejected (Codex review). Simulated by rewriting the manifest at
        the moment the early check returns.
        """
        import abicheck.cli_compare_receipt as receipt

        real = receipt.validate_pack_manifests

        def _validate_then_swap(pack_paths):
            real(pack_paths)
            ignore_removals.write_text(
                "id: relax_removals\nversion: 2\nkind: contract\n"
                "assignments:\n  contract.unresolved: warn\n",
                encoding="utf-8",
            )

        monkeypatch.setattr(receipt, "validate_pack_manifests", _validate_then_swap)
        result = _compare(CliRunner(), pair, "--pack", str(ignore_removals))
        assert result.exit_code == 64, result.output
        assert "contract.unresolved" in result.output

    def test_the_dry_run_reports_the_resolved_scheme_not_the_raw_flag(
        self, pair: tuple[Path, Path], tmp_path: Path, monkeypatch
    ) -> None:
        """A dry run previews CI behaviour, so the scheme it prints must be
        the one the run would use.

        The renderer was handed the raw `--exit-code-scheme`, so it printed
        "legacy (0/2/4)" whenever the flag was absent — including when
        `.abicheck.yml` configured severity and the real run therefore used
        the severity scheme. That predates `--pack` (Codex review), which is
        why this case uses no pack at all.
        """
        old, new = pair
        (tmp_path / ".abicheck.yml").write_text(
            "severity:\n  abi_breaking: warning\n", encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(main, ["compare", str(old), str(new), "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "exit-code scheme: severity" in result.output

    def test_the_dry_run_says_a_pack_may_still_move_the_scheme(
        self, pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """A gate pack's scheme cannot be resolved this early — the
        configuration needs the `--policy-file` loaded much later, and a
        *partial* resolution would run D8 conflict detection against
        different pins than the real one, which can reject a pack pair the
        real run accepts. Saying so is honest; asserting a scheme computed
        under different precedence would not be."""
        gate = _pack(
            tmp_path,
            "scheme.yml",
            "id: scheme\nversion: 1\nkind: gate\n"
            "assignments:\n  gate.exit_code_scheme: severity\n",
        )
        result = _compare(CliRunner(), pair, "--dry-run", "--pack", str(gate))
        assert result.exit_code == 0, result.output
        assert "a selected --pack may adjust it" in result.output

    def test_a_dry_run_still_accepts_a_usable_pack(
        self, pair: tuple[Path, Path], ignore_removals: Path
    ) -> None:
        result = _compare(
            CliRunner(), pair, "--dry-run", "--pack", str(ignore_removals)
        )
        assert result.exit_code == 0, result.output

    def test_pack_is_rejected_on_a_release_comparison(
        self, tmp_path: Path, ignore_removals: Path
    ) -> None:
        """The directory/package fan-out dispatches before the effective
        configuration is resolved, so a pack there would be accepted and score
        nothing -- the same reason `--contract-evaluation` is rejected."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        result = CliRunner().invoke(
            main,
            ["compare", str(old_dir), str(new_dir), "--pack", str(ignore_removals)],
        )
        assert result.exit_code == 64, result.output
        assert "--pack" in result.output


class TestNoPackChangesNothing:
    def test_an_application_with_no_packs_is_inert(self) -> None:
        from abicheck.compatibility_evaluation_frontend import (
            ExplicitCompatibilityInputs,
            FrontEnd,
            resolve_compatibility_evaluation_config,
        )
        from abicheck.pack_application import pack_application

        config = resolve_compatibility_evaluation_config(
            front_end=FrontEnd.CLI, explicit=ExplicitCompatibilityInputs()
        )
        assert pack_application(config, policy_file=None).is_empty()

    def test_folding_an_inert_application_returns_the_same_objects(self) -> None:
        from abicheck.pack_application import (
            PackApplication,
            apply_to_compare_config,
            policy_file_with_packs,
        )
        from abicheck.policy_file import PolicyFile

        inert = PackApplication(policy_overrides={})
        original = PolicyFile(base_policy="sdk_vendor")
        assert policy_file_with_packs(original, inert, base_policy="x") is original
        sentinel = object()
        assert apply_to_compare_config(sentinel, inert) is sentinel


class TestReceiptAgreesWithWhatScored:
    """The CLI's established split: values from the run, provenance from the
    canonical resolver -- held to agreeing by a test rather than assumed."""

    def test_the_receipt_names_the_manifest_that_supplied_the_override(
        self, pair: tuple[Path, Path], ignore_removals: Path, tmp_path: Path
    ) -> None:
        report = tmp_path / "report.json"
        result = _compare(
            CliRunner(),
            pair,
            "--contract-evaluation",
            "--format",
            "json",
            "--pack",
            str(ignore_removals),
            "-o",
            str(report),
        )
        # The verdict moved *and* the receipt explains why -- neither alone is
        # the claim being made here.
        assert result.exit_code == 0, result.output
        ctx = json.loads(report.read_text(encoding="utf-8"))["contract_context"][
            "evaluation_context"
        ]
        assert ctx["resolved_config"]["policy"]["overrides"] == {
            "func_removed": "COMPATIBLE"
        }
        provenance = ctx["field_provenance"]["policy.overrides"]
        assert provenance["source_kind"] == "pack_manifest"
        assert provenance["reference"] == "relax_removals"
        assert [hop["option"] for hop in provenance["selected_by"]] == ["--pack"]

    def test_compare_and_scan_receipts_agree_on_the_packs_axis(
        self, pair: tuple[Path, Path], ignore_removals: Path, tmp_path: Path
    ) -> None:
        """§6.4's parity Gate lists `packs`. It was untestable end to end
        while nothing selected one."""
        old, new = pair
        runner = CliRunner()
        compare_out = tmp_path / "compare.json"
        scan_out = tmp_path / "scan.json"
        common = [
            "--contract-evaluation",
            "--pack",
            str(ignore_removals),
            "--format",
            "json",
        ]
        assert (
            runner.invoke(
                main, ["compare", str(old), str(new), *common, "-o", str(compare_out)]
            ).exit_code
            == 0
        )
        assert (
            runner.invoke(
                main,
                ["scan", str(new), "--against", str(old), *common, "-o", str(scan_out)],
            ).exit_code
            == 0
        )
        compare_ctx = json.loads(compare_out.read_text(encoding="utf-8"))[
            "contract_context"
        ]["evaluation_context"]
        scan_ctx = json.loads(scan_out.read_text(encoding="utf-8"))["diff"][
            "contract_context"
        ]["evaluation_context"]
        assert (
            scan_ctx["resolved_config"]["policy"]
            == compare_ctx["resolved_config"]["policy"]
        )
        assert (
            scan_ctx["field_provenance"]["policy.overrides"]
            == compare_ctx["field_provenance"]["policy.overrides"]
        )

    def test_the_applied_gate_equals_the_resolved_gate(
        self, pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """The gate is the one field the CLI takes from `resolve_compare_config`
        while reporting the canonical resolver's provenance. A gate pack moves
        the value on one side only unless the two really agree.
        """
        gate = _pack(
            tmp_path,
            "scheme.yml",
            "id: scheme\nversion: 1\nkind: gate\n"
            "assignments:\n  gate.exit_code_scheme: severity\n",
        )
        report = tmp_path / "report.json"
        result = _compare(
            CliRunner(),
            pair,
            "--contract-evaluation",
            "--format",
            "json",
            "--pack",
            str(gate),
            "-o",
            str(report),
        )
        assert result.exit_code == 4, result.output
        ctx = json.loads(report.read_text(encoding="utf-8"))["contract_context"][
            "evaluation_context"
        ]
        assert ctx["resolved_config"]["gate"]["exit_code_scheme"] == "severity"
        assert (
            ctx["field_provenance"]["gate.exit_code_scheme"]["source_kind"]
            == "pack_manifest"
        )
