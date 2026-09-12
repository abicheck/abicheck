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

"""ADR-049 Phase 5 — ``compare`` consumes one resolved configuration.

Phase 1 built the canonical resolver
(``compatibility_evaluation_frontend.resolve_compatibility_evaluation_config``)
and Phase 4 persisted an ``evaluation_context`` block, but the two were not
joined: the block carried what ``checker.compare`` could reconstruct from its
own arguments, with each front end patching the fields it happened to know
about afterwards. This covers the seam that replaced that -- the CLI resolving
one object through the canonical resolver and installing it -- plus the claim
the module docstring of :mod:`abicheck.cli_compare_receipt` makes: that the
gate the receipt reports is the gate the run was scored with.

``tests/test_cli_compare_contract_evaluation.py`` covers the flag itself and
the per-field provenance cases that predate this wiring; this file covers
what the wiring newly makes true.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


def _fn(name: str, mangled: str) -> Function:
    return Function(
        name=name, mangled=mangled, return_type="int", visibility=Visibility.PUBLIC
    )


def _write_pair(tmp_path: Path) -> tuple[Path, Path]:
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


def _context(tmp_path: Path, *args: str) -> dict:
    """Run ``compare --contract auto`` and return its evaluation context.

    ``auto`` evaluates while leaving the domain to the D7 chain below an
    explicit CLI value -- which is the point for a receipt test asking which
    layer chose a field.
    """
    old_p, new_p = _write_pair(tmp_path)
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(old_p),
            str(new_p),
            "--contract",
            "auto",
            "-o",
            "json=-",
            *args,
        ],
    )
    assert result.exit_code in (1, 2, 4), result.output
    return json.loads(result.output)["contract_context"]["evaluation_context"]


class TestCanonicalResolverIsWhatRuns:
    """The persisted config is the canonical resolver's, not a reconstruction."""

    def test_policy_file_receipt_names_the_file_and_its_digest(self, tmp_path):
        """``checker.compare`` receives an already-loaded ``PolicyFile`` and can
        say only "some caller asked for this". The resolver has the path and
        the bytes, so ``policy.base`` names both -- which is what a D6 replay
        needs to re-read the same document.
        """
        policy = tmp_path / "p.yaml"
        policy.write_text("base_policy: sdk_vendor\n", encoding="utf-8")
        ctx = _context(tmp_path, "--policy", str(policy))

        assert ctx["resolved_config"]["policy"]["base"]["id"] == "sdk_vendor"
        prov = ctx["field_provenance"]["policy.base"]
        assert prov["layer"] == "explicit_cli"
        assert prov["path"] == str(policy)
        assert prov["sha256"]
        assert prov["selected_by"][0]["option"] == "--policy"

    def test_suppression_receipt_names_the_file_it_was_read_from(self, tmp_path):
        """The core verb sees a ``SuppressionList``; only the front end knows
        which path it came from."""
        rules = tmp_path / "s.yaml"
        rules.write_text(
            "version: 1\nsuppressions:\n  - symbol: api_b\n    reason: intentional\n",
            encoding="utf-8",
        )
        ctx = _context(tmp_path, "--suppress", str(rules))

        prov = ctx["field_provenance"]["suppressions"]
        assert prov["layer"] == "explicit_cli"
        assert prov["path"] == str(rules)
        # The digest is the one the live load captured over the bytes it
        # parsed -- not a second read that could hash different content.
        assert prov["sha256"] == ctx["resolved_config"]["suppressions"]["sha256"]

    def test_project_config_is_recorded_as_the_project_layer(self, tmp_path):
        """A value only ``.abicheck.yml`` supplied must not read as a CLI flag
        (or as a built-in default). The core verb cannot tell the difference;
        the resolver is handed both layers separately."""
        (tmp_path / ".abicheck.yml").write_text(
            "scope:\n  public: false\n", encoding="utf-8"
        )
        ctx = _context(tmp_path, "--config", str(tmp_path / ".abicheck.yml"))

        assert ctx["resolved_config"]["contract"]["mode"] == "all"
        prov = ctx["field_provenance"]["contract.mode"]
        assert prov["layer"] == "project_config"
        assert prov["path"] == str(tmp_path / ".abicheck.yml")

    def test_untyped_policy_default_is_not_reported_as_a_choice(self, tmp_path):
        """``--policy``'s click default (``strict_abi``) is indistinguishable
        from a typed value in the kwargs, which is exactly what
        ``typed_parameter_names()`` exists to disambiguate. Left alone, the
        field is a built-in default, not an explicit CLI selection."""
        ctx = _context(tmp_path)
        assert ctx["resolved_config"]["policy"]["base"]["id"] == "strict_abi"
        assert ctx["field_provenance"]["policy.base"]["layer"] == "built_in_default"

    def test_required_symbol_contract_records_the_policy_it_really_used(self, tmp_path):
        """``--required-symbol`` switches an untouched ``--policy`` to
        ``plugin_abi`` (ADR-043), and that value is not typed, not a project
        setting, and not the built-in default -- so a resolution reading only
        typed flags reported ``strict_abi`` for a run that used ``plugin_abi``
        (Codex review, fresh evidence). The receipt names the flag that really
        selected it.
        """
        ctx = _context(tmp_path, "--required-symbol", "api_b")
        assert ctx["resolved_config"]["policy"]["base"]["id"] == "plugin_abi"
        prov = ctx["field_provenance"]["policy.base"]
        assert prov["selected_by"][0]["option"] == "--required-symbol"

    def test_a_symbols_file_contract_names_the_file_and_the_option(self, tmp_path):
        """A `--required-symbol @FILE` run's receipt names the file and its
        digest (ADR-068 D5 / plan Phase 7h: `--required-symbols FILE` was
        merged into this `@FILE` spelling)."""
        listed = tmp_path / "required.txt"
        listed.write_text("# contract\napi_b\n", encoding="utf-8")
        ctx = _context(tmp_path, "--required-symbol", f"@{listed}")

        assert ctx["resolved_config"]["policy"]["base"]["id"] == "plugin_abi"
        prov = ctx["field_provenance"]["policy.base"]
        assert prov["path"] == str(listed)
        hop = prov["selected_by"][0]
        assert hop["option"] == "--required-symbol"
        assert hop["path"] == str(listed)
        # The digest is over the file's raw bytes, from the same read that
        # parsed the symbols.
        import hashlib

        assert hop["sha256"] == hashlib.sha256(listed.read_bytes()).hexdigest()

    def test_a_symbols_file_that_contributed_nothing_is_not_the_selector(
        self, tmp_path
    ):
        """Passing a file is not the same as the file supplying the contract.

        With `--required-symbol api_b --required-symbol @empty.txt` the file
        parses to nothing, so naming it omits the option that actually made
        the contract non-empty (Codex review, fresh evidence — the sharper
        half of the file-attribution fix; generalized from the removed
        `--required-symbols` flag to the merged `@FILE` spelling).
        """
        empty = tmp_path / "required.txt"
        empty.write_text("# nothing but a comment\n", encoding="utf-8")
        ctx = _context(
            tmp_path, "--required-symbol", "api_b", "--required-symbol", f"@{empty}"
        )

        assert ctx["resolved_config"]["policy"]["base"]["id"] == "plugin_abi"
        prov = ctx["field_provenance"]["policy.base"]
        assert prov["selected_by"][0]["option"] == "--required-symbol"
        assert prov.get("path") is None

    def test_project_config_provenance_carries_its_digest(self, tmp_path):
        """Naming the `.abicheck.yml` is not enough: edited after the run, the
        receipt could no longer prove which content produced the value."""
        import hashlib

        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text("scope:\n  public: false\n", encoding="utf-8")
        ctx = _context(tmp_path, "--config", str(cfg))

        hop = ctx["field_provenance"]["contract.mode"]["selected_by"][0]
        assert hop["sha256"] == hashlib.sha256(cfg.read_bytes()).hexdigest()

    def test_typed_policy_still_wins_over_the_required_symbol_default(self, tmp_path):
        """The derivation is gated on the flag being untouched, so a typed
        ``--policy`` must be reported as the selector, not overwritten."""
        ctx = _context(tmp_path, "--required-symbol", "api_b", "--policy", "sdk_vendor")
        assert ctx["resolved_config"]["policy"]["base"]["id"] == "sdk_vendor"
        assert (
            ctx["field_provenance"]["policy.base"]["selected_by"][0]["option"]
            == "--policy"
        )

    def test_typed_policy_is_reported_as_a_choice(self, tmp_path):
        ctx = _context(tmp_path, "--policy", "sdk_vendor")
        assert ctx["resolved_config"]["policy"]["base"]["id"] == "sdk_vendor"
        # D7 puts a `--policy` candidate at the LEGACY_ALIAS layer: it is the
        # older spelling `--policy` supersedes.
        assert ctx["field_provenance"]["policy.base"]["layer"] == "legacy_alias"


class TestProjectConfigOverridesReachTheReceipt:
    """Findings-analysis-fixes review round 3, finding 1: a project-config
    override that actually changed a run's verdict must be visible in
    ``resolved_config.policy.overrides``/``field_provenance["policy.
    overrides"]`` -- not just applied to the scoring ``PolicyFile`` while
    the receipt stays silent about it (the exact scenario Codex named:
    ``--contract auto --format json`` scores a project override that makes
    ``func_removed`` compatible, but the receipt used to still show
    ``resolved_config.policy.overrides: {}``)."""

    @staticmethod
    def _invoke_and_get_context(tmp_path: Path, *args: str) -> tuple[int, dict]:
        """Like the module-level ``_context`` helper, but without its
        exit-code assumption -- a project-config override that genuinely
        makes ``func_removed`` compatible is exactly the case this class
        tests, and that exits 0, not (1, 2, 4)."""
        old_p, new_p = _write_pair(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--contract",
                "auto",
                "-o",
                "json=-",
                *args,
            ],
        )
        out = result.output
        i = out.find("{")
        payload = json.loads(out[i:] if i >= 0 else out)
        return result.exit_code, payload["contract_context"]["evaluation_context"]

    def test_a_project_override_that_changes_the_verdict_is_in_the_receipt(
        self, tmp_path, monkeypatch
    ):
        (tmp_path / ".abicheck.yml").write_text(
            "policy:\n  overrides:\n    func_removed: ignore\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        exit_code, ctx = self._invoke_and_get_context(tmp_path)

        # The override actually scored the run: no ABI/API break is left --
        # proving the project config genuinely took effect for this run, not
        # just that the receipt happens to mention it. exit 0 or 1: a clean
        # compatible run, or the same run with the orthogonal contract-
        # coverage axis (AGENTS.md) raising a clean 0 to 1 for incomplete
        # header evidence -- either way, never the exit 4 an un-overridden
        # func_removed break would produce.
        assert exit_code in (0, 1), exit_code
        assert ctx["resolved_config"]["policy"]["overrides"] == {
            "func_removed": "COMPATIBLE"
        }
        prov = ctx["field_provenance"]["policy.overrides"]
        assert prov["layer"] == "project_config"
        assert prov["selected_by"][0]["option"] == "policy.overrides"

    def test_an_explicit_policy_file_still_wins_in_the_receipt_too(
        self, tmp_path, monkeypatch
    ):
        (tmp_path / ".abicheck.yml").write_text(
            "policy:\n  overrides:\n    func_removed: ignore\n",
            encoding="utf-8",
        )
        policy_path = tmp_path / "policy.yml"
        policy_path.write_text(
            "base_policy: strict_abi\noverrides:\n  func_removed: risk\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        exit_code, ctx = self._invoke_and_get_context(
            tmp_path, "--policy", str(policy_path)
        )

        assert exit_code in (0, 1), exit_code
        assert ctx["resolved_config"]["policy"]["overrides"] == {
            "func_removed": "COMPATIBLE_WITH_RISK"
        }
        options = {
            e["option"]
            for e in ctx["field_provenance"]["policy.overrides"]["selected_by"]
        }
        assert options == {"--policy"}


class TestGateParityWithTheLiveRun:
    """The receipt's gate must be the gate the run was actually scored with.

    ``cli_compare_receipt`` writes the *values* from
    ``resolve_compare_config`` (what computed the verdict and exit code) and
    the *provenance* from the canonical resolver. That split is only sound
    while the two agree on the values, which is a checkable claim rather than
    an assumption -- this is the check.
    """

    @pytest.mark.parametrize(
        "params,typed,config_severity",
        [
            ({}, set(), {}),
            ({"severity_preset": "default"}, set(), {}),
            # The per-category levels have no CLI flag any more, so the tier
            # that can still state them is the project config -- and the
            # parity claim has to hold through it just the same.
            ({}, set(), {"severity_abi_breaking": "warning"}),
            ({"severity_preset": "strict"}, set(), {}),
            (
                {"severity_preset": "info-only"},
                set(),
                {"severity_addition": "error"},
            ),
            ({"policy": "sdk_vendor"}, {"policy"}, {}),
            ({"scope_public_headers": False}, {"scope_public_headers"}, {}),
        ],
    )
    def test_resolver_gate_matches_resolve_compare_config(
        self, params, typed, config_severity
    ):
        from abicheck.buildsource.inline import BuildConfig
        from abicheck.cli_compare_receipt import resolve_cli_config
        from abicheck.cli_helpers_compare import resolve_compare_config

        full = {
            "contract_mode": None,
            "scope_public_headers": True,
            "policy": "strict_abi",
            "policy_file_path": None,
            "suppress": None,
            "require_justification": False,
            "severity_preset": None,
            "pack_paths": (),
            **params,
        }
        project_cfg = BuildConfig(**config_severity) if config_severity else None
        live = resolve_compare_config(
            project_cfg,
            cli_severity_preset=full["severity_preset"],
            cli_scope_public=(
                full["scope_public_headers"]
                if "scope_public_headers" in typed
                else None
            ),
        )
        resolved = resolve_cli_config(
            full, typed=typed, project_cfg=project_cfg, project_path=None
        )

        assert resolved.gate.exit_code_scheme == live.exit_code_scheme
        for category in (
            "abi_breaking",
            "potential_breaking",
            "quality_issues",
            "addition",
        ):
            assert getattr(resolved.gate.severity, category) == getattr(
                live.severity, category
            ), category


class TestObservedOverlaysSurvive:
    def test_forced_public_scope_still_comes_from_the_evidence_ledger(self, tmp_path):
        """``scope.public_symbols`` is both a stated input and an applied overlay.

        ``overlay_selection`` recovers what actually applied from the run's own
        ledger, including sources (``--post-manifest``) the resolver has no
        input model for at all, so installing the resolver's object must not
        blank either field.

        Stated through the project config now: the ``--public-symbol``/
        ``--public-symbols-list`` pair were hidden CLI duplicates of this key
        and were removed, so ``project_config`` is the tier that states it.
        """
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text("scope:\n  public_symbols: [api_b]\n", encoding="utf-8")
        ctx = _context(tmp_path, "--config", str(cfg))
        surface = ctx["resolved_config"]["surface"]
        assert ctx["resolved_config"]["contract"]["overlays"]
        assert surface["explicit_scope"] is not None
        assert surface["explicit_scope"]["sha256"]

    def test_the_config_that_forced_the_scope_is_still_named(self, tmp_path):
        """The observed value must not cost the receipt its identification.

        The ledger's own entry for `surface.explicit_scope` is a contentless
        `api_request` hop; taking it wholesale dropped the layer, option,
        path, and digest of the source that selected the scope, leaving a
        replay unable to find it (Codex review, fresh evidence).
        """
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text("scope:\n  public_symbols: [api_b]\n", encoding="utf-8")
        ctx = _context(tmp_path, "--config", str(cfg))

        prov = ctx["field_provenance"]["surface.explicit_scope"]
        options = [hop.get("option") for hop in prov["selected_by"]]
        # ...and the observed overlay hop is still recorded.
        assert "overlays" in options
        # The value stays the ledger's: it is what actually applied.
        assert ctx["resolved_config"]["surface"]["explicit_scope"]["items"] == ["api_b"]

    def test_no_overlay_leaves_the_resolved_scope_alone(self, tmp_path):
        ctx = _context(tmp_path)
        assert ctx["resolved_config"]["contract"]["overlays"] == []
        assert ctx["resolved_config"]["surface"]["explicit_scope"] is None


class TestOverlayProvenanceEdges:
    """`with_resolved_config`'s defensive branches, at the unit level.

    An overlay recorded with no provenance entry cannot arise from
    ``build_evaluation_context`` (it writes the entry under exactly the
    condition that populates the value), but this function is a public seam
    any front end may call with a context it assembled itself — so
    "overlays present, entry missing" must drop the stale entry rather than
    leave the resolver's, which would attribute an observed overlay to a
    flag.
    """

    def _context_with_overlays(self, *, scope_entry, overlay_entry):
        from dataclasses import replace

        from abicheck.compatibility_evaluation_config import (
            DigestedItems,
            SelectedByEntry,
            ValueProvenance,
        )
        from abicheck.contract_context import build_evaluation_context
        from abicheck.contract_evidence import (
            ContractEvidenceBlock,
            DecisionReceiptBlock,
            PersistedContractContext,
        )
        from abicheck.contract_relevance_types import ContractMode, SelectorLayer

        block = build_evaluation_context(mode=ContractMode.PUBLIC)
        config = block.resolved_config
        provenance = dict(config.provenance)
        api = ValueProvenance(
            layer=SelectorLayer.API_REQUEST,
            selected_by=(
                SelectedByEntry(layer=SelectorLayer.API_REQUEST, option="overlays"),
            ),
        )
        if overlay_entry:
            provenance["contract.overlays"] = api
        if scope_entry:
            provenance["surface.explicit_scope"] = api
        config = replace(
            config,
            contract=replace(config.contract, overlays=("forced_public_symbols",)),
            surface=replace(
                config.surface,
                explicit_scope=DigestedItems(sha256="deadbeef", items=("api_b",)),
            ),
            provenance=provenance,
        )
        return PersistedContractContext(
            contract_evidence=ContractEvidenceBlock(),
            evaluation_context=replace(block, resolved_config=config),
            decision_receipt=DecisionReceiptBlock(),
        )

    def _resolved(self):
        from abicheck.cli_compare_receipt import (
            COMPARE_CONFIG_PARAMS,
            resolve_cli_config,
        )

        # Every declared key, since `resolve_cli_config` rejects a partial
        # mapping outright -- an absent one would resolve as "not stated".
        params = dict.fromkeys(COMPARE_CONFIG_PARAMS)
        params["public_symbols"] = ("api_b",)
        return resolve_cli_config(params, typed=(), project_cfg=None, project_path=None)

    def test_missing_observed_entries_are_dropped_not_inherited(self):
        from abicheck.contract_context import with_resolved_config

        ctx = self._context_with_overlays(scope_entry=False, overlay_entry=False)
        out = with_resolved_config(ctx, self._resolved())
        provenance = out.evaluation_context.resolved_config.provenance
        assert "contract.overlays" not in provenance
        # The stated scope entry survives on its own — there is no observed
        # hop to append, which is the one-sided case the merge allows.
        assert provenance["surface.explicit_scope"].selected_by

    def test_a_stated_overlay_selector_survives_alongside_the_observed_one(self):
        """A run can state `contract.overlays` — via `--post-manifest`/
        `--public-symbol`, or an API request as here — and those selectors are
        a different set from the providers the ledger observed.

        Deliberately not "a `kind: contract` pack can assign it", which this
        docstring used to say: a pack assigning `contract.overlays` is now a
        usage error (`pack_application.UNAPPLIED_PACK_FIELDS` — the field
        routes and resolves, but the overlay kinds it names have no concrete
        input to point at). The merge under test is unaffected either way,
        since it is about a stated value meeting an observed one whatever
        stated it (CodeRabbit review).

        Replacing one with the other dropped a real selection and its receipt
        (CodeRabbit review), so the value is their union and the entries
        merge — the same rule `surface.explicit_scope` already follows.
        """
        from dataclasses import replace

        from abicheck.contract_context import with_resolved_config

        ctx = self._context_with_overlays(scope_entry=True, overlay_entry=True)
        resolved = self._resolved()
        stated = replace(
            resolved,
            contract=replace(resolved.contract, overlays=("ffi",)),
        )
        out = with_resolved_config(ctx, stated)
        config = out.evaluation_context.resolved_config
        assert config.contract.overlays == ("ffi", "forced_public_symbols")
        options = [
            hop.option for hop in config.provenance["contract.overlays"].selected_by
        ]
        assert "overlays" in options

    def test_observed_hop_is_appended_once(self):
        """A merge that re-appended an already-present hop would report one
        selection as two."""
        from abicheck.contract_context import with_resolved_config

        ctx = self._context_with_overlays(scope_entry=True, overlay_entry=True)
        resolved = self._resolved()
        once = with_resolved_config(ctx, resolved)
        twice = with_resolved_config(once, resolved)
        key = "surface.explicit_scope"
        assert (
            once.evaluation_context.resolved_config.provenance[key].selected_by
            == twice.evaluation_context.resolved_config.provenance[key].selected_by
        )

    def test_an_entry_already_carrying_the_observed_hop_is_returned_unchanged(self):
        """The dedup itself, at the helper level.

        The end-to-end idempotence check above never reaches this branch: on a
        second pass the *stated* entry is still the resolver's one-hop one, so
        there is always an extra hop to append. Nothing exercised the case the
        docstring actually promises — an entry that already carries every
        observed hop is returned as-is, not rebuilt.
        """
        from abicheck.compatibility_evaluation_config import (
            SelectedByEntry,
            ValueProvenance,
        )
        from abicheck.contract_context import _merged_overlay_provenance
        from abicheck.contract_relevance_types import SelectorLayer

        hop = SelectedByEntry(layer=SelectorLayer.API_REQUEST, option="overlays")
        observed = ValueProvenance(layer=SelectorLayer.API_REQUEST, selected_by=(hop,))
        stated = ValueProvenance(
            layer=SelectorLayer.EXPLICIT_CLI,
            selected_by=(
                SelectedByEntry(
                    layer=SelectorLayer.EXPLICIT_CLI, option="--public-symbol"
                ),
                hop,
            ),
        )

        assert _merged_overlay_provenance(stated=stated, observed=observed) is stated


class TestWiringContract:
    def test_typed_parameter_names_mirrors_the_resolver_constant(self):
        """A second copy of "which options have an ambiguous default" is a
        second thing to keep in sync; this asserts there is only one."""
        from abicheck.cli_compare_receipt import typed_parameter_names
        from abicheck.compatibility_evaluation_frontend import (
            DEFAULTED_COMPARE_PARAMETERS,
        )

        assert set(typed_parameter_names()) == set(DEFAULTED_COMPARE_PARAMETERS)

    def test_compare_forwards_every_declared_config_param(self, tmp_path, monkeypatch):
        """A key the caller forgets resolves silently to "not stated", which
        reads exactly like a user who did not pass the flag."""
        import abicheck.cli_compare_receipt as receipt
        from abicheck.cli_compare_receipt import COMPARE_CONFIG_PARAMS

        seen: dict = {}

        def _spy(params: dict, **kwargs: Any) -> tuple[Any, Any, Any]:
            seen.update(params)
            return None, kwargs.get("policy_file"), kwargs["resolved_cfg"]

        monkeypatch.setattr(receipt, "resolve_and_apply", _spy)
        old_p, new_p = _write_pair(tmp_path)
        CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "-o", "json=-"]
        )
        assert set(seen) == set(COMPARE_CONFIG_PARAMS)

    def test_a_resolution_usage_error_becomes_exit_64(self, tmp_path, monkeypatch):
        """The resolver documents mapping its errors to an exit code as the
        front end's job; a D7 same-tier conflict or a malformed pack manifest
        is a usage error like any other bad flag combination."""
        import abicheck.cli_compare_receipt as receipt
        from abicheck.compatibility_evaluation_resolver import FieldResolutionError

        def _boom(*args, **kwargs):
            raise FieldResolutionError("contract.mode: two explicit values")

        monkeypatch.setattr(receipt, "resolve_and_apply", _boom)
        old_p, new_p = _write_pair(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--contract",
                "public",
                "-o",
                "json=-",
            ],
        )
        assert result.exit_code == 64, result.output
        assert "two explicit values" in result.output

    def test_a_dropped_config_param_fails_loudly(self):
        """The tuple's whole point: a renamed or forgotten key would resolve
        as "not stated", which reads exactly like a flag the user never
        passed (CodeRabbit review — the contract was documented, not
        enforced)."""
        from abicheck.cli_compare_receipt import (
            COMPARE_CONFIG_PARAMS,
            resolve_cli_config,
        )

        params = dict.fromkeys(COMPARE_CONFIG_PARAMS)
        del params["policy"]
        with pytest.raises(KeyError, match="policy"):
            resolve_cli_config(params, typed=(), project_cfg=None, project_path=None)

    def test_recording_is_a_noop_without_a_context(self):
        """Every run that did not ask for ``--contract`` has no
        context to record onto, and must not pay for a resolution either."""
        from abicheck.cli_compare_receipt import record_resolved_config

        class _Result:
            contract_context = None

        result = _Result()
        record_resolved_config(result, object(), None)
        assert result.contract_context is None


class TestRequireCompleteAnalysisReceiptConsistency:
    """P2 (Codex review, fresh evidence on the require-complete-analysis
    retirement PR): for a ``--contract`` compare with
    ``assurance.require_complete: true``, ``record_resolved_config`` used to
    call ``with_resolved_gate`` without threading the resolved
    ``require_complete_analysis`` value through, so it fell back to the
    resolver's own built-in-default ``GateConfig.require_complete_
    analysis=False``. The persisted receipt then disagreed with itself:
    the top-level ``effective_config_fields["gate.require_complete_
    analysis"]`` (sourced from ``resolved_cfg`, the value that actually
    gated the run) read ``"True"`` while ``contract_context.
    evaluation_context.resolved_config.gate.require_complete_analysis``
    read ``False``. Both must now agree.
    """

    def test_both_representations_agree_when_true(self, tmp_path: Path) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            "assurance:\n  require_complete: true\n", encoding="utf-8"
        )
        old_p, new_p = _write_pair(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--contract",
                "auto",
                "--config",
                str(tmp_path / ".abicheck.yml"),
                "-o",
                "json=-",
            ],
        )
        assert result.exit_code in (1, 2, 4), result.output
        payload = json.loads(result.output)

        top_level = payload["effective_config_fields"]["gate.require_complete_analysis"]
        contract_context_value = payload["contract_context"]["evaluation_context"][
            "resolved_config"
        ]["gate"]["require_complete_analysis"]

        assert top_level == "True", payload["effective_config_fields"]
        assert contract_context_value is True, payload["contract_context"]
        # The actual agreement this test exists to pin: the receipt's two
        # ways of stating the same field must not diverge.
        assert (top_level == "True") == (contract_context_value is True)

    def test_both_representations_agree_when_false(self, tmp_path: Path) -> None:
        """Negative control: the default (no assurance.require_complete
        set at all) must agree too, not just the True case this bug hid
        behind (a fixed-input regression test only proves the reported
        input, per this repo's own bug-class-regression-testing
        convention -- the False side is a real, independently-checkable
        sibling case, not a restatement of the same assertion)."""
        old_p, new_p = _write_pair(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--contract",
                "auto",
                "-o",
                "json=-",
            ],
        )
        assert result.exit_code in (1, 2, 4), result.output
        payload = json.loads(result.output)

        top_level = payload["effective_config_fields"]["gate.require_complete_analysis"]
        contract_context_value = payload["contract_context"]["evaluation_context"][
            "resolved_config"
        ]["gate"]["require_complete_analysis"]

        assert top_level == "False", payload["effective_config_fields"]
        assert contract_context_value is False, payload["contract_context"]
        assert (top_level == "True") == (contract_context_value is True)


class TestRequireCompleteAnalysisFieldProvenance:
    """P2 (Codex review, fresh evidence after
    ``TestRequireCompleteAnalysisReceiptConsistency`` above landed): that fix
    threaded the resolved *value* through to ``with_resolved_gate``, but
    ``field_provenance["gate.require_complete_analysis"]`` was still absent
    from the persisted receipt -- the receipt could show *that* the gate was
    enabled but not *why* (which layer/file resolved it). Fixed by
    constructing the entry directly in ``record_resolved_config`` (this
    field has no D7 resolver of its own -- see ``with_resolved_gate``'s own
    docstring for why that is the correct fix rather than projecting it
    into ``ProjectCompatibilityInputs``)."""

    def test_provenance_entry_present_when_config_sets_it(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".abicheck.yml"
        config_path.write_text(
            "assurance:\n  require_complete: true\n", encoding="utf-8"
        )
        old_p, new_p = _write_pair(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--contract",
                "auto",
                "--config",
                str(config_path),
                "-o",
                "json=-",
            ],
        )
        assert result.exit_code in (1, 2, 4), result.output
        payload = json.loads(result.output)

        field_provenance = payload["contract_context"]["evaluation_context"][
            "field_provenance"
        ]
        entry = field_provenance.get("gate.require_complete_analysis")
        assert entry is not None, field_provenance
        assert entry["layer"] == "project_config", entry
        assert entry["field_location"] == "assurance.require_complete", entry

    def test_provenance_entry_absent_when_not_set(self, tmp_path: Path) -> None:
        """Negative control: the "absent, not defaulted" rule -- an unset
        field gets no provenance entry at all, mirroring how an unsupplied
        severity category is handled."""
        old_p, new_p = _write_pair(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--contract",
                "auto",
                "-o",
                "json=-",
            ],
        )
        assert result.exit_code in (1, 2, 4), result.output
        payload = json.loads(result.output)

        field_provenance = payload["contract_context"]["evaluation_context"][
            "field_provenance"
        ]
        assert "gate.require_complete_analysis" not in field_provenance

    def test_provenance_entry_absent_when_assurance_block_omits_the_key(
        self, tmp_path: Path
    ) -> None:
        """An ``assurance:`` block present WITHOUT ``require_complete`` is
        the same "not stated" case as an entirely-omitted block above --
        still no provenance entry. ``require_complete`` is currently the
        ONLY key ``assurance:`` accepts, so an empty mapping is the one way
        to state "the block is present but the key isn't"."""
        config_path = tmp_path / ".abicheck.yml"
        config_path.write_text("assurance: {}\n", encoding="utf-8")
        old_p, new_p = _write_pair(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--contract",
                "auto",
                "--config",
                str(config_path),
                "-o",
                "json=-",
            ],
        )
        assert result.exit_code in (1, 2, 4), result.output
        payload = json.loads(result.output)

        field_provenance = payload["contract_context"]["evaluation_context"][
            "field_provenance"
        ]
        assert "gate.require_complete_analysis" not in field_provenance

    def test_provenance_entry_present_for_an_explicit_false(
        self, tmp_path: Path
    ) -> None:
        """P2 finding (Codex review, fresh evidence, PR #1222 fourth
        round): an EXPLICIT ``assurance.require_complete: false`` is a
        real, deliberate statement in the document -- distinct from never
        having mentioned the setting at all (the negative control above)
        -- and must produce a real provenance entry naming the config path/
        digest, even though the resolved gate value (``false``) is
        identical to the built-in default."""
        config_path = tmp_path / ".abicheck.yml"
        config_path.write_text(
            "assurance:\n  require_complete: false\n", encoding="utf-8"
        )
        old_p, new_p = _write_pair(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--contract",
                "auto",
                "--config",
                str(config_path),
                "-o",
                "json=-",
            ],
        )
        assert result.exit_code in (1, 2, 4), result.output
        payload = json.loads(result.output)

        gate = payload["contract_context"]["evaluation_context"]["resolved_config"][
            "gate"
        ]
        assert gate["require_complete_analysis"] is False

        field_provenance = payload["contract_context"]["evaluation_context"][
            "field_provenance"
        ]
        entry = field_provenance.get("gate.require_complete_analysis")
        assert entry is not None, field_provenance
        assert entry["layer"] == "project_config", entry
        assert entry["field_location"] == "assurance.require_complete", entry
        assert entry["path"] == str(config_path), entry
        assert entry["sha256"], entry


class TestRequireCompleteAnalysisProvenanceIdentity:
    """P2 (Codex review, fresh evidence after
    ``TestRequireCompleteAnalysisFieldProvenance`` above landed): the
    provenance entry named the ``project_config`` layer but not the config
    document itself -- for a project whose ``.abicheck.yml`` sets ONLY
    ``assurance.require_complete`` (no other override, so no OTHER
    field_provenance entry happens to name the same file), the persisted
    receipt could not identify or replay which exact file/digest enabled
    the gate. Fixed by threading the real, already-resolved project-config
    path/digest through ``record_resolved_config`` -> ``with_resolved_gate``
    (``cli_compare_receipt.py``, ``contract_context.py``)."""

    def test_provenance_entry_names_the_real_config_path_and_digest(
        self, tmp_path: Path
    ) -> None:
        config_path = tmp_path / ".abicheck.yml"
        config_path.write_text(
            "assurance:\n  require_complete: true\n", encoding="utf-8"
        )
        old_p, new_p = _write_pair(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--contract",
                "auto",
                "--config",
                str(config_path),
                "-o",
                "json=-",
            ],
        )
        assert result.exit_code in (1, 2, 4), result.output
        payload = json.loads(result.output)

        field_provenance = payload["contract_context"]["evaluation_context"][
            "field_provenance"
        ]
        entry = field_provenance.get("gate.require_complete_analysis")
        assert entry is not None, field_provenance
        # Not a bare stub: the entry identifies the actual document (path
        # and content digest), the same identity every other project-
        # config-sourced field_provenance entry in this receipt carries.
        assert entry["path"] == str(config_path), entry
        assert entry.get("sha256"), entry
        selected_by = entry.get("selected_by") or []
        assert len(selected_by) == 1, entry
        hop = selected_by[0]
        assert hop["layer"] == "project_config", hop
        assert hop["option"] == "assurance.require_complete", hop
        assert hop["path"] == str(config_path), hop
        assert hop.get("sha256"), hop

    def test_a_config_setting_only_assurance_still_identifies_itself(
        self, tmp_path: Path
    ) -> None:
        """The exact scenario the finding names: a project config with NO
        other override -- so no sibling field_provenance entry happens to
        carry the same path/digest this one must supply for itself."""
        config_path = tmp_path / ".abicheck.yml"
        config_path.write_text(
            "assurance:\n  require_complete: true\n", encoding="utf-8"
        )
        old_p, new_p = _write_pair(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--contract",
                "auto",
                "--config",
                str(config_path),
                "-o",
                "json=-",
            ],
        )
        assert result.exit_code in (1, 2, 4), result.output
        payload = json.loads(result.output)

        field_provenance = payload["contract_context"]["evaluation_context"][
            "field_provenance"
        ]
        # No other field in this minimal config contributed a provenance
        # entry at the project_config layer for this same document --
        # this entry is the only place that identity can come from.
        other_project_entries = {
            key: value
            for key, value in field_provenance.items()
            if key != "gate.require_complete_analysis"
            and value.get("layer") == "project_config"
        }
        assert other_project_entries == {}, field_provenance
        entry = field_provenance["gate.require_complete_analysis"]
        assert entry["path"] == str(config_path), entry
        assert entry.get("sha256"), entry
