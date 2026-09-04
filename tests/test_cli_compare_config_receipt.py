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
one object through the canonical resolver and installing it -- plus the two
claims the module docstring of :mod:`abicheck.cli_compare_receipt` makes:
that the gate the receipt reports is the gate the run was scored with, and
that a ``--profile``-injected value is recorded as the profile's choice
rather than as a default nobody made.

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
            "--format",
            "json",
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

    def test_a_symbols_file_contract_names_the_file_option_and_the_file(self, tmp_path):
        """A `--required-symbols FILE` run never passes `--required-symbol`.

        Naming the inline flag would fabricate a selector and leave the list
        that really chose `plugin_abi` unidentifiable (Codex review, fresh
        evidence).
        """
        listed = tmp_path / "required.txt"
        listed.write_text("# contract\napi_b\n", encoding="utf-8")
        ctx = _context(tmp_path, "--required-symbols", str(listed))

        assert ctx["resolved_config"]["policy"]["base"]["id"] == "plugin_abi"
        prov = ctx["field_provenance"]["policy.base"]
        assert prov["path"] == str(listed)
        hop = prov["selected_by"][0]
        assert hop["option"] == "--required-symbols"
        assert hop["path"] == str(listed)
        # The digest is over the file's raw bytes, from the same read that
        # parsed the symbols.
        import hashlib

        assert hop["sha256"] == hashlib.sha256(listed.read_bytes()).hexdigest()

    def test_a_symbols_file_that_contributed_nothing_is_not_the_selector(
        self, tmp_path
    ):
        """Passing a file is not the same as the file supplying the contract.

        With `--required-symbol api_b --required-symbols empty.txt` the file
        parses to nothing, so naming it omits the option that actually made
        the contract non-empty (Codex review, fresh evidence — the sharper
        half of the file-attribution fix).
        """
        empty = tmp_path / "required.txt"
        empty.write_text("# nothing but a comment\n", encoding="utf-8")
        ctx = _context(
            tmp_path, "--required-symbol", "api_b", "--required-symbols", str(empty)
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


class TestRunProfileLayer:
    """``--profile``'s injected values are the profile's choice, not defaults."""

    def test_ci_gate_scheme_is_recorded_as_the_run_profile(self, tmp_path):
        """CLI cleanup phase two PR G2 migrated ``ci-gate`` from injecting
        ``exit_code_scheme: "severity"`` to injecting
        ``severity_preset: "default"`` -- behavior-preserving, since stating
        a severity preset is exactly what flips the now-purely-derived
        ``gate.exit_code_scheme`` to ``severity``, and it's the field
        ``apply_compare_profile`` actually fills in without stamping a
        source, so a resolution that never saw the profile would otherwise
        answer ``legacy`` for a run that really scored ``severity`` -- a
        wrong *value*, not merely an under-claimed source.
        """
        ctx = _context(tmp_path, "--profile", "ci-gate")

        assert ctx["resolved_config"]["gate"]["exit_code_scheme"] == "severity"
        prov = ctx["field_provenance"]["gate.preset"]
        assert prov["layer"] == "run_profile"
        assert prov["reference"] == "ci-gate"
        assert prov["selected_by"][0]["option"] == "--profile"

    def test_a_typed_flag_still_outranks_the_profile(self, tmp_path):
        """The profile is documented to yield to an explicit flag, and
        ``apply_compare_profile`` never overwrites one -- so the receipt must
        name the flag, not the profile."""
        ctx = _context(
            tmp_path,
            "--profile",
            "ci-gate",
            "--severity-preset",
            "strict",
        )
        assert ctx["resolved_config"]["gate"]["preset"]["id"] == "strict"
        assert ctx["field_provenance"]["gate.preset"]["layer"] == (
            "explicit_cli"
        )

    def test_no_profile_leaves_no_run_profile_layer(self, tmp_path):
        ctx = _context(tmp_path)
        assert ctx["field_provenance"]["gate.preset"]["layer"] == (
            "built_in_default"
        )
        # gate.exit_code_scheme is purely derived and carries no
        # field_provenance entry at all any more (PR G2).
        assert "gate.exit_code_scheme" not in ctx["field_provenance"]

    def test_apply_compare_profile_records_only_what_it_injected(self):
        """The meta record is what the receipt reads; an explicitly-set option
        must not appear in it, or the profile would claim a value the user
        chose."""
        from abicheck.cli_options import RUN_PROFILE_META_KEY, apply_compare_profile

        class _Ctx:
            def __init__(self, explicit: set[str]) -> None:
                self.meta: dict = {}
                self._explicit = explicit

            def get_parameter_source(self, name):
                from click.core import ParameterSource

                return (
                    ParameterSource.COMMANDLINE
                    if name in self._explicit
                    else ParameterSource.DEFAULT
                )

        ctx = _Ctx(explicit={"severity_preset"})
        kwargs: dict = {"profile": "ci-gate", "severity_preset": "strict"}
        apply_compare_profile(ctx, kwargs)
        record = ctx.meta[RUN_PROFILE_META_KEY]
        assert record["name"] == "ci-gate"
        assert "severity_preset" not in record["injected"]
        assert record["injected"]["depth"] == "headers"

    def test_no_profile_records_nothing(self):
        from abicheck.cli_options import RUN_PROFILE_META_KEY, apply_compare_profile

        class _Ctx:
            def __init__(self) -> None:
                self.meta: dict = {}

        ctx = _Ctx()
        apply_compare_profile(ctx, {"profile": None})
        assert RUN_PROFILE_META_KEY not in ctx.meta


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
            main, ["compare", str(old_p), str(new_p), "--format", "json"]
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
                "--format",
                "json",
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
