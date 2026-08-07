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

"""ADR-049 Phase 3 — the native ``abicheck compare`` CLI's own ``--contract-
evaluation`` flag.

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
        """--contract-evaluation must reach compare_snapshots as a real
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
            ["compare", str(old_p), str(new_p), "--contract-evaluation"],
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
                "--contract-evaluation",
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
                "--contract-evaluation",
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
                "--contract-evaluation",
                "--format",
                "json",
            ],
        )
        assert legacy.exit_code == 4, legacy.output
        ctx = json.loads(legacy.output)["contract_context"]["evaluation_context"]
        gate = ctx["resolved_config"]["gate"]
        # No --severity-* flag and no config: `auto` resolved to `legacy`.
        assert gate["exit_code_scheme"] == "legacy"
        assert gate["severity"]["abi_breaking"] == "error"
        assert (
            ctx["field_provenance"]["gate.exit_code_scheme"]["layer"]
            == "built_in_default"
        )
        for category in ("abi_breaking", "potential_breaking", "addition"):
            assert (
                ctx["field_provenance"][f"gate.severity.{category}"]["layer"]
                == "built_in_default"
            )

        scored = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--contract-evaluation",
                "--format",
                "json",
                "--severity-abi-breaking",
                "warning",
            ],
        )
        ctx = json.loads(scored.output)["contract_context"]["evaluation_context"]
        gate = ctx["resolved_config"]["gate"]
        # A severity setting flips `auto` to the severity-aware scheme, and the
        # typed flag is what selected the level.
        assert gate["exit_code_scheme"] == "severity"
        assert gate["severity"]["abi_breaking"] == "warning"
        prov = ctx["field_provenance"]
        assert prov["gate.severity.abi_breaking"]["layer"] == "explicit_cli"
        # ...and only that category. The other three were not typed *and* no
        # project config supplied them, so they are the built-in defaults --
        # `severity_active` is run-wide ("set anywhere"), and using it per
        # category named a `.abicheck.yml` that does not exist here (Codex
        # review).
        assert prov["gate.severity.addition"]["layer"] == "built_in_default"

    def test_explicit_exit_code_scheme_records_its_own_provenance(self, tmp_path):
        """A typed ``--exit-code-scheme`` is ``EXPLICIT_CLI``, not the
        ``built_in_default`` layer an ``auto`` resolution gets."""
        old_p, new_p = _write_pair(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--contract-evaluation",
                "--format",
                "json",
                "--exit-code-scheme",
                "legacy",
            ],
        )
        ctx = json.loads(result.output)["contract_context"]["evaluation_context"]
        assert ctx["resolved_config"]["gate"]["exit_code_scheme"] == "legacy"
        assert (
            ctx["field_provenance"]["gate.exit_code_scheme"]["layer"] == "explicit_cli"
        )

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
                "--contract-evaluation",
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
                "--contract-evaluation",
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
        assert "--contract-evaluation" in result.output


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
                "--contract-evaluation",
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
    directory/package fan-out now threads --contract-evaluation/--contract
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
                "--contract-evaluation",
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
        # No --contract-evaluation: every pre-existing directory/package
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

    def test_contract_requires_contract_evaluation_on_directory_inputs(self, tmp_path):
        # The generic --contract-without--contract-evaluation UsageError
        # (cli_compare_helpers._reject_incoherent_compare_flags) runs
        # unconditionally ahead of the directory/package dispatch, so this
        # still rejects -- unlike --contract-evaluation itself, --contract
        # alone was never meaningfully "rejected only for directories".
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old, new = _breaking_pair()
        (old_dir / "libfoo.json").write_text(snapshot_to_json(old), encoding="utf-8")
        (new_dir / "libfoo.json").write_text(snapshot_to_json(new), encoding="utf-8")

        result = CliRunner().invoke(
            main,
            ["compare", str(old_dir), str(new_dir), "--contract", "public"],
        )
        assert result.exit_code != 0
        assert "--contract requires --contract-evaluation" in result.output

    def test_pack_still_rejected_on_directory_inputs(self, tmp_path):
        # --pack is deliberately NOT part of this parity slice: applying a
        # pack's policy/contract/gate overrides per library still needs its
        # own resolve-once-apply-per-pair design.
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
            "kind: contract\nversion: 1\nassignments:\n  contract.mode: public\n",
            encoding="utf-8",
        )

        result = CliRunner().invoke(
            main,
            ["compare", str(old_dir), str(new_dir), "--pack", str(pack_path)],
        )
        assert result.exit_code != 0
        assert "not supported for directory/package" in result.output
        assert "--pack" in result.output


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
                "--contract-evaluation",
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
                "--contract-evaluation",
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
                "--contract-evaluation",
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
                "--contract-evaluation",
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
        # --contract-evaluation tag was silently dropped for this one
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
                "--contract-evaluation",
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
