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
from abicheck.model import AbiSnapshot, Function, Variable, Visibility
from abicheck.serialization import snapshot_to_json


def _fn(name: str, mangled: str) -> Function:
    return Function(
        name=name, mangled=mangled, return_type="int", visibility=Visibility.PUBLIC
    )


def _var(name: str, mangled: str) -> Variable:
    return Variable(
        name=name, mangled=mangled, type="int", visibility=Visibility.PUBLIC
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


@pytest.fixture
def two_kind_pair(tmp_path: Path) -> tuple[Path, Path]:
    """As `pair`, but the removal spans two `ChangeKind`s.

    One finding cannot show that per-kind overrides from two sources compose:
    with a single kind, dropping either source's contribution is invisible
    whenever the surviving one already covers it.
    """
    common = {"library": "libfoo.so.1", "from_headers": True}
    old = AbiSnapshot(
        version="1.0",
        functions=[_fn("api_a", "_Z5api_av"), _fn("api_b", "_Z5api_bv")],
        variables=[_var("api_v", "api_v")],
        **common,
    )
    new = AbiSnapshot(
        version="2.0", functions=[_fn("api_a", "_Z5api_av")], variables=[], **common
    )
    old_p = tmp_path / "old-two.json"
    new_p = tmp_path / "new-two.json"
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
            check_resolved_config_applies_packs,
            pack_application,
            policy_file_with_packs,
        )

        assert applied_pack_fields(PackKind.CONTRACT) == {
            "surface.internal_namespaces",
            # Applied since Phase 7, when its coverage exit became real.
            "contract.unresolved",
        }
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
        # The applicability check has to *accept* this: the inert-value rule
        # rejects `surface.internal_namespaces: []`, and a rule that rejected
        # the non-empty case too would make the field unassignable rather than
        # merely value-restricted.
        check_resolved_config_applies_packs(config)
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
            "--policy",
            str(policy_file),
        )
        assert result.exit_code == 4, result.output

    def test_a_pack_folds_into_a_policy_file_that_states_other_kinds(
        self, two_kind_pair: tuple[Path, Path], ignore_removals: Path, tmp_path: Path
    ) -> None:
        """Outranking is per *kind*, not per file: a `--policy` shadows
        only the kinds it actually states, and the pack still supplies the
        rest by merging into that same file rather than replacing it.

        The sibling above covers the collision (the file wins). This covers
        the merge, which is the half either a "policy file present, so drop
        the pack" shortcut or a "pack selected, so replace the file" one would
        silently break -- so the pair here drops a function *and* a variable,
        one override coming from each side. Exit 0 requires both to survive.
        """
        policy_file = tmp_path / "other-kinds.yml"
        policy_file.write_text("overrides:\n  var_removed: ignore\n", encoding="utf-8")
        result = _compare(
            CliRunner(),
            two_kind_pair,
            "--format",
            "json",
            "--pack",
            str(ignore_removals),
            "--policy",
            str(policy_file),
        )
        assert result.exit_code == 0, result.output

    def test_an_explicit_severity_flag_outranks_a_gate_pack(
        self, pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        # --severity-preset is the explicit severity flag that survived the
        # per-category removals; D8's rule is about an explicitly *stated*
        # value outranking a pack, not about which spelling states it.
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
            "--severity-preset",
            "strict",
        )
        assert result.exit_code == 4, result.output

    def test_a_gate_pack_severity_level_selects_the_algorithm_it_earns(
        self, pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """A gate-pack-supplied severity level is itself a severity setting
        in effect, so it moves the (purely automatic, since CLI cleanup
        phase two PR G2) algorithm to `"severity"` and is then scored under
        it -- a `warning`-level `abi_breaking` assignment demotes what would
        otherwise be a legacy-scheme exit 4 BREAKING to exit 0, since
        `warning` is not an error level.

        (Before PR G2, this test parametrized three cases: an explicit
        `--exit-code-scheme legacy`/`severity` pinning the algorithm
        regardless of the pack, and the no-flag case below. The manual
        selector no longer exists at all -- there is no longer a stated
        value for the pack to be forbidden from overriding, so only the
        "nothing stated it, the pack's own level decides" case survives.)
        """
        gate = _pack(
            tmp_path,
            "lenient.yml",
            "id: lenient\nversion: 1\nkind: gate\n"
            "assignments:\n  gate.severity.abi_breaking: warning\n",
        )
        result = _compare(CliRunner(), pair, "--format", "json", "--pack", str(gate))
        assert result.exit_code == 0, result.output

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
        """With no `--policy` there is nothing to fold into, so one is
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

    @pytest.mark.parametrize("extra", [[], ["--dry-run"]])
    def test_an_inert_empty_namespace_set_is_rejected(
        self, pair: tuple[Path, Path], tmp_path: Path, extra: list[str]
    ) -> None:
        """`surface.internal_namespaces: []` routes and resolves, but nothing
        acts on it: post-processing turns an empty list into "unset" and falls
        back to the default namespaces, so the pack would be recorded as
        active configuration having changed nothing (Codex review).

        The runtime collapse is pre-existing and shared with a `--policy`
        writing the same empty list, so honoring stated-empty is its own
        change; rejecting the inert value keeps this module's rule true.

        Whether a `--policy` shadows the field is read off the file
        itself via the resolver's own predicate, so a file that states
        something else entirely does not suppress the rejection -- the
        `base_policy`-only case below is the one a coarser "was any policy
        file given" proxy got wrong. Answerable before the `--dry-run` emit
        either way, and both must agree, hence the parametrization.
        """
        pack = _pack(
            tmp_path,
            "empty-ns.yml",
            "id: none\nversion: 1\nkind: contract\n"
            "assignments:\n  surface.internal_namespaces: []\n",
        )
        result = _compare(CliRunner(), pair, "--pack", str(pack), *extra)
        assert result.exit_code == 64, (extra, result.output)
        assert "surface.internal_namespaces" in result.output
        # ...and the rejection names which manifest is at fault.
        assert "empty-ns.yml" in result.output

    @pytest.mark.parametrize("extra", [[], ["--dry-run"]])
    def test_a_policy_file_stating_something_else_does_not_shadow(
        self, pair: tuple[Path, Path], tmp_path: Path, extra: list[str]
    ) -> None:
        """A `--policy` only shadows the field it actually states.

        Passing "a policy file exists" as the shadow signal treated a file
        setting only `base_policy` as pinning `surface.internal_namespaces`,
        so the dry run exited 0 while the real run exited 64 (Codex review,
        reproduced). The signal is now the resolver's own pin predicate.
        """
        policy = tmp_path / "base-only.yml"
        policy.write_text("base_policy: sdk_vendor\n", encoding="utf-8")
        pack = _pack(
            tmp_path,
            "empty-ns.yml",
            "id: none\nversion: 1\nkind: contract\n"
            "assignments:\n  surface.internal_namespaces: []\n",
        )
        result = _compare(
            CliRunner(),
            pair,
            "--pack",
            str(pack),
            "--policy",
            str(policy),
            *extra,
        )
        assert result.exit_code == 64, (extra, result.output)

    def test_a_shadowed_inert_value_is_not_rejected(
        self, pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """D8: an explicit `--policy` stating the field wins, so the
        pack's value never reaches runtime and there is nothing inert to
        reject.

        The first version of the inert check ran against the raw manifest,
        before precedence was resolved, and so failed this invocation with
        exit 64 (Codex review, reproduced). `_pack_supplied` is what makes the
        surviving check precedence-aware: the field's provenance names the
        policy file, not a pack.
        """
        policy = tmp_path / "ns.yml"
        policy.write_text("internal_namespaces:\n  - priv\n", encoding="utf-8")
        pack = _pack(
            tmp_path,
            "empty-ns.yml",
            "id: none\nversion: 1\nkind: contract\n"
            "assignments:\n  surface.internal_namespaces: []\n",
        )
        result = _compare(
            CliRunner(), pair, "--pack", str(pack), "--policy", str(policy)
        )
        assert result.exit_code == 4, result.output
        # ...and the dry run agrees rather than rejecting it early.
        dry = _compare(
            CliRunner(),
            pair,
            "--dry-run",
            "--pack",
            str(pack),
            "--policy",
            str(policy),
        )
        assert dry.exit_code == 0, dry.output

    def test_a_non_empty_namespace_set_still_applies(self, tmp_path: Path) -> None:
        """Only the inert *value* is rejected — the field itself still works."""
        from abicheck.pack_application import _inert_value_reason

        assert _inert_value_reason("surface.internal_namespaces", ("priv",)) is None
        assert _inert_value_reason("surface.internal_namespaces", ()) is not None

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

    def test_the_pack_help_names_every_field_it_applies(self) -> None:
        """`--pack`'s own help is the answer a user gets, so it has to be the
        answer the code gives.

        It said a `kind: contract` pack assigns `surface.internal_namespaces`
        and stopped there -- true until Phase 7 gave `contract.unresolved` an
        engine consumer, in the same commit range. A user-facing description
        that understates what this build applies is how a supported field gets
        talked out of existence, so the two are pinned together rather than
        kept in sync by hand (CodeRabbit review, on the adjacent claim).
        """
        from abicheck.cli import main
        from abicheck.compatibility_evaluation_packs import PackKind
        from abicheck.pack_application import (
            UNAPPLIED_PACK_FIELDS,
            applied_pack_fields,
        )

        option = next(
            p for p in main.commands["compare"].params if p.name == "pack_paths"
        )
        help_text = option.help or ""
        for kind in PackKind:
            for field_name in applied_pack_fields(kind):
                # The four `gate.severity.<category>` fields are one family and
                # the help names them as one, which is a description of the
                # vocabulary rather than an omission from it. Nothing else may
                # stand in for a field's own name.
                family = "gate.severity.<category>"
                spellings = {field_name}
                if field_name.startswith("gate.severity."):
                    spellings.add(family)
                assert any(s in help_text for s in spellings), (kind, field_name)
        # ...and the complement: advertising a field this build rejects would
        # be the same failure pointing the other way.
        for field_name in UNAPPLIED_PACK_FIELDS:
            assert field_name not in help_text, field_name

    def test_a_gate_pack_is_applied_to_scan(
        self, pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """CLI cleanup phase two, "PR B": a `kind: gate` pack now configures
        `scan --against` instead of being rejected outright -- `scan`'s exit
        code has honored `--severity-preset`/`--exit-code-scheme` (direct CLI
        flags and `.abicheck.yml`) since the fix that closed the "scan never
        consults severity" gap (AGENTS.md "Known gaps"); a gate pack is one
        more source for that same real gate, mirroring
        `test_a_gate_pack_severity_moves_the_run_onto_the_severity_scheme`
        (the single-pair `compare` version) and
        `test_gate_pack_is_applied_to_a_release_comparison` (the release
        fan-out version)."""
        old, new = pair
        gate = _pack(
            tmp_path,
            "lenient.yml",
            "id: lenient\nversion: 1\nkind: gate\n"
            "assignments:\n  gate.severity.abi_breaking: warning\n",
        )
        without_pack = CliRunner().invoke(
            main, ["scan", str(new), "--against", str(old)]
        )
        assert without_pack.exit_code == 4, without_pack.output

        with_pack = CliRunner().invoke(
            main,
            [
                "scan", str(new), "--against", str(old),
                "--format", "json", "--pack", str(gate),
            ],
        )
        assert with_pack.exit_code == 0, with_pack.output
        # The finding is still reported -- only the gate moved.
        summary = json.loads(with_pack.output)
        assert summary["verdict"] == "BREAKING"

    def test_a_gate_pack_is_reflected_in_scan_dry_run_preview(
        self, pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence: `scan --dry-run`'s previewed
        exit-code scheme/severity must describe the pack-folded gate that
        will actually run, not a stale snapshot computed before the pack was
        applied. Reproduces the exact repro from that review: a pack
        demoting `abi_breaking` to `warning` must be visible in the preview,
        not left showing the legacy/pre-pack scheme."""
        old, new = pair
        gate = _pack(
            tmp_path,
            "lenient.yml",
            "id: lenient\nversion: 1\nkind: gate\n"
            "assignments:\n  gate.severity.abi_breaking: warning\n",
        )
        result = CliRunner().invoke(
            main,
            [
                "scan", str(new), "--against", str(old),
                "--dry-run", "--pack", str(gate),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "exit-code scheme: severity" in result.output
        assert "abi_breaking=warning" in result.output
        # Codex review, fresh evidence: by the time this preview is
        # rendered, the pack has already been folded into resolved_cfg (the
        # values just asserted above ARE the pack-adjusted ones) -- claiming
        # "a selected --pack may adjust it" here would self-contradict the
        # very label it's attached to.
        assert "may adjust it" not in result.output

    def test_a_gate_pack_cannot_override_an_explicit_scan_severity_preset(
        self, pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """Codex review on #801: the precedence rule D8 states for every
        other front end -- an explicitly stated value always outranks a
        pack -- must hold for `scan --against` too. Reproduces the exact
        repro from that review: a removed export scanned with an explicit
        `--severity-preset strict` must still exit 4 even when a selected
        gate pack tries to demote `abi_breaking` to `warning`; without the
        fix the pack silently won and this exited 0."""
        old, new = pair
        gate = _pack(
            tmp_path,
            "lenient.yml",
            "id: lenient\nversion: 1\nkind: gate\n"
            "assignments:\n  gate.severity.abi_breaking: warning\n",
        )
        result = CliRunner().invoke(
            main,
            [
                "scan", str(new), "--against", str(old),
                "--severity-preset", "strict",
                "--format", "json", "--pack", str(gate),
            ],
        )
        assert result.exit_code == 4, result.output
        summary = json.loads(result.output)
        assert summary["verdict"] == "BREAKING"

    def test_a_gate_pack_cannot_override_a_project_config_severity_preset(
        self, pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """The project-config (`.abicheck.yml`) tier of the identical
        precedence bug -- found while fixing the explicit-CLI tier above,
        by the same mechanism (`cli_scan_receipt`'s ADR-049 receipt not
        knowing a project-config value was already stated, so a selected
        pack looked unopposed)."""
        old, new = pair
        gate = _pack(
            tmp_path,
            "lenient.yml",
            "id: lenient\nversion: 1\nkind: gate\n"
            "assignments:\n  gate.severity.abi_breaking: warning\n",
        )
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text("severity:\n  preset: strict\n", encoding="utf-8")
        result = CliRunner().invoke(
            main,
            [
                "scan", str(new), "--against", str(old),
                "--config", str(cfg),
                "--format", "json", "--pack", str(gate),
            ],
        )
        assert result.exit_code == 4, result.output
        summary = json.loads(result.output)
        assert summary["verdict"] == "BREAKING"

    def test_a_gate_pack_asserting_exit_code_scheme_is_rejected_on_scan_too(
        self, pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """CLI cleanup phase two PR G2: `gate.exit_code_scheme` is not a
        pack-assignable field at all any more, on any front end -- a gate
        pack asserting it is rejected at load time (`PackManifestError`,
        surfaced by `scan` as the same `click.UsageError`/exit 64 an
        unroutable pack field always gets, mirroring `compare`'s identical
        rejection). Before PR G2, this test proved a project's explicit
        `exit_code_scheme: auto` outranked a gate pack's concrete scheme --
        that whole precedence question no longer applies, since neither the
        project config key nor the pack field exist to compete over."""
        old, new = pair
        gate = _pack(
            tmp_path,
            "legacy.yml",
            "id: legacy_scheme\nversion: 1\nkind: gate\n"
            "assignments:\n  gate.exit_code_scheme: legacy\n",
        )
        result = CliRunner().invoke(
            main,
            [
                "scan", str(new), "--against", str(old),
                "--format", "json", "--pack", str(gate),
            ],
        )
        assert result.exit_code == 64, result.output
        assert "may not assign" in result.output

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
            "assignments:\n  contract.overlays: [post_manifest]\n",
        )
        result = CliRunner().invoke(
            main, ["scan", str(new), "--against", str(old), "--pack", str(pack)]
        )
        assert result.exit_code == 64, result.output
        assert "contract.overlays" in result.output

    def test_scan_rejects_an_inert_value_from_the_resolution(
        self, pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """The inert-value rule reaches `scan` through the resolution alone.

        `compare` answers this twice -- once against the files, early enough
        that `--dry-run` agrees, and once authoritatively against the resolved
        provenance. `scan` has no dry run and so asks only the second, which
        is the path that reads the *resolved* value rather than the manifest's
        (`_resolved_field`). Same verdict, reached the other way.
        """
        old, new = pair
        pack = _pack(
            tmp_path,
            "scan-empty-ns.yml",
            "id: none\nversion: 1\nkind: contract\n"
            "assignments:\n  surface.internal_namespaces: []\n",
        )
        result = CliRunner().invoke(
            main, ["scan", str(new), "--against", str(old), "--pack", str(pack)]
        )
        assert result.exit_code == 64, result.output
        assert "surface.internal_namespaces" in result.output
        # ...and it names the manifest, which only the provenance can supply.
        assert "scan-empty-ns.yml" in result.output

    def test_an_unreadable_policy_file_does_not_decide_the_shadow_question(
        self, pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """The early shadow probe reads the `--policy` itself, so it has
        to answer "unreadable" somehow. It answers "shadows nothing", which is
        the conservative half: the pack stays subject to the inert-value rule
        rather than being waved through by a file that states nothing.

        The probe swallows the failure instead of reporting it, since the real
        run loads the same file a moment later and reports it with its own
        `--policy` framing -- this path exists only to keep `--dry-run`
        honest, not to validate policy files. It swallows exactly the three
        exceptions `PolicyFile.load` documents, which is also the set
        `cli_params` catches, so an undocumented failure surfaces identically
        whether or not `--pack` was given.
        """
        policy = tmp_path / "broken.yml"
        policy.write_text("overrides: not_a_mapping\n", encoding="utf-8")
        pack = _pack(
            tmp_path,
            "empty-ns.yml",
            "id: none\nversion: 1\nkind: contract\n"
            "assignments:\n  surface.internal_namespaces: []\n",
        )
        result = _compare(
            CliRunner(), pair, "--pack", str(pack), "--policy", str(policy)
        )
        assert result.exit_code == 64, result.output
        assert "surface.internal_namespaces" in result.output

    def test_both_paths_explain_a_rejection_identically(
        self, pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """One condition, one explanation.

        `compare` reaches an unapplied field through the file-based check and
        `scan --against` through the resolved-provenance one. Only the source
        token differs -- a manifest path there, the provenance-named pack
        here -- so both build the message from one helper. Written out twice,
        an edit to one wording would leave the two paths explaining the same
        condition differently (CodeRabbit review).
        """
        old, new = pair
        pack = _pack(
            tmp_path,
            "future.yml",
            "id: future\nversion: 1\nkind: contract\n"
            "assignments:\n  contract.overlays: [post_manifest]\n",
        )
        compare_out = _compare(CliRunner(), pair, "--pack", str(pack)).output
        scan_out = (
            CliRunner()
            .invoke(
                main, ["scan", str(new), "--against", str(old), "--pack", str(pack)]
            )
            .output
        )
        # The shared tail is everything after the source token, so comparing it
        # pins the wording without pinning which path names the pack how.
        tail = "'contract.overlays' is resolvable but not applied by this"
        assert tail in compare_out, compare_out
        assert tail in scan_out, scan_out

    def test_scan_rejects_a_pack_that_assigns_nothing_too(
        self, pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """The rule has to hold on both commands, and `scan` first missed it
        because it uses only the resolved check while emptiness was asked of
        the files (Codex review).

        It cannot move to the resolved check either: a pack an explicit
        `--policy` outranks *also* supplies no provenance, so at the
        resolution "assigns nothing" is indistinguishable from D8 precedence
        working correctly. It lives in `load_selected_packs` instead — every
        path that resolves or validates packs loads through there, so neither
        command can miss it again.
        """
        old_p, new_p = pair
        pack = _pack(
            tmp_path,
            "empty.yml",
            "id: empty\nversion: 1\nkind: policy\nassignments: {}\n",
        )
        result = CliRunner().invoke(
            main, ["scan", str(new_p), "--against", str(old_p), "--pack", str(pack)]
        )
        assert result.exit_code == 64, result.output
        assert "assigns nothing" in result.output

    @pytest.mark.parametrize("extra", [[], ["--dry-run"]])
    def test_a_pack_that_assigns_nothing_is_rejected(
        self, pair: tuple[Path, Path], tmp_path: Path, extra: list[str]
    ) -> None:
        """`assignments: {}` is the decorative pack in its purest form —
        selected, recorded in the receipt, configuring nothing (Codex
        review). Answerable from the file, since emptiness is not a
        precedence question and no layer can rescue it, so `--dry-run`
        rejects it too rather than approving a plan the real run refuses.
        """
        pack = _pack(
            tmp_path,
            "empty.yml",
            "id: empty\nversion: 1\nkind: policy\nassignments: {}\n",
        )
        result = _compare(CliRunner(), pair, "--pack", str(pack), *extra)
        assert result.exit_code == 64, (extra, result.output)
        assert "assigns nothing" in result.output

    def test_emptiness_is_judged_on_the_revision_that_configures_the_run(
        self, tmp_path: Path
    ) -> None:
        """The window a front-end-only check left open: a generated or
        concurrently edited pack that is non-empty when the command validates
        it and `assignments: {}` when the resolver reads it (Codex review).

        Validating early and resolving later is two reads; the rule has to
        hold on the second one, or the empty revision is what configures the
        run while the first read approved a different document.
        """
        from abicheck.cli_compare_receipt import validate_pack_manifests
        from abicheck.compatibility_evaluation_frontend import (
            ExplicitCompatibilityInputs,
            FrontEnd,
            resolve_compatibility_evaluation_config,
        )
        from abicheck.errors import PackManifestError

        pack = _pack(
            tmp_path,
            "shrinking.yml",
            "id: shrinking\nversion: 1\nkind: contract\n"
            "assignments:\n  surface.internal_namespaces: [priv]\n",
        )
        # The early, file-level validation every `compare` runs before its
        # --dry-run emit: this revision is fine.
        validate_pack_manifests([str(pack)])
        # ...and then it is rewritten before resolution.
        pack.write_text(
            "id: shrinking\nversion: 1\nkind: contract\nassignments: {}\n",
            encoding="utf-8",
        )
        with pytest.raises(PackManifestError, match="assigns nothing"):
            resolve_compatibility_evaluation_config(
                front_end=FrontEnd.CLI,
                explicit=ExplicitCompatibilityInputs(pack_paths=(str(pack),)),
            )

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
            "kind: contract\nassignments:\n  contract.overlays: [post_manifest]\n",
        ],
    )
    @pytest.mark.parametrize("extra", [[], ["--dry-run"]])
    def test_a_dry_run_rejects_what_the_real_run_rejects(
        self, pair: tuple[Path, Path], tmp_path: Path, body: str, extra: list[str]
    ) -> None:
        """`compare` validates flag combinations ahead of its `--dry-run`
        emit precisely so a dry run cannot report "ok" for an invocation the
        identical real run rejects. Manifest validity is answerable that
        early, so it is answered there (Codex/CodeRabbit review)."""
        pack = _pack(tmp_path, "bad.yml", f"id: bad\nversion: 1\n{body}")
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

        def _validate_then_swap(pack_paths, **kwargs):
            real(pack_paths, **kwargs)
            ignore_removals.write_text(
                "id: relax_removals\nversion: 2\nkind: contract\n"
                "assignments:\n  contract.overlays: [post_manifest]\n",
                encoding="utf-8",
            )

        monkeypatch.setattr(receipt, "validate_pack_manifests", _validate_then_swap)
        result = _compare(CliRunner(), pair, "--pack", str(ignore_removals))
        assert result.exit_code == 64, result.output
        assert "contract.overlays" in result.output

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
        configuration needs the `--policy` loaded much later, and a
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

    def test_policy_pack_is_applied_to_a_release_comparison(
        self, tmp_path: Path, ignore_removals: Path
    ) -> None:
        """CLI cleanup phase two, "PR B" slice 1: a `kind: policy` pack now
        configures the directory/package fan-out instead of being rejected
        outright -- the first-assertion rule this whole module states in its
        own docstring: an exit code that differs with and without the pack,
        not just "the flag was accepted"."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
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
        (old_dir / "libfoo.json").write_text(snapshot_to_json(old), encoding="utf-8")
        (new_dir / "libfoo.json").write_text(snapshot_to_json(new), encoding="utf-8")
        without_pack = CliRunner().invoke(main, ["compare", str(old_dir), str(new_dir)])
        assert without_pack.exit_code == 4, without_pack.output
        with_pack = CliRunner().invoke(
            main,
            ["compare", str(old_dir), str(new_dir), "--pack", str(ignore_removals)],
        )
        assert with_pack.exit_code == 0, with_pack.output

    def test_gate_pack_is_applied_to_a_release_comparison(self, tmp_path: Path) -> None:
        """CLI cleanup phase two, "PR B" slice 2: a `kind: gate` pack now
        configures the directory/package fan-out instead of being rejected
        outright -- the same first-assertion rule
        `test_policy_pack_is_applied_to_a_release_comparison` states for the
        policy half: an exit code that differs with and without the pack.

        Exercises the one field a gate pack can assign
        (`gate.severity.<category>` -- `gate.exit_code_scheme` was deleted
        in CLI cleanup phase two PR G2, no longer a pack-assignable field at
        all), on a release whose only change is a compatible addition --
        normally exit 0 regardless of scheme, since neither the legacy
        verdict mapping nor the severity default (`addition: info`) makes an
        addition-only release non-zero. The pack moves it to exit 1 by
        forcing the addition category to error, which is itself a severity
        setting in effect and so also (purely automatically) selects the
        severity algorithm.
        """
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old = AbiSnapshot(
            library="libfoo.so.1",
            version="1.0",
            functions=[_fn("api_a", "_Z5api_av")],
            from_headers=True,
        )
        new = AbiSnapshot(
            library="libfoo.so.1",
            version="2.0",
            functions=[_fn("api_a", "_Z5api_av"), _fn("api_b", "_Z5api_bv")],
            from_headers=True,
        )
        (old_dir / "libfoo.json").write_text(snapshot_to_json(old), encoding="utf-8")
        (new_dir / "libfoo.json").write_text(snapshot_to_json(new), encoding="utf-8")

        gate = _pack(
            tmp_path,
            "strict-additions.yml",
            "id: strict_additions\nversion: 1\nkind: gate\n"
            "assignments:\n"
            "  gate.severity.addition: error\n",
        )
        without_pack = CliRunner().invoke(
            main, ["compare", str(old_dir), str(new_dir), "--format", "json"]
        )
        assert without_pack.exit_code == 0, without_pack.output
        with_pack = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_dir),
                str(new_dir),
                "--format",
                "json",
                "--pack",
                str(gate),
            ],
        )
        assert with_pack.exit_code == 1, with_pack.output
        summary = json.loads(with_pack.output)
        assert summary["severity"]["config"]["addition"] == "error"
        assert summary["severity"]["exit_code"] == 1

    def test_gate_pack_severity_moves_a_release_onto_the_severity_scheme(
        self, tmp_path: Path
    ) -> None:
        """The release-side mirror of
        `test_a_gate_pack_severity_moves_the_run_onto_the_severity_scheme`
        (the single-pair version, above): a bare `gate.severity.<category>`
        assignment -- no explicit `gate.exit_code_scheme` -- still moves the
        release onto the severity scheme, via
        `cli_compare_release_helpers.apply_release_gate_pack`'s fallback to
        the canonical resolver's own already-decided `resolved_exit_code_
        scheme`, exactly as `pack_application.apply_to_compare_config` does
        for a single-pair `compare`."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
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
        (old_dir / "libfoo.json").write_text(snapshot_to_json(old), encoding="utf-8")
        (new_dir / "libfoo.json").write_text(snapshot_to_json(new), encoding="utf-8")

        gate = _pack(
            tmp_path,
            "lenient-abi.yml",
            "id: lenient_abi\nversion: 1\nkind: gate\n"
            "assignments:\n  gate.severity.abi_breaking: warning\n",
        )
        without_pack = CliRunner().invoke(main, ["compare", str(old_dir), str(new_dir)])
        assert without_pack.exit_code == 4, without_pack.output

        with_pack = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_dir),
                str(new_dir),
                "--format",
                "json",
                "--pack",
                str(gate),
            ],
        )
        assert with_pack.exit_code == 0, with_pack.output
        # The finding is still reported -- only the gate moved (same
        # assertion the single-pair sibling test makes).
        summary = json.loads(with_pack.output)
        assert summary["libraries"][0]["verdict"] == "BREAKING"

    @pytest.mark.parametrize("with_contract", [True, False])
    def test_contract_unresolved_pack_still_rejected_on_a_release_comparison(
        self, tmp_path: Path, with_contract: bool
    ) -> None:
        """`resolve_release_pack_application` rejects `contract.unresolved`
        unconditionally, with or without --contract -- not because the
        release fan-out lacks a per-library `PersistedContractContext` for
        it (it doesn't lack one; that was an earlier review round's wrong
        premise), but because whether lifting the rejection is safe remains
        unverified. See that function's own docstring and ADR-063 Track 4's
        7B ledger entry (`docs/_meta/one-semantic-pipeline-status.yaml`) for
        the full trace and review history."""
        pack = _pack(
            tmp_path,
            "unresolved.yml",
            "id: unresolved\nversion: 1\nkind: contract\n"
            "assignments:\n  contract.unresolved: warn\n",
        )
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        args = ["compare", str(old_dir), str(new_dir), "--pack", str(pack)]
        if with_contract:
            args += ["--contract", "public"]
        result = CliRunner().invoke(main, args)
        assert result.exit_code == 64, result.output
        assert "contract.unresolved" in result.output
        assert "cannot be applied to a directory/package" in result.output

    def test_release_pack_resolution_direct_call_with_no_packs_is_a_no_op(
        self,
    ) -> None:
        """Direct-call contract for the two release-pack resolvers: no
        `--pack` means no Click/file access at all, and a bare `None` back --
        the same "inert without a pack" property `TestNoPackChangesNothing`
        asserts for the single-pair resolver."""
        from abicheck.cli_compare_receipt import (
            resolve_release_pack_application,
            resolve_release_pack_application_from_ctx,
        )

        assert resolve_release_pack_application({"pack_paths": ()}) is None
        assert (
            resolve_release_pack_application_from_ctx(
                ctx=None,
                contract_mode=None,
                scope_public_headers=True,
                policy="strict_abi",
                policy_file_path=None,
                suppress=None,
                require_justification=False,
                severity_preset=None,
                pack_paths=(),
                contract_evaluation=False,
                project_cfg=None,
                project_path=None,
                project_sha256=None,
                policy_option=None,
                policy_path=None,
                policy_sha256=None,
            )
            is None
        )

    def test_broken_policy_document_is_a_clean_usage_error_on_release(
        self, tmp_path: Path, ignore_removals: Path
    ) -> None:
        """Codex review, found while adding direct test coverage: unlike the
        single-pair path (whose own `_load_suppression_and_policy` call
        already converts a malformed `--policy` document to a clean error
        *before* ever reaching the canonical resolver), the release fan-out
        had no earlier guard -- `resolve_release_pack_application`'s own
        `resolve_cli_config` call re-loads the document a second time (for
        D7 provenance) and, unguarded, let a genuine `PolicyError` propagate
        as a raw, uncaught exception instead of a clean `exit 64`. Fixed by
        widening `resolve_release_pack_application_from_ctx`'s own except
        clause. Reached only through `--pack` (the release fan-out never
        called `resolve_cli_config` at all before PR B slice 1), so this is
        a real regression relative to the pre-`--pack` release baseline, not
        a pre-existing bug: without `--pack`, the identical broken `--policy`
        document already degrades cleanly (verified separately)."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
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
        (old_dir / "libfoo.json").write_text(snapshot_to_json(old), encoding="utf-8")
        (new_dir / "libfoo.json").write_text(snapshot_to_json(new), encoding="utf-8")
        # Syntactically valid YAML, semantically invalid as a policy (an
        # unknown ChangeKind slug) -- PolicyFile.load raises PolicyError (a
        # ValueError subclass), which is now caught here. A genuine YAML
        # *syntax* error (yaml.YAMLError, not a ValueError) is a distinct
        # failure mode, covered separately below by
        # `test_yaml_syntax_error_is_a_clean_usage_error_on_release`.
        broken_policy_file = tmp_path / "broken-policy.yml"
        broken_policy_file.write_text(
            "base_policy: strict_abi\noverrides:\n  not_a_real_kind: ignore\n",
            encoding="utf-8",
        )
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_dir),
                str(new_dir),
                # ADR-037 D4: `--policy` takes both a built-in profile name
                # and a document path -- there is no separate `--policy-file`.
                "--policy",
                str(broken_policy_file),
                "--pack",
                str(ignore_removals),
            ],
        )
        assert result.exit_code == 64, result.output
        assert result.exception is None or isinstance(result.exception, SystemExit), (
            "must be a clean click.UsageError exit, not an uncaught exception"
        )
        assert "not_a_real_kind" in result.output

    def test_yaml_syntax_error_is_a_clean_usage_error_on_release(
        self, tmp_path: Path, ignore_removals: Path
    ) -> None:
        """Codex review, P2 follow-up on the finding above: a genuinely
        malformed YAML *document* (unbalanced flow-mapping brackets, not a
        semantically-invalid-but-well-formed one) raises PyYAML's own
        `yaml.YAMLError` from `resolve_release_pack_application`'s second,
        provenance-only `--policy` reload -- not a `ValueError`, so the
        earlier fix's `except (..., ValueError, OSError, ImportError)` still
        let this specific shape through as a raw traceback. Fixed by adding
        `yaml.YAMLError` to both `resolve_release_pack_application_from_ctx`'s
        except clause and its own earlier best-effort `PolicyFile.load`
        pre-read, which would otherwise raise the identical uncaught error
        one step earlier, before ever reaching the later, wider guard."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
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
        (old_dir / "libfoo.json").write_text(snapshot_to_json(old), encoding="utf-8")
        (new_dir / "libfoo.json").write_text(snapshot_to_json(new), encoding="utf-8")
        # Genuinely malformed YAML -- an unclosed flow mapping -- so
        # `yaml.safe_load` itself raises `yaml.YAMLError` (a `ParserError`),
        # never reaching `PolicyFile.load`'s own semantic validation.
        syntax_error_file = tmp_path / "syntax-error-policy.yml"
        syntax_error_file.write_text(
            "base_policy: strict_abi\noverrides: {not_closed\n",
            encoding="utf-8",
        )
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_dir),
                str(new_dir),
                "--policy",
                str(syntax_error_file),
                "--pack",
                str(ignore_removals),
            ],
        )
        assert result.exit_code == 64, result.output
        assert result.exception is None or isinstance(result.exception, SystemExit), (
            "must be a clean click.UsageError exit, not an uncaught exception"
        )


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

    def test_a_severity_pack_always_moves_the_purely_derived_scheme(
        self,
    ) -> None:
        """CLI cleanup phase two PR G2 simplified `apply_to_compare_config`
        down to one fold: a gate pack's severity levels merge in, and
        `severity_active` becomes true -- there is no separate scheme value
        to read, invent, or fall back to any more (no `resolved_exit_code_
        scheme`/`exit_code_scheme` field on `PackApplication` at all; the
        prior three-tier "read the resolver's own already-decided answer,
        never re-derive one" fallback this test used to pin only existed
        because a manual override could disagree with the derivation --
        with no override left, the derivation is the only answer, always
        computed fresh from `severity_active`)."""
        from abicheck.cli_helpers_compare import resolve_compare_config
        from abicheck.pack_application import PackApplication, apply_to_compare_config
        from abicheck.severity import SeverityLevel

        resolved = resolve_compare_config(
            None,
            cli_severity_preset=None,
            cli_scope_public=None,
        )
        assert resolved.exit_code_scheme == "legacy"
        application = PackApplication(
            policy_overrides={},
            severity_levels={"abi_breaking": SeverityLevel.WARNING},
        )
        folded = apply_to_compare_config(resolved, application)
        # The level is folded in, and the now-active severity setting moves
        # the purely-derived scheme to "severity" -- there is no longer a
        # "keep the pre-pack scheme" fallback path to reach at all.
        assert folded.severity.abi_breaking == SeverityLevel.WARNING
        assert folded.severity_active is True
        assert folded.exit_code_scheme == "severity"


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
            # See the parity test below: `all` keeps ADR-049 Phase 7's
            # contract-coverage axis quiet so this stays a test about packs.
            "--contract",
            "all",
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
            # Pin the rollback domain so this stays a test about `packs`.
            # ADR-049 Phase 7 made the contract-coverage axis real, and this
            # fixture's symbols carry no header provenance, so `public` would
            # contribute its orthogonal exit 1 and mask the pack's own effect.
            "--contract",
            "all",
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

        (Before CLI cleanup phase two PR G2, the pack asserted
        `gate.exit_code_scheme: severity` directly and this test also
        checked a `gate.exit_code_scheme` provenance entry existed with
        `source_kind == "pack_manifest"`. Neither exists any more: the field
        was deleted as a pack-assignable route entirely, and the purely-
        derived scheme carries no provenance entry of its own -- see
        `compatibility_evaluation_frontend.py`'s resolver docstring. A
        `gate.severity.<category>` assignment is the pack's only remaining
        way to move the scheme, indirectly, by putting a severity setting
        in effect -- which this test now asserts instead.)
        """
        gate = _pack(
            tmp_path,
            "scheme.yml",
            "id: scheme\nversion: 1\nkind: gate\n"
            "assignments:\n  gate.severity.abi_breaking: error\n",
        )
        report = tmp_path / "report.json"
        result = _compare(
            CliRunner(),
            pair,
            "--contract",
            "public",
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
            ctx["field_provenance"]["gate.severity.abi_breaking"]["source_kind"]
            == "pack_manifest"
        )
