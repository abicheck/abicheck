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

"""ADR-049 Phase 3 — the native ``abicheck compare`` CLI's own ``--contract``
flag.

``service.compare_snapshots``/``run_compare_request``/``service.run_compare``
already forward a ``contract_evaluation`` keyword (``tests/test_service_unit.
py``'s ``TestContractEvaluationThreading``) and the MCP ``abi_compare`` tool
already exposes it, but until now no CLI front end did — ``cli.py`` was at
its 2000-line AI-readiness hard cap, blocking a new ``@click.option`` (see
``docs/contribute/plans/public-contract-default.md`` Phase 3). Extracting the
ADR-043 app-usage/required-symbol option family into
``cli_options.app_usage_scope_options`` freed the headroom; this file covers
the resulting CLI flag the same way ``tests/test_cli_comparability_gate.py``
covers the sibling ``--diagnostic-comparison`` escape hatch and
``tests/test_compare_dispatch.py`` covers its directory/package rejection."""

from __future__ import annotations

import json
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from abicheck.checker import DiffResult, Verdict
from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json

# ADR-049 Phase 3 -- app-usage scoping requires real library binaries (not
# JSON snapshots), so --used-by tests stub `dumper.dump` the same way
# tests/test_cov95_cli.py's TestUsedByScoping does, rather than driving a
# real compiler.


def _fn(name: str, mangled: str, ret: str = "int") -> Function:
    return Function(
        name=name, mangled=mangled, return_type=ret, visibility=Visibility.PUBLIC
    )


def _breaking_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
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
    return old, new


def _write_pair(tmp_path: Path) -> tuple[Path, Path]:
    old, new = _breaking_pair()
    old_p = tmp_path / "old.json"
    new_p = tmp_path / "new.json"
    old_p.write_text(snapshot_to_json(old), encoding="utf-8")
    new_p.write_text(snapshot_to_json(new), encoding="utf-8")
    return old_p, new_p


class TestFlagForwarded:
    def test_contract_evaluation_flag_forwarded_to_compare_snapshots(
        self, tmp_path, monkeypatch
    ):
        """--contract must reach compare_snapshots as a real
        keyword, not be silently dropped at the Click/run_compare boundary
        (the same class of regression test_cli_comparability_gate.py already
        guards for --diagnostic-comparison)."""
        old_p, new_p = _write_pair(tmp_path)

        captured: dict[str, object] = {}

        def _fake_compare_snapshots(*_a, **kw):
            captured["contract_evaluation"] = kw.get("contract_evaluation")
            return DiffResult(
                old_version="1.0",
                new_version="2.0",
                library="libfoo.so.1",
                verdict=Verdict.NO_CHANGE,
            )

        monkeypatch.setattr(
            "abicheck.service.compare_snapshots", _fake_compare_snapshots
        )

        result = CliRunner().invoke(
            main,
            ["compare", str(old_p), str(new_p), "--contract", "public"],
        )
        assert result.exit_code == 0
        assert captured["contract_evaluation"] is True

    def test_contract_evaluation_defaults_to_false(self, tmp_path, monkeypatch):
        old_p, new_p = _write_pair(tmp_path)

        captured: dict[str, object] = {}

        def _fake_compare_snapshots(*_a, **kw):
            captured["contract_evaluation"] = kw.get("contract_evaluation")
            return DiffResult(
                old_version="1.0",
                new_version="2.0",
                library="libfoo.so.1",
                verdict=Verdict.NO_CHANGE,
            )

        monkeypatch.setattr(
            "abicheck.service.compare_snapshots", _fake_compare_snapshots
        )

        result = CliRunner().invoke(main, ["compare", str(old_p), str(new_p)])
        assert result.exit_code == 0
        assert captured["contract_evaluation"] is False


class TestEndToEndJsonReport:
    def test_flag_stamps_contract_relevance_in_json_report(self, tmp_path):
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
        assert result.exit_code == 4, result.output
        payload = json.loads(result.output)
        stamped = [c for c in payload["changes"] if "contract_relevance" in c]
        assert stamped, "fixture must produce at least one shadow-evaluated finding"
        for c in stamped:
            assert c["contract_relevance"] in {
                "IN_CONTRACT",
                "UNKNOWN_UNRESOLVED",
                "UNKNOWN_UNPROVEN",
                "PROVEN_OUT_OF_CONTRACT",
                "NOT_APPLICABLE",
            }
            assert isinstance(c["contract_reason_code"], str)

    def test_evidence_refs_and_context_are_declared_in_the_schema(self, tmp_path):
        """The ledger (Phase 3) and persisted context (Phase 4) in a real report.

        Asserts schema *declaration* on top of ``jsonschema.validate``:
        ``compare_report.schema.json`` is ``additionalProperties: true``, so
        validation alone cannot tell a correctly-declared key from an
        accepted-but-undeclared one -- the exact gap that let the
        ``suppression_audit`` key ship undeclared (schema 2.24).
        """
        jsonschema = pytest.importorskip("jsonschema")
        from abicheck.schemas import load_compare_report_schema

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
        assert result.exit_code == 4, result.output
        payload = json.loads(result.output)
        schema = load_compare_report_schema()
        jsonschema.validate(instance=payload, schema=schema)
        assert "contract_context" in schema["properties"]
        assert "contract_evidence_refs" in schema["$defs"]["change"]["properties"]

        context = payload["contract_context"]
        assert {"contract_evidence", "evaluation_context", "decision_receipt"} <= set(
            context
        )
        # Every reference a finding cites must name a record the same report
        # carries -- a dangling ref is indistinguishable from one that failed
        # to serialize (contract_evidence_collect.validate_decision_evidence).
        known = {
            entry["record"]["id"] for entry in context["contract_evidence"]["providers"]
        }
        stamped = [c for c in payload["changes"] if "contract_evidence_refs" in c]
        assert stamped, "fixture must produce at least one stamped finding"
        for change in stamped:
            for ref in change["contract_evidence_refs"]:
                assert ref in known or ref.startswith("run:"), ref

    def test_audit_ledger_entries_join_to_the_decision_receipt(self) -> None:
        """A demoted finding's ledger entry must carry the receipt's key.

        The receipt is keyed by ``finding_id``; the audit-ledger serializers
        emitted only the contract fields, and also omit ``old_value``/
        ``new_value``, so a consumer could neither read the key nor recompute
        it (Codex review, fresh evidence).
        """
        import json as _json

        from abicheck.checker import compare as _compare
        from abicheck.model import (
            AbiSnapshot as _Snap,
            RecordType as _Rec,
            ScopeOrigin as _Origin,
        )
        from abicheck.reporter import to_json as _to_json

        pub = _Rec(
            name="Pub", kind="struct", size_bits=64, origin=_Origin.PUBLIC_HEADER
        )
        old = _Snap(
            library="libfoo.so.1",
            version="1.0",
            from_headers=True,
            functions=[
                Function(
                    name="api",
                    mangled="api",
                    return_type="Pub *",
                    visibility=Visibility.PUBLIC,
                    origin=_Origin.PUBLIC_HEADER,
                )
            ],
            types=[
                pub,
                _Rec(
                    name="Hidden",
                    kind="struct",
                    size_bits=64,
                    origin=_Origin.PRIVATE_HEADER,
                ),
            ],
        )
        new = _Snap(
            library="libfoo.so.1",
            version="2.0",
            from_headers=True,
            functions=old.functions,
            types=[
                pub,
                _Rec(
                    name="Hidden",
                    kind="struct",
                    size_bits=128,
                    origin=_Origin.PRIVATE_HEADER,
                ),
            ],
        )
        result = _compare(old, new, contract_evaluation=True)
        assert result.out_of_surface_changes, "fixture must demote a finding"
        payload = _json.loads(_to_json(result))
        receipt = payload["contract_context"]["decision_receipt"][
            "relevance_by_finding"
        ]
        demoted = payload["surface_scope"]["out_of_surface_changes"]
        assert demoted
        for entry in demoted:
            assert entry["finding_id"] in receipt
            assert receipt[entry["finding_id"]] == entry["contract_relevance"]

    def test_omitted_by_default(self, tmp_path):
        old_p, new_p = _write_pair(tmp_path)
        result = CliRunner().invoke(
            main,
            ["compare", str(old_p), str(new_p), "--format", "json"],
        )
        assert result.exit_code == 4, result.output
        payload = json.loads(result.output)
        assert "contract_context" not in payload
        for c in payload["changes"]:
            assert "contract_relevance" not in c
            assert "contract_reason_code" not in c
            assert "contract_assurance" not in c
            assert "contract_evidence_refs" not in c

    def test_persisted_gate_is_the_one_the_run_was_scored_with(self, tmp_path):
        """``checker.compare`` never sees the gate -- the front end resolves it
        and applies it after the core verb returns -- so the persisted context
        recorded ``GateConfig()``'s built-in defaults for every run: the
        ``severity`` scheme and the default severity levels, even for a
        ``legacy``-scheme run or one that moved a category with a flag (Codex
        review, fresh evidence). The block is documented as the *complete*
        resolved configuration, so those defaults were a false receipt.
        """
        old_p, new_p = _write_pair(tmp_path)
        legacy = CliRunner().invoke(
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
        assert legacy.exit_code == 4, legacy.output
        ctx = json.loads(legacy.output)["contract_context"]["evaluation_context"]
        gate = ctx["resolved_config"]["gate"]
        # No severity setting anywhere: resolved to `legacy`.
        # gate.exit_code_scheme is purely derived (PR G2) -- no provenance.
        assert gate["exit_code_scheme"] == "legacy"
        assert gate["severity"]["abi_breaking"] == "error"
        assert "gate.exit_code_scheme" not in ctx["field_provenance"]
        for category in ("abi_breaking", "potential_breaking", "addition"):
            assert (
                ctx["field_provenance"][f"gate.severity.{category}"]["layer"]
                == "built_in_default"
            )

        # The per-category levels are config-only now (the hidden
        # `--severity-<category>` flags were removed), so the tier that can
        # state one is the project config.
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text("severity:\n  abi_breaking: warning\n", encoding="utf-8")
        scored = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--contract",
                "public",
                "--format",
                "json",
                "--config",
                str(cfg),
            ],
        )
        ctx = json.loads(scored.output)["contract_context"]["evaluation_context"]
        gate = ctx["resolved_config"]["gate"]
        # A severity setting flips `auto` to the severity-aware scheme, and the
        # config is what selected the level.
        assert gate["exit_code_scheme"] == "severity"
        assert gate["severity"]["abi_breaking"] == "warning"
        prov = ctx["field_provenance"]
        assert prov["gate.severity.abi_breaking"]["layer"] == "project_config"
        # ...and only that category. The config supplied no other level, so
        # the rest are the built-in defaults -- `severity_active` is run-wide
        # ("set anywhere"), and using it per category named the config for a
        # value it never stated (Codex review).
        assert prov["gate.severity.addition"]["layer"] == "built_in_default"

    def test_explicit_severity_preset_records_its_own_provenance(self, tmp_path):
        """A typed ``--severity-preset`` is ``EXPLICIT_CLI``, not the
        ``built_in_default`` layer an unstated resolution gets. (PR G2
        deleted the sibling ``--exit-code-scheme``/its provenance entry
        entirely -- that field is now purely derived, nothing to assert.)"""
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
                "--severity-preset",
                "strict",
            ],
        )
        ctx = json.loads(result.output)["contract_context"]["evaluation_context"]
        assert ctx["resolved_config"]["gate"]["exit_code_scheme"] == "severity"
        assert ctx["field_provenance"]["gate.preset"]["layer"] == "explicit_cli"

    def test_a_typed_contract_flag_is_not_an_api_request(self, tmp_path):
        """``checker.compare`` sees a value, not the option that supplied it.

        The core verb can honestly claim only ``API_REQUEST`` for a mode it
        was handed, so a user who typed ``--contract exports`` had their flag
        recorded as a programmatic request and the audit context could not
        tell the two apart (Codex review, fresh evidence).
        """
        old_p, new_p = _write_pair(tmp_path)
        typed = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--contract",
                "exports",
                "--format",
                "json",
            ],
        )
        ctx = json.loads(typed.output)["contract_context"]["evaluation_context"]
        assert ctx["resolved_config"]["contract"]["mode"] == "exports"
        provenance = ctx["field_provenance"]["contract.mode"]
        assert provenance["layer"] == "explicit_cli"
        assert provenance["selected_by"][0]["option"] == "--contract"

    def test_an_untyped_contract_flag_keeps_the_legacy_alias_source(self, tmp_path):
        """The refresh is opt-in per run, not a blanket overwrite.

        ``--scope-public-headers`` selects the domain through D7's
        ``LEGACY_ALIAS`` layer, which ``resolve_legacy_contract_mode``
        already recorded correctly -- claiming ``EXPLICIT_CLI`` for it would
        name an option the user never typed.
        """
        old_p, new_p = _write_pair(tmp_path)
        legacy = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                # --contract auto asks for a decision without naming a domain,
                # so the legacy alias below is what actually selects one.
                "--contract",
                "auto",
                "--scope-public-headers",
                "--format",
                "json",
            ],
        )
        ctx = json.loads(legacy.output)["contract_context"]["evaluation_context"]
        assert ctx["field_provenance"]["contract.mode"]["layer"] != "explicit_cli"

    def test_help_all_mentions_flag(self):
        result = CliRunner().invoke(main, ["compare", "--help-all"])
        assert result.exit_code == 0
        assert "--contract" in result.output
        # The standalone --contract flag is gone: naming a domain
        # is the request, so there is no second way to ask for the same thing.
        assert "--contract-evaluation" not in result.output


class TestShowFilteredAuditLedger:
    def test_renders_contract_tag_in_out_of_surface_ledger(self, tmp_path):
        # Regression (Codex review, fresh evidence): --show-filtered's stderr
        # audit ledgers (cli_audit.echo_filtered_surface/echo_reconciled) were
        # the one remaining per-finding contract-decision rendering site left
        # unstamped after the JSON/Markdown fixes -- the finding is already
        # stamped PROVEN_OUT_OF_CONTRACT by the time _finalize_compare_result
        # runs, but the printer never read it.
        from abicheck.model import RecordType

        old = AbiSnapshot(
            library="lib",
            version="1",
            functions=[_fn("public_api", "_Z10public_apiv", ret="Result *")],
            types=[
                RecordType(name="Result", kind="struct", size_bits=64),
                RecordType(name="InternalCache", kind="struct", size_bits=64),
            ],
        )
        new = AbiSnapshot(
            library="lib",
            version="2",
            functions=[_fn("public_api", "_Z10public_apiv", ret="Result *")],
            types=[
                RecordType(name="Result", kind="struct", size_bits=64),
                RecordType(name="InternalCache", kind="struct", size_bits=128),
            ],
        )
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        old_p.write_text(snapshot_to_json(old), encoding="utf-8")
        new_p.write_text(snapshot_to_json(new), encoding="utf-8")

        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--scope-public-headers",
                "--show-filtered",
                "--contract",
                "public",
                "--format",
                "json",
            ],
        )
        assert "Filtered as non-public ABI surface" in result.output
        assert "InternalCache" in result.output
        assert "[contract: PROVEN_OUT_OF_CONTRACT" in result.output

    def test_omits_contract_tag_by_default(self, tmp_path):
        from abicheck.model import RecordType

        old = AbiSnapshot(
            library="lib",
            version="1",
            functions=[_fn("public_api", "_Z10public_apiv", ret="Result *")],
            types=[
                RecordType(name="Result", kind="struct", size_bits=64),
                RecordType(name="InternalCache", kind="struct", size_bits=64),
            ],
        )
        new = AbiSnapshot(
            library="lib",
            version="2",
            functions=[_fn("public_api", "_Z10public_apiv", ret="Result *")],
            types=[
                RecordType(name="Result", kind="struct", size_bits=64),
                RecordType(name="InternalCache", kind="struct", size_bits=128),
            ],
        )
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        old_p.write_text(snapshot_to_json(old), encoding="utf-8")
        new_p.write_text(snapshot_to_json(new), encoding="utf-8")

        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--scope-public-headers",
                "--show-filtered",
                "--format",
                "json",
            ],
        )
        assert "InternalCache" in result.output
        assert "[contract:" not in result.output


class TestReleaseFanOutContractParity:
    """CLI-audit P1 (release/package contract parity): the per-library
    directory/package fan-out now threads --contract
    straight into each pair's own service.run_compare() call, the exact
    same Tier-2 chokepoint a single-pair `compare` uses -- so a library
    compared through the fan-out gets the identical contract decision it
    would from comparing it individually. This used to be an outright
    UsageError ("not supported for directory/package comparisons yet");
    replaced here by positive coverage now that it works."""

    def test_contract_evaluation_applies_per_library(self, tmp_path):
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old, new = _breaking_pair()
        (old_dir / "libfoo.json").write_text(snapshot_to_json(old), encoding="utf-8")
        (new_dir / "libfoo.json").write_text(snapshot_to_json(new), encoding="utf-8")
        out_dir = tmp_path / "reports"

        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_dir),
                str(new_dir),
                "--contract",
                "public",
                "--format",
                "json",
                "--output-dir",
                str(out_dir),
            ],
        )
        assert result.exit_code == 4, result.output
        summary = json.loads(result.stdout)
        assert summary["verdict"] == "BREAKING"

        # The per-library --output-dir report is a full single-pair-shaped
        # `to_json()` document, so it already carries the complete ADR-049
        # per-finding shape for free -- no extra plumbing needed beyond
        # threading contract_evaluation into service.run_compare.
        lib_report = json.loads((out_dir / "libfoo.json").read_text())
        stamped = [c for c in lib_report["changes"] if "contract_relevance" in c]
        assert stamped, "per-library report must carry ADR-049 contract fields"

    def test_contract_evaluation_off_by_default(self, tmp_path):
        # No --contract: every pre-existing directory/package
        # report is unaffected -- library JSON carries no contract fields.
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old, new = _breaking_pair()
        (old_dir / "libfoo.json").write_text(snapshot_to_json(old), encoding="utf-8")
        (new_dir / "libfoo.json").write_text(snapshot_to_json(new), encoding="utf-8")
        out_dir = tmp_path / "reports"

        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_dir),
                str(new_dir),
                "--format",
                "json",
                "--output-dir",
                str(out_dir),
            ],
        )
        assert result.exit_code == 4, result.output
        summary = json.loads(result.stdout)
        assert "contract_coverage_exit_contribution" not in summary
        lib_report = json.loads((out_dir / "libfoo.json").read_text())
        assert not any("contract_relevance" in c for c in lib_report["changes"])

    def test_contract_alone_implies_contract_evaluation_on_directory_inputs(
        self, tmp_path
    ):
        # CLI audit PR 3/5: --contract alone is the whole request
        # (abicheck.cli_options.resolve_contract_evaluation), resolved
        # unconditionally in run_compare ahead of the directory/package
        # dispatch -- so this now behaves identically to explicitly passing
        # both flags (test_contract_evaluation_applies_per_library above),
        # not a UsageError. Directories were never special-cased for this
        # rule, so this mirrors the single-pair behavior exactly.
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old, new = _breaking_pair()
        (old_dir / "libfoo.json").write_text(snapshot_to_json(old), encoding="utf-8")
        (new_dir / "libfoo.json").write_text(snapshot_to_json(new), encoding="utf-8")
        out_dir = tmp_path / "reports"

        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_dir),
                str(new_dir),
                "--contract",
                "public",
                "--format",
                "json",
                "--output-dir",
                str(out_dir),
            ],
        )
        assert result.exit_code == 4, result.output
        lib_report = json.loads((out_dir / "libfoo.json").read_text())
        assert any("contract_relevance" in c for c in lib_report["changes"]), (
            "--contract public must stamp contract_relevance on a directory/"
            "package compare, same as on a single pair"
        )

    def test_gate_pack_applied_on_directory_inputs(self, tmp_path):
        # CLI cleanup phase two, "PR B": --pack now applies both a
        # `policy.overrides`/`surface.internal_namespaces` contribution
        # (slice 1) and a `kind: gate` pack's `gate.severity.*` contribution
        # (slice 2) to the release fan-out uniformly -- see
        # test_pack_application.py's TestOnlyAppliedFieldsAreAccepted for the
        # exit-code-differs assertions this test doesn't repeat. PR G2
        # deleted `gate.exit_code_scheme` as an assignable field entirely
        # (the scheme is purely derived from whether a severity setting is
        # in effect at all) -- a bare `gate.severity.abi_breaking: error`
        # pack still resolves and applies cleanly here, and is itself
        # exactly the kind of setting that flips the derived scheme to
        # `severity`; it's just not on its own enough to move this
        # particular pair's exit code, since abi_breaking already defaults
        # to `error` under both the legacy and severity schemes.
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old, new = _breaking_pair()
        (old_dir / "libfoo.json").write_text(snapshot_to_json(old), encoding="utf-8")
        (new_dir / "libfoo.json").write_text(snapshot_to_json(new), encoding="utf-8")
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        pack_path = pack_dir / "pack.yml"
        pack_path.write_text(
            "id: gate_scheme\nkind: gate\nversion: 1\n"
            "assignments:\n  gate.severity.abi_breaking: error\n",
            encoding="utf-8",
        )

        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_dir),
                str(new_dir),
                "--pack",
                str(pack_path),
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 4, result.output
        assert (
            json.loads(result.output)["severity"]["config"]["abi_breaking"] == "error"
        )

    def test_pack_field_this_kind_may_not_assign_still_rejected(self, tmp_path):
        # A `kind: contract` pack assigning `contract.mode` (deliberately not
        # a routable field for any pack kind -- ADR-049 D8 keeps contract/
        # policy/gate distinct) fails the same way it always has: resolving
        # the pack for the release fan-out does not loosen field routing.
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old, new = _breaking_pair()
        (old_dir / "libfoo.json").write_text(snapshot_to_json(old), encoding="utf-8")
        (new_dir / "libfoo.json").write_text(snapshot_to_json(new), encoding="utf-8")
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        pack_path = pack_dir / "pack.yml"
        pack_path.write_text(
            "id: bad_route\nkind: contract\nversion: 1\n"
            "assignments:\n  contract.mode: public\n",
            encoding="utf-8",
        )

        result = CliRunner().invoke(
            main,
            ["compare", str(old_dir), str(new_dir), "--pack", str(pack_path)],
        )
        assert result.exit_code == 64, result.output
        assert "may not assign" in result.output


class TestUsedByScopingStampsExplicitEvidence:
    """Regression (Codex review, PR #658, fresh evidence): --contract-
    evaluation combined with --used-by/--required-symbol ran the shadow
    evaluator before ADR-043's app-usage/required-symbol scoping applied --
    scoped_only_changes (fresh Change objects scope_diff_to_app/
    scope_diff_to_required_symbols synthesize) and synthetic missing-
    contract label entries were never stamped, and an existing
    result.changes entry the scoping pass marks relevant kept whatever
    weaker header-derived decision the shadow evaluator had already
    computed. Mirrors tests/test_mcp_server_unit.py's identical coverage
    for the MCP abi_compare tool, which this fix's shared
    contract_evaluation.stamp_explicit_scope_contract_evaluation helper is
    reused from."""

    def _setup(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock

        from abicheck import dumper as dumper_mod

        app = tmp_path / "app"
        app.write_bytes(b"\x7fELF" + b"\x00" * 200)
        old = tmp_path / "old.so"
        old.write_bytes(b"\x7fELF" + b"\x00" * 200)
        new = tmp_path / "new.so"
        new.write_bytes(b"\x7fELF" + b"\x00" * 200)
        old_snap = AbiSnapshot(library="libfoo.so", version="1.0", functions=[])
        new_snap = AbiSnapshot(library="libfoo.so", version="2.0", functions=[])
        monkeypatch.setattr(
            dumper_mod, "dump", MagicMock(side_effect=[old_snap, new_snap])
        )
        return app, old, new

    def _patch_scope(self, monkeypatch, result):
        import abicheck.appcompat as appcompat_mod

        monkeypatch.setattr(appcompat_mod, "scope_diff_to_app", lambda *a, **k: result)

    def test_used_by_missing_symbol_gets_contract_evaluation(
        self, tmp_path, monkeypatch
    ):
        from abicheck.appcompat import AppCompatResult

        app, old, new = self._setup(tmp_path, monkeypatch)
        scoped = AppCompatResult(
            app_path=str(app),
            old_lib_path=str(old),
            new_lib_path=str(new),
            required_symbols={"_Z5entryv"},
            required_symbol_count=1,
            missing_symbols=["_Z5entryv"],
            verdict=Verdict.BREAKING,
        )
        self._patch_scope(monkeypatch, scoped)

        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old),
                str(new),
                "--used-by",
                str(app),
                "--contract",
                "public",
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 4, result.output
        payload = json.loads(result.stdout)
        missing_entries = [
            c for c in payload["changes"] if c["kind"] == "used_by_missing_symbol"
        ]
        assert missing_entries
        for c in missing_entries:
            assert c["contract_relevance"] == "IN_CONTRACT"
            assert c["contract_reason_code"] == (
                "explicit_consumer_or_required_symbol_evidence"
            )

    def test_used_by_missing_symbol_omits_contract_fields_by_default(
        self, tmp_path, monkeypatch
    ):
        from abicheck.appcompat import AppCompatResult

        app, old, new = self._setup(tmp_path, monkeypatch)
        scoped = AppCompatResult(
            app_path=str(app),
            old_lib_path=str(old),
            new_lib_path=str(new),
            required_symbols={"_Z5entryv"},
            required_symbol_count=1,
            missing_symbols=["_Z5entryv"],
            verdict=Verdict.BREAKING,
        )
        self._patch_scope(monkeypatch, scoped)

        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old),
                str(new),
                "--used-by",
                str(app),
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 4, result.output
        payload = json.loads(result.stdout)
        missing_entries = [
            c for c in payload["changes"] if c["kind"] == "used_by_missing_symbol"
        ]
        assert missing_entries
        for c in missing_entries:
            assert "contract_relevance" not in c
            assert "contract_reason_code" not in c

    def test_used_by_scoped_only_change_gets_contract_evaluation(
        self, tmp_path, monkeypatch
    ):
        # A fresh Change scope_diff_to_app synthesizes (never present in
        # result.changes) must also be stamped, not just a reused/existing
        # finding.
        from abicheck.appcompat import AppCompatResult
        from abicheck.checker_policy import ChangeKind
        from abicheck.diff_helpers import make_change

        app, old, new = self._setup(tmp_path, monkeypatch)
        synthetic = make_change(
            ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
            symbol="_Z5entryv",
            name=app.name,
        )
        scoped = AppCompatResult(
            app_path=str(app),
            old_lib_path=str(old),
            new_lib_path=str(new),
            required_symbols={"_Z5entryv"},
            required_symbol_count=1,
            breaking_for_app=[synthetic],
            verdict=Verdict.BREAKING,
        )
        self._patch_scope(monkeypatch, scoped)

        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old),
                str(new),
                "--used-by",
                str(app),
                "--contract",
                "public",
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 4, result.output
        payload = json.loads(result.stdout)
        matches = [
            c
            for c in payload["changes"]
            if c["kind"] == ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED.value
        ]
        assert matches
        for c in matches:
            assert c["contract_relevance"] == "IN_CONTRACT"
        # The persisted receipt must agree with the report emitted beside it:
        # `compare()` freezes it before this scoping pass runs, so a receipt
        # that isn't refreshed afterwards makes `replay_original_decisions`
        # reproduce decisions that were never the run's own (Codex review,
        # fresh evidence).
        receipt = payload["contract_context"]["decision_receipt"]
        by_finding = receipt["relevance_by_finding"]
        for c in matches:
            assert by_finding[c["finding_id"]] == "IN_CONTRACT"

    def test_scoped_stamping_refreshes_the_persisted_receipt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Every emitted finding carrying a contract decision must appear in
        # the receipt with that same decision -- including one the scoping
        # pass *overwrote* (the shadow evaluator had already recorded a
        # weaker header-derived decision for it inside compare()), and the
        # synthesized missing-contract entry, which has no backing Change
        # and so reached neither collection the refresh used to merge
        # (Codex review, fresh evidence).
        from abicheck.appcompat import AppCompatResult
        from abicheck.checker_policy import ChangeKind
        from abicheck.diff_helpers import make_change

        app, old, new = self._setup(tmp_path, monkeypatch)
        synthetic = make_change(
            ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
            symbol="_Z5entryv",
            name=app.name,
        )
        scoped = AppCompatResult(
            app_path=str(app),
            old_lib_path=str(old),
            new_lib_path=str(new),
            required_symbols={"_Z5entryv", "_Z6absentv"},
            required_symbol_count=2,
            breaking_for_app=[synthetic],
            # `_Z6absentv` is covered by no Change, so it survives
            # `uncovered_missing_symbols`' dedup and becomes the synthetic
            # missing-contract entry this test is about; `_Z5entryv` is
            # covered by `synthetic` and is correctly deduped away.
            missing_symbols=["_Z5entryv", "_Z6absentv"],
            verdict=Verdict.BREAKING,
        )
        self._patch_scope(monkeypatch, scoped)

        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old),
                str(new),
                "--used-by",
                str(app),
                "--contract",
                "public",
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 4, result.output
        payload = json.loads(result.stdout)
        by_finding = payload["contract_context"]["decision_receipt"][
            "relevance_by_finding"
        ]
        decided = [c for c in payload["changes"] if c.get("contract_relevance")]
        assert decided
        # No `finding_id` filter: an entry that carries a decision but no id
        # is exactly the unjoinable case, so skipping it here would hide it.
        assert any(c["kind"] == "used_by_missing_symbol" for c in decided)
        for c in decided:
            assert c.get("finding_id"), c
            assert by_finding.get(c["finding_id"]) == c["contract_relevance"]

    def test_used_by_missing_symbol_gets_contract_evaluation_in_markdown(
        self, tmp_path, monkeypatch
    ):
        # Regression (Codex review, fresh evidence): the default markdown
        # format's own "## Additional scoped-gate findings" fold-in
        # (cli_compare_fold._fold_scoped_compat_into_text) built missing-
        # label lines from a bare string, never the stamped dict entry the
        # JSON branch uses -- so a plain `compare --used-by ... --contract-
        # evaluation` (no --format json) reported the gated finding with no
        # contract decision at all.
        from abicheck.appcompat import AppCompatResult

        app, old, new = self._setup(tmp_path, monkeypatch)
        scoped = AppCompatResult(
            app_path=str(app),
            old_lib_path=str(old),
            new_lib_path=str(new),
            required_symbols={"_Z5entryv"},
            required_symbol_count=1,
            missing_symbols=["_Z5entryv"],
            verdict=Verdict.BREAKING,
        )
        self._patch_scope(monkeypatch, scoped)

        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old),
                str(new),
                "--used-by",
                str(app),
                "--contract",
                "public",
            ],
        )
        assert result.exit_code == 4, result.output
        assert "Additional scoped-gate findings" in result.output
        assert "[contract: IN_CONTRACT" in result.output

    def test_used_by_missing_symbol_omits_contract_tag_in_markdown_by_default(
        self, tmp_path, monkeypatch
    ):
        from abicheck.appcompat import AppCompatResult

        app, old, new = self._setup(tmp_path, monkeypatch)
        scoped = AppCompatResult(
            app_path=str(app),
            old_lib_path=str(old),
            new_lib_path=str(new),
            required_symbols={"_Z5entryv"},
            required_symbol_count=1,
            missing_symbols=["_Z5entryv"],
            verdict=Verdict.BREAKING,
        )
        self._patch_scope(monkeypatch, scoped)

        result = CliRunner().invoke(
            main,
            ["compare", str(old), str(new), "--used-by", str(app)],
        )
        assert result.exit_code == 4, result.output
        assert "Additional scoped-gate findings" in result.output
        assert "[contract:" not in result.output

    def test_used_by_missing_symbol_gets_contract_evaluation_in_root_cause_mode(
        self, tmp_path, monkeypatch
    ):
        # Regression (Codex review, fresh evidence, PR #658): --report-mode
        # root-cause builds its own missing-label lines independently of
        # cli_compare_fold._fold_scoped_compat_into_text (the latter is
        # explicitly skipped for root-cause markdown), so the same
        # --contract tag was silently dropped for this one
        # report mode even after the fold-in path was fixed.
        from abicheck.appcompat import AppCompatResult

        app, old, new = self._setup(tmp_path, monkeypatch)
        scoped = AppCompatResult(
            app_path=str(app),
            old_lib_path=str(old),
            new_lib_path=str(new),
            required_symbols={"_Z5entryv"},
            required_symbol_count=1,
            missing_symbols=["_Z5entryv"],
            verdict=Verdict.BREAKING,
        )
        self._patch_scope(monkeypatch, scoped)

        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old),
                str(new),
                "--used-by",
                str(app),
                "--contract",
                "public",
                "--report-mode",
                "root-cause",
            ],
        )
        assert result.exit_code == 4, result.output
        assert "Root Causes" in result.output
        assert "[contract: IN_CONTRACT" in result.output
        assert "assurance:" in result.output

    def test_used_by_missing_symbol_omits_contract_tag_in_root_cause_mode_by_default(
        self, tmp_path, monkeypatch
    ):
        from abicheck.appcompat import AppCompatResult

        app, old, new = self._setup(tmp_path, monkeypatch)
        scoped = AppCompatResult(
            app_path=str(app),
            old_lib_path=str(old),
            new_lib_path=str(new),
            required_symbols={"_Z5entryv"},
            required_symbol_count=1,
            missing_symbols=["_Z5entryv"],
            verdict=Verdict.BREAKING,
        )
        self._patch_scope(monkeypatch, scoped)

        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old),
                str(new),
                "--used-by",
                str(app),
                "--report-mode",
                "root-cause",
            ],
        )
        assert result.exit_code == 4, result.output
        assert "Root Causes" in result.output
        assert "[contract:" not in result.output


class TestContractFlagResolvers:
    """``resolve_contract_evaluation``/``resolve_contract_domain`` directly.

    Both are exercised end to end by the CLI tests above and by
    ``test_scan_compare_parity``'s compare/scan ``--contract auto`` pair, but
    only ever on the branches a real command takes. The mapping is the whole
    reason ``auto`` is safe to offer -- it is what keeps "the caller declined
    to name a domain" spelled the way every D7 tier below ``explicit_cli``
    reads it -- so it is pinned here as its own contract rather than left as
    a by-product of two callers (AGENTS.md's primitive-level guidance).
    """

    def test_any_domain_asks_for_evaluation_and_absence_does_not(self) -> None:
        from abicheck.cli_options import resolve_contract_evaluation

        for mode in ("public", "exports", "all", "auto"):
            assert resolve_contract_evaluation(mode) is True, mode
        assert resolve_contract_evaluation(None) is False

    def test_only_auto_is_mapped_away(self) -> None:
        from abicheck.cli_options import resolve_contract_domain

        for mode in ("public", "exports", "all", None):
            assert resolve_contract_domain(mode) is mode, mode
        assert resolve_contract_domain("auto") is None

    @staticmethod
    def _ctx(**params: object) -> click.Context:
        ctx = click.Context(click.Command("probe"))
        ctx.params = dict(params)
        for name in params:
            ctx.set_parameter_source(name, click.core.ParameterSource.COMMANDLINE)
        return ctx

    def test_auto_is_normalized_on_the_context_too(self) -> None:
        """`scan` rebuilds its resolver inputs from ``ctx.params`` and its
        typed set from ``ctx.get_parameter_source``, so normalizing only the
        returned value left ``auto`` reaching ``coerce_contract_mode`` and
        raising (Codex review). Both must be corrected together."""
        from abicheck.cli_options import resolve_contract_domain

        ctx = self._ctx(contract_mode="auto")
        assert resolve_contract_domain("auto", ctx) is None
        assert ctx.params["contract_mode"] is None
        assert (
            ctx.get_parameter_source("contract_mode")
            is click.core.ParameterSource.DEFAULT
        )

    def test_a_named_domain_leaves_the_context_alone(self) -> None:
        """The demotion is specific to ``auto``. A real domain *was* typed, so
        its ``explicit_cli`` provenance must survive -- rewriting it here would
        silently drop the top D7 tier for every ordinary invocation."""
        from abicheck.cli_options import resolve_contract_domain

        ctx = self._ctx(contract_mode="exports")
        assert resolve_contract_domain("exports", ctx) == "exports"
        assert ctx.params["contract_mode"] == "exports"
        assert (
            ctx.get_parameter_source("contract_mode")
            is click.core.ParameterSource.COMMANDLINE
        )

    def test_a_context_without_the_parameter_is_not_an_error(self) -> None:
        """Only `compare`/`scan` declare ``--contract``; the helper must not
        assume its caller's context carries the parameter at all."""
        from abicheck.cli_options import resolve_contract_domain

        ctx = self._ctx()
        assert resolve_contract_domain("auto", ctx) is None
        assert "contract_mode" not in ctx.params
