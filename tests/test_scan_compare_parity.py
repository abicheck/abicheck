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

"""Field-for-field parity between `compare` and `scan --against` (ADR-049 Phase 5 §6.4 Gate).

`scan --against` now shares `compare`'s policy/suppression/scope config
surface (`abicheck/l0_export_delta.py`, `cli_scan.py`'s `@policy_options`/
`@scope_options`/`--strict-suppressions`/`--public-symbol`/
`--public-symbols-list`/`--pattern-verdicts`/`--env-matrix`). These tests
assert the two commands actually agree end to end on the same JSON snapshot
inputs under identical suppression/policy/scope flags — not just that each
command's own kwargs are threaded correctly in isolation (that's what
`test_scan_baseline_headers.py`'s kwarg-capture test already covers).

Two layers, in escalating strength:

1. **Exit-code agreement** (the original slice): the same inputs and flags
   produce the same exit code from both commands. Necessary, but far from
   §6.4's Gate -- two commands can agree on "breaking" while disagreeing
   about *which* finding broke.
2. **Field-for-field agreement** (`TestFieldForFieldParity`): the two
   commands' *shared comparison findings* are compared as records, joined
   on the canonical identity (`finding_id`) both now emit, across the §6.4
   input/config matrix. The compared fields are exactly the ones §6.4 names
   -- canonical identity, `ChangeKind`, detector provenance, contract
   relevance/reason/assurance/evidence side, compatibility decision, and
   suppression -- so a divergence in any of them fails here rather than
   surviving because both exit codes happened to be `4`.

Both layers run on the same JSON snapshot inputs through Click's
`CliRunner`, end to end -- not on each command's kwargs in isolation
(that's what `test_scan_baseline_headers.py`'s kwarg-capture test covers).
`TestBinaryAndMixedInputParity` extends the same assertions to real
compiled binaries and to a binary-vs-snapshot pair, and is `integration`
because it needs a compiler.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    Function,
    ScopeOrigin,
    Visibility,
)
from abicheck.serialization import snapshot_to_json


def _write_snapshot(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _elf(*names: str) -> ElfMetadata:
    return ElfMetadata(symbols=[ElfSymbol(name=n) for n in names])


def _func(name: str, mangled: str, *, origin=ScopeOrigin.PUBLIC_HEADER) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        visibility=Visibility.PUBLIC,
        access=AccessLevel.PUBLIC,
        origin=origin,
    )


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def old_snap(tmp_path: Path) -> Path:
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        functions=[_func("foo", "_Z3foov"), _func("bar", "_Z3barv")],
        elf=_elf("_Z3foov", "_Z3barv"),
    )
    return _write_snapshot(tmp_path / "old.abi.json", snap)


@pytest.fixture
def new_snap_breaking(tmp_path: Path) -> Path:
    # `bar` removed -> a removed exported symbol is a hard ABI break.
    snap = AbiSnapshot(
        library="libfoo.so",
        version="2.0",
        from_headers=True,
        functions=[_func("foo", "_Z3foov")],
        elf=_elf("_Z3foov"),
    )
    return _write_snapshot(tmp_path / "new_break.abi.json", snap)


@pytest.fixture
def suppress_bar(tmp_path: Path) -> Path:
    supp = tmp_path / "suppress.yml"
    supp.write_text(
        "version: 1\nsuppressions:\n"
        "  - symbol: '_Z3barv'\n"
        "    change_kind: func_removed\n"
        "    reason: 'intentionally removed, see MIGRATION.md'\n",
        encoding="utf-8",
    )
    return supp


def test_unsuppressed_removal_breaks_both_commands_identically(
    runner: CliRunner, old_snap: Path, new_snap_breaking: Path
) -> None:
    compare_res = runner.invoke(
        main, ["compare", str(old_snap), str(new_snap_breaking)]
    )
    scan_res = runner.invoke(
        main, ["scan", str(new_snap_breaking), "--against", str(old_snap)]
    )
    assert compare_res.exit_code == 4, compare_res.output
    assert scan_res.exit_code == 4, scan_res.output


def test_suppressed_removal_is_compatible_in_both_commands_identically(
    runner: CliRunner,
    old_snap: Path,
    new_snap_breaking: Path,
    suppress_bar: Path,
) -> None:
    # Same suppression file, same inputs: `compare --suppress` and
    # `scan --against --suppress` must agree that the removal is no longer
    # gating (ADR-049 Phase 5's whole point -- one shared config surface).
    compare_res = runner.invoke(
        main,
        [
            "compare",
            str(old_snap),
            str(new_snap_breaking),
            "--suppress",
            str(suppress_bar),
        ],
    )
    scan_res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_breaking),
            "--against",
            str(old_snap),
            "--suppress",
            str(suppress_bar),
        ],
    )
    assert compare_res.exit_code == 0, compare_res.output
    assert scan_res.exit_code == 0, scan_res.output


def test_no_scope_public_headers_agrees_across_both_commands(
    runner: CliRunner, old_snap: Path, new_snap_breaking: Path
) -> None:
    # --no-scope-public-headers is a no-op here (the removed symbol is already
    # public-header-scoped either way) but exercises the flag reaching
    # compare_snapshots identically on both sides rather than being silently
    # dropped by `scan --against` (the pre-Phase-5 hardcoded True).
    compare_res = runner.invoke(
        main,
        ["compare", str(old_snap), str(new_snap_breaking), "--no-scope-public-headers"],
    )
    scan_res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_breaking),
            "--against",
            str(old_snap),
            "--no-scope-public-headers",
        ],
    )
    assert compare_res.exit_code == 4, compare_res.output
    assert scan_res.exit_code == 4, scan_res.output


def test_scan_against_exposes_suppression_ledger_like_compare(
    runner: CliRunner,
    old_snap: Path,
    new_snap_breaking: Path,
    suppress_bar: Path,
) -> None:
    # `compare`'s JSON report already surfaces which findings a --suppress
    # rule silenced (DiffResult.suppressed_changes, reporter.py's
    # _add_suppression) -- a per-run suppression audit trail. `scan
    # --against`'s own summary previously had no equivalent: the suppression
    # rule was honored (this Phase 5 slice's earlier work) but *which*
    # finding it silenced was invisible. Assert scan's JSON `diff` block now
    # carries the same audit trail.
    import json

    scan_res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_breaking),
            "--against",
            str(old_snap),
            "--suppress",
            str(suppress_bar),
            "--format",
            "json",
        ],
    )
    assert scan_res.exit_code == 0, scan_res.output
    payload = json.loads(scan_res.output)
    diff = payload["diff"]
    assert diff["suppressed_count"] == 1
    assert diff["suppressed"][0]["symbol"] == "_Z3barv"
    assert diff["suppressed"][0]["kind"] == "func_removed"
    # Codex review: which rule silenced it must be attributable too (falls
    # back to the rule's `reason` since `suppress_bar` sets no `label`).
    assert (
        diff["suppressed"][0]["suppression_rule"]
        == "intentionally removed, see MIGRATION.md"
    )


def test_scan_against_honors_config_suppression_strict_like_compare(
    runner: CliRunner,
    old_snap: Path,
    new_snap_breaking: Path,
    tmp_path: Path,
) -> None:
    # Codex review on PR #657: scan --against read its --strict-suppressions/
    # --scope-public-headers/--public-symbol CLI values raw, never resolving
    # them through the project's .abicheck.yml the way `compare` does (CLI >
    # config > default, ADR-037 D4) -- so suppression.strict: true in a
    # shared project config gated `compare` but silently had no effect on
    # `scan --against`. An EXPIRED suppression rule under a config-declared
    # strict mode must reject both commands identically.
    expired_supp = tmp_path / "suppress.yml"
    expired_supp.write_text(
        "version: 1\nsuppressions:\n"
        "  - symbol: '_Z3barv'\n"
        "    change_kind: func_removed\n"
        "    reason: 'no longer needed'\n"
        '    expires: "2000-01-01"\n',
        encoding="utf-8",
    )
    project_config = tmp_path / ".abicheck.yml"
    project_config.write_text("suppression:\n  strict: true\n", encoding="utf-8")

    compare_res = runner.invoke(
        main,
        [
            "compare",
            str(old_snap),
            str(new_snap_breaking),
            "--suppress",
            str(expired_supp),
            "--config",
            str(project_config),
        ],
    )
    scan_res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_breaking),
            "--against",
            str(old_snap),
            "--suppress",
            str(expired_supp),
            "--config",
            str(project_config),
        ],
    )
    # Neither --strict-suppressions was passed on the CLI for either command
    # -- only the config's suppression.strict: true -- so both must still
    # reject the expired rule identically (exit 1, not the expired rule
    # silently accepted).
    assert compare_res.exit_code == 1, compare_res.output
    assert scan_res.exit_code == 1, scan_res.output
    assert "expired" in scan_res.output.lower()


def test_scan_against_malformed_config_is_usage_error(
    runner: CliRunner, old_snap: Path, new_snap_breaking: Path, tmp_path: Path
) -> None:
    # A malformed .abicheck.yml must surface as a clean usage error, not an
    # uncaught traceback -- same contract as compare's own --config loading.
    bad_config = tmp_path / "bad.abicheck.yml"
    bad_config.write_text("scope: [unclosed\n  public: true", encoding="utf-8")

    scan_res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_breaking),
            "--against",
            str(old_snap),
            "--config",
            str(bad_config),
        ],
    )
    assert scan_res.exit_code == 64, scan_res.output
    assert scan_res.exception is None or isinstance(scan_res.exception, SystemExit)


def test_scan_against_malformed_autodiscovered_config_warns_and_falls_back(
    runner: CliRunner,
    old_snap: Path,
    new_snap_breaking: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Codex review: an *auto-discovered* config (the user never bound to it
    # via explicit --config) must be best-effort, matching
    # merge_compile_config's own identical convention for the compile:
    # block -- warn and fall back to CLI-only settings, not fail the run.
    # Only an *explicit* --config (the case above) is fail-loud.
    import abicheck.cli_helpers_compare as _cch

    bad_config = tmp_path / ".abicheck.yml"
    bad_config.write_text("scope: [unclosed\n  public: true", encoding="utf-8")
    monkeypatch.setattr(_cch, "discover_project_config", lambda start=None: bad_config)

    scan_res = runner.invoke(
        main, ["scan", str(new_snap_breaking), "--against", str(old_snap)]
    )
    assert scan_res.exit_code == 4, scan_res.output  # the real BREAKING verdict
    assert "warning: could not parse auto-discovered" in scan_res.output


def test_scan_against_honors_config_require_justification_like_compare(
    runner: CliRunner,
    old_snap: Path,
    new_snap_breaking: Path,
    tmp_path: Path,
) -> None:
    # Codex review on PR #657: resolve_compare_config also resolves
    # require_justification, but cli_scan.py discarded it -- a reason-less
    # --suppress rule was rejected by `compare --config` (suppression.
    # require_justification: true) but silently accepted by
    # `scan --against --config`, letting an unjustified rule suppress a
    # breaking finding. `scan` has no --require-justification flag of its
    # own (config-only, same as compare's own hidden/demoted flag).
    reasonless_supp = tmp_path / "suppress.yml"
    reasonless_supp.write_text(
        "version: 1\nsuppressions:\n"
        "  - symbol: '_Z3barv'\n"
        "    change_kind: func_removed\n",
        encoding="utf-8",
    )
    project_config = tmp_path / ".abicheck.yml"
    project_config.write_text(
        "suppression:\n  require_justification: true\n", encoding="utf-8"
    )

    compare_res = runner.invoke(
        main,
        [
            "compare",
            str(old_snap),
            str(new_snap_breaking),
            "--suppress",
            str(reasonless_supp),
            "--config",
            str(project_config),
        ],
    )
    scan_res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_breaking),
            "--against",
            str(old_snap),
            "--suppress",
            str(reasonless_supp),
            "--config",
            str(project_config),
        ],
    )
    assert compare_res.exit_code != 0, compare_res.output
    assert scan_res.exit_code != 0, scan_res.output
    assert "reason" in scan_res.output.lower()


def test_run_baseline_compare_forwards_collapse_versioned_symbols(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Codex review on PR #657: resolve_compare_config also resolves
    # collapse_versioned_symbols (scope.collapse_versioned_symbols in
    # .abicheck.yml), but cli_scan.py discarded it before calling
    # compare_snapshots -- an ICU-style version-suffix transition would
    # report COMPATIBLE_WITH_RISK under `compare --config` but BREAKING
    # under `scan --against --config`. Assert the kwarg reaches
    # compare_snapshots (`scan` has no --collapse-versioned-symbols flag of
    # its own, config-only same as require_justification above).
    import abicheck.cli_buildsource as cbs
    import abicheck.service as service
    from abicheck.cli_scan_baseline import _run_baseline_compare

    captured: dict[str, object] = {}

    class _FakeSnap:
        build_source = None

    class _FakeVerdict:
        value = "NO_CHANGE"

    class _FakeDiff:
        verdict = _FakeVerdict()
        breaking: list[object] = []
        source_breaks: list[object] = []
        risk: list[object] = []
        compatible: list[object] = []

    def fake_resolve_input(path, headers, includes, **kw):  # type: ignore[no-untyped-def]
        return _FakeSnap()

    def fake_compare_snapshots(old, new, suppression=None, *, extra_changes, **kw):
        captured["collapse_versioned_symbols"] = kw.get("collapse_versioned_symbols")
        return _FakeDiff()

    monkeypatch.setattr(service, "resolve_input", fake_resolve_input)
    monkeypatch.setattr(service, "compare_snapshots", fake_compare_snapshots)
    monkeypatch.setattr(
        cbs,
        "prepare_embedded_build_source",
        lambda old, new, cm, extra, *rest, **kw: (list(extra), [], {}, None),
    )

    _run_baseline_compare(
        Path("old.so"),
        Path("new.so"),
        _FakeSnap(),
        [],
        "c++",
        "off",
        [],
        [],
        [],
        [],
        scope_to_public_surface=True,
        collapse_versioned_symbols=True,
    )

    assert captured["collapse_versioned_symbols"] is True


def test_scan_audit_mode_skips_comparison_config_resolution(
    runner: CliRunner,
    new_snap_breaking: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Codex review: a plain one-build audit (no --against) must not even
    # attempt to load a project config for scope/suppression settings --
    # every field that config resolves is comparison-only and never
    # consumed by an audit-only run, so a malformed *auto-discovered*
    # config must not fail an otherwise-unrelated audit scan.
    import abicheck.cli_helpers_compare as _cch

    bad_config = tmp_path / ".abicheck.yml"
    bad_config.write_text("scope: [unclosed\n  public: true", encoding="utf-8")
    discovered: list[object] = []

    def _tracking_discover(start=None):  # type: ignore[no-untyped-def]
        discovered.append(start)
        return bad_config

    monkeypatch.setattr(_cch, "discover_project_config", _tracking_discover)

    result = runner.invoke(main, ["scan", str(new_snap_breaking)])

    assert result.exit_code == 0, result.output
    # The comparison-config resolution block (the only caller of
    # discover_project_config in cli_scan.py) must never even run for a
    # plain audit -- not just tolerate a bad result from it.
    assert discovered == []


def test_scan_against_prefers_sources_root_config_over_cwd(
    runner: CliRunner,
    old_snap: Path,
    new_snap_breaking: Path,
    tmp_path: Path,
) -> None:
    # Codex review: `resolve_compile_context`'s own compile: block
    # discovery already prefers the `--sources` tree root over a cwd-upward
    # walk (`discover_build_config`); the comparison-config resolution must
    # use the identical precedence so one `scan --against --sources DIR`
    # invocation can't resolve its compile: settings from one .abicheck.yml
    # and its scope/suppression settings from a different one.
    sources_root = tmp_path / "project"
    sources_root.mkdir()
    (sources_root / ".abicheck.yml").write_text(
        "suppression:\n  strict: true\n", encoding="utf-8"
    )
    suppress = tmp_path / "suppress.yml"
    suppress.write_text(
        "version: 1\nsuppressions:\n"
        "  - symbol: '_Z3barv'\n"
        "    change_kind: func_removed\n"
        "    reason: 'no longer needed'\n"
        '    expires: "2000-01-01"\n',
        encoding="utf-8",
    )

    result = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_breaking),
            "--against",
            str(old_snap),
            "--sources",
            str(sources_root),
            "--suppress",
            str(suppress),
        ],
    )
    # No --strict-suppressions on the CLI -- only the sources-root config's
    # suppression.strict: true -- so the expired rule must still be
    # rejected (exit 1), proving the sources-root config was actually found.
    assert result.exit_code == 1, result.output


def test_scan_against_cwd_discovered_config_compile_block_actually_applies(
    runner: CliRunner,
    old_snap: Path,
    new_snap_breaking: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Codex P1 review on PR #657: a cwd-upward-discovered config's
    # scope/suppression settings were being applied, but its compile: block
    # (defines/include dirs/frontend/std/sysroot) never was --
    # resolve_compile_context had no cwd-upward fallback of its own, so
    # that half of the same file was silently dropped. Assert the config's
    # compile.defines actually reach the CompileContext run_scan_core
    # receives, proving the SAME discovered file now backs both halves.
    import abicheck.cli_helpers_compare as _cch
    import abicheck.scan_engine as _se

    cwd_config = tmp_path / ".abicheck.yml"
    cwd_config.write_text("compile:\n  defines:\n    - FOO=1\n", encoding="utf-8")
    monkeypatch.setattr(_cch, "discover_project_config", lambda start=None: cwd_config)

    captured: dict[str, object] = {}
    real_run_scan_core = _se.run_scan_core

    def spying_run_scan_core(**kw):
        captured["compile_context"] = kw.get("compile_context")
        return real_run_scan_core(**kw)

    monkeypatch.setattr("abicheck.cli_scan.run_scan_core", spying_run_scan_core)

    result = runner.invoke(
        main, ["scan", str(new_snap_breaking), "--against", str(old_snap)]
    )

    assert result.exit_code == 4, result.output  # the real BREAKING verdict
    cc = captured["compile_context"]
    assert cc is not None, "compile_context.is_default was wrongly True"
    assert "-DFOO=1" in cc.gcc_option_tokens


# ---------------------------------------------------------------------------
# ADR-049 Phase 5 §6.4 Gate: field-for-field parity, not just exit codes.
# ---------------------------------------------------------------------------

#: The per-finding fields §6.4 requires the two commands to agree on, minus
#: the compatibility decision (which the two spell differently by design --
#: see :func:`_scan_rows`) and the canonical identity (which is the join key,
#: so equality of the two key *sets* is what checks it).
_SHARED_FINDING_FIELDS = (
    "kind",
    "symbol",
    "contract_relevance",
    "contract_reason_code",
    "contract_assurance",
    "contract_evidence_refs",
    # ADR-049 D1's canonical pair. Both commands must state whether policy
    # scored the finding, and what it decided -- a scan row carrying only the
    # relevance could not be compared with the `compare` finding for the same
    # fact, which is the divergence this Gate exists to catch (Codex review).
    # `gate_contribution` is deliberately *not* here: it is a property of a
    # gate `scan --against` does not run (see `_add_contract_fields`).
    "compatibility_evaluation_status",
    "compatibility_decision",
)

#: The three gating buckets `scan --against`'s summary itemizes. `compare`'s
#: report carries compatible findings too; those are outside the shared set
#: this Gate compares (scan's summary was never meant to itemize additions),
#: so they are filtered out rather than counted as a divergence.
_GATING_SEVERITIES = frozenset({"breaking", "api_break", "risk"})


def _compare_rows(report: dict) -> dict[str, dict]:
    """`compare`'s gating findings, keyed by canonical identity."""
    return {
        c["finding_id"]: {
            **{f: c.get(f) for f in _SHARED_FINDING_FIELDS},
            "decision": c["severity"],
        }
        for c in report["changes"]
        if c["severity"] in _GATING_SEVERITIES
    }


def _scan_rows(diff: dict) -> dict[str, dict]:
    """`scan --against`'s gating findings, in `_compare_rows`' shape.

    The compatibility decision is the one field the two commands genuinely
    spell differently: `compare` calls it ``severity`` (one label per
    finding) while scan's summary calls it ``bucket`` (which of the three
    itemized verdict lists the finding came out of). They are the same
    value -- both are ``report_model.VERDICT_TO_SEVERITY_LABEL`` of the
    finding's effective verdict -- so normalizing the key here is a
    renaming, not a fudge, and
    :func:`test_the_two_decision_spellings_are_the_same_vocabulary` pins
    that claim independently.
    """
    return {
        f["finding_id"]: {
            **{k: f.get(k) for k in _SHARED_FINDING_FIELDS},
            "decision": f["bucket"],
        }
        for f in diff.get("findings", [])
    }


def _both(
    runner: CliRunner, old: Path, new: Path, extra: list[str], tmp_path: Path
) -> tuple[dict, dict]:
    """Run both commands on the same inputs/flags; return their JSON payloads.

    Always passes ``--contract`` so the contract fields are
    actually populated -- an all-``None`` comparison of those four keys
    would pass vacuously.
    """
    import json

    report_path = tmp_path / "compare-report.json"
    scan_path = tmp_path / "scan-report.json"
    compare_res = runner.invoke(
        main,
        [
            "compare",
            str(old),
            str(new),
            "--format",
            "json",
            "--contract",
            "public",
            "-o",
            str(report_path),
            *extra,
        ],
    )
    assert compare_res.exit_code in (0, 1, 2, 4), compare_res.output
    scan_res = runner.invoke(
        main,
        [
            "scan",
            str(new),
            "--against",
            str(old),
            "--format",
            "json",
            "--contract",
            "public",
            "-o",
            str(scan_path),
            *extra,
        ],
    )
    assert scan_res.exit_code in (0, 1, 2, 4), scan_res.output
    assert compare_res.exit_code == scan_res.exit_code, (
        f"gate contribution diverged for {extra}: "
        f"compare={compare_res.exit_code} scan={scan_res.exit_code}"
    )
    # Both read from a file rather than stdout: `scan` legitimately writes
    # warnings/progress to stdout alongside the report, so parsing its
    # captured output works for a snapshot pair and breaks the moment the
    # operand is a real binary.
    return (
        json.loads(report_path.read_text(encoding="utf-8")),
        json.loads(scan_path.read_text(encoding="utf-8")),
    )


def _mixed_snapshots(tmp_path: Path) -> tuple[Path, Path]:
    """A pair exercising several detectors and several `ChangeKind` families.

    Deliberately richer than the module's single-removal fixtures: a removal,
    a return-type change, an addition, a reachable record whose layout grew,
    and an enum that gained a member -- so the parity assertion covers the
    function, type, and enum detectors at once rather than proving agreement
    on one `func_removed` and generalizing from it.
    """
    from abicheck.model import EnumMember, EnumType, Param, RecordType, TypeField

    def fn(name: str, mangled: str, ret: str = "void", params: tuple = ()) -> Function:
        return Function(
            name=name,
            mangled=mangled,
            return_type=ret,
            params=[Param(name=n, type=t) for n, t in params],
            visibility=Visibility.PUBLIC,
            access=AccessLevel.PUBLIC,
            origin=ScopeOrigin.PUBLIC_HEADER,
        )

    def cfg(fields: tuple[tuple[str, str], ...], size: int) -> RecordType:
        return RecordType(
            name="Cfg",
            kind="struct",
            fields=[TypeField(name=n, type=t) for n, t in fields],
            size_bits=size,
            origin=ScopeOrigin.PUBLIC_HEADER,
        )

    def mode(members: tuple[tuple[str, int], ...]) -> EnumType:
        return EnumType(
            name="Mode",
            members=[EnumMember(name=n, value=v) for n, v in members],
            origin=ScopeOrigin.PUBLIC_HEADER,
        )

    # `use` keeps Cfg/Mode reachable from the public surface, so their own
    # findings survive public-surface scoping instead of being filtered as
    # unreferenced types.
    use = ("_Z3useP3CfgP4Mode", (("c", "Cfg*"), ("m", "Mode*")))
    old = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        functions=[
            fn("foo", "_Z3foov"),
            fn("bar", "_Z3barv"),
            fn("baz", "_Z3bazv", "int"),
            fn("use", use[0], params=use[1]),
        ],
        types=[cfg((("a", "int"),), 32)],
        enums=[mode((("M_A", 0),))],
        elf=_elf("_Z3foov", "_Z3barv", "_Z3bazv", use[0]),
    )
    new = AbiSnapshot(
        library="libfoo.so",
        version="2.0",
        from_headers=True,
        functions=[
            fn("foo", "_Z3foov"),
            fn("baz", "_Z3bazv", "long"),
            fn("qux", "_Z3quxv"),
            fn("use", use[0], params=use[1]),
        ],
        types=[cfg((("a", "int"), ("b", "int")), 64)],
        enums=[mode((("M_A", 0), ("M_B", 1)))],
        elf=_elf("_Z3foov", "_Z3bazv", "_Z3quxv", use[0]),
    )
    return (
        _write_snapshot(tmp_path / "mixed_old.abi.json", old),
        _write_snapshot(tmp_path / "mixed_new.abi.json", new),
    )


@pytest.fixture
def mixed_pair(tmp_path: Path) -> tuple[Path, Path]:
    return _mixed_snapshots(tmp_path)


class TestFieldForFieldParity:
    """§6.4's Gate as an executable check, over its own input/config matrix."""

    @pytest.mark.parametrize(
        "label,extra",
        [
            ("defaults", []),
            ("policy", ["--policy", "sdk_vendor"]),
            ("scope-off", ["--no-scope-public-headers"]),
            ("explicit-scope", ["--scope-public-headers"]),
            ("contract-public", ["--contract", "public"]),
            ("contract-exports", ["--contract", "exports"]),
            ("contract-all", ["--contract", "all"]),
            ("pattern-verdicts", ["--pattern-verdicts"]),
        ],
    )
    def test_shared_findings_are_identical_records(
        self,
        runner: CliRunner,
        mixed_pair: tuple[Path, Path],
        tmp_path: Path,
        label: str,
        extra: list[str],
    ) -> None:
        old, new = mixed_pair
        report, scan = _both(runner, old, new, extra, tmp_path)

        rows_c = _compare_rows(report)
        rows_s = _scan_rows(scan["diff"])
        # Scan's summary caps its itemized findings (`_MAX_BASELINE_FINDINGS`);
        # a truncated cell would fail the set comparison below for a reason
        # that has nothing to do with parity, so name that cause directly
        # rather than leaving a future fixture to produce an opaque set diff
        # (CodeRabbit review).
        assert not scan["diff"].get("findings_truncated"), label
        # A vacuous pass (both empty) would satisfy every assertion below,
        # so require the matrix cell to actually have findings to compare.
        assert rows_c, f"{label}: no gating findings to compare"
        # Canonical identity: the same finding set, not merely the same count.
        assert set(rows_c) == set(rows_s), label
        # ChangeKind, contract relevance/reason/assurance/evidence side, and
        # the compatibility decision, per finding.
        assert rows_c == rows_s, label

    def test_a_forced_public_symbol_is_identical_from_config(
        self, runner: CliRunner, mixed_pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """The forced-public-symbol matrix cell, moved off its removed flag.

        ``--public-symbol`` duplicated ``scope.public_symbols`` and was
        removed with the rest of the hidden config duplicates, so the overlay
        now reaches both commands through ``--config`` alone -- which is
        exactly the parity §6.4 is about: one config surface, two commands,
        identical records. A separate test rather than a matrix row because
        the config file needs ``tmp_path``, which a parametrize list has no
        access to.
        """
        old, new = mixed_pair
        cfg = tmp_path / "public-symbol.abicheck.yml"
        cfg.write_text("scope:\n  public_symbols: [_Z3barv]\n", encoding="utf-8")
        report, scan = _both(runner, old, new, ["--config", str(cfg)], tmp_path)

        rows_c = _compare_rows(report)
        rows_s = _scan_rows(scan["diff"])
        assert not scan["diff"].get("findings_truncated")
        assert rows_c, "no gating findings to compare"
        assert rows_c == rows_s

    @pytest.mark.parametrize(
        "extra",
        [[], ["--policy", "sdk_vendor"], ["--no-scope-public-headers"]],
    )
    def test_detector_provenance_is_identical(
        self,
        runner: CliRunner,
        mixed_pair: tuple[Path, Path],
        tmp_path: Path,
        extra: list[str],
    ) -> None:
        # §6.4 names detector provenance among the equal fields. Both sides
        # emit the same "detectors with findings or a coverage gap" list.
        old, new = mixed_pair
        report, scan = _both(runner, old, new, extra, tmp_path)
        assert scan["diff"]["detectors"] == report["detectors"]
        assert any(d["changes_count"] > 0 for d in report["detectors"])

    def test_suppression_is_identical_field_for_field(
        self,
        runner: CliRunner,
        mixed_pair: tuple[Path, Path],
        suppress_bar: Path,
        tmp_path: Path,
    ) -> None:
        # Suppression is one of §6.4's equal fields, and the audit trail is
        # where a divergence would actually show: a rule can silence a
        # finding on one side and a *different* one on the other while both
        # commands still report "1 suppressed".
        old, new = mixed_pair
        report, scan = _both(
            runner, old, new, ["--suppress", str(suppress_bar)], tmp_path
        )

        assert (
            scan["diff"]["suppressed_count"]
            == report["suppression"]["suppressed_count"]
        )
        scan_supp = {
            (f["symbol"], f["kind"], f["suppression_rule"], f["finding_id"])
            for f in scan["diff"]["suppressed"]
        }
        compare_supp = {
            (
                c["symbol"],
                c["kind"],
                c["impact_assessment"]["decision"]["suppression_rule"],
                c["finding_id"],
            )
            for c in report["suppression"]["suppressed_changes"]
        }
        assert scan_supp == compare_supp
        # And the silenced finding is gone from the shared set on both sides.
        assert "_Z3barv" not in {r["symbol"] for r in _compare_rows(report).values()}
        assert "_Z3barv" not in {r["symbol"] for r in _scan_rows(scan["diff"]).values()}

    def test_scan_only_findings_are_appended_not_substituted(
        self,
        runner: CliRunner,
        mixed_pair: tuple[Path, Path],
        tmp_path: Path,
    ) -> None:
        # §6.4: "Scan-only source/cross-check findings may be appended; they
        # cannot rewrite the shared comparison findings." Scan's own audit
        # blocks live outside `diff`, so the shared set stays byte-identical
        # to `compare`'s no matter what the audit found.
        old, new = mixed_pair
        report, scan = _both(runner, old, new, [], tmp_path)
        assert _scan_rows(scan["diff"]) == _compare_rows(report)
        # The scan-only material really is present, and really is separate:
        # it lives beside `diff`, never inside it, so it structurally cannot
        # rewrite a shared comparison finding.
        assert "crosscheck" in scan
        assert {"advisories", "coverage", "pattern_scan"} <= set(scan)
        assert "crosscheck" not in scan["diff"]

    def test_the_two_decision_spellings_are_the_same_vocabulary(self) -> None:
        # `_scan_rows` normalizes scan's `bucket` onto compare's `severity`.
        # That is only a renaming if the two really draw from one vocabulary
        # -- pinned here so a future relabeling of either side fails loudly
        # instead of silently making the parity comparison compare nothing.
        from abicheck.report_model import VERDICT_TO_SEVERITY_LABEL

        labels = set(VERDICT_TO_SEVERITY_LABEL.values())
        assert _GATING_SEVERITIES < labels
        assert labels == {"breaking", "api_break", "risk", "compatible"}

    def test_scan_contract_keys_match_the_reporters(
        self,
        runner: CliRunner,
        mixed_pair: tuple[Path, Path],
        tmp_path: Path,
    ) -> None:
        # `cli_scan_baseline._add_contract_fields` deliberately re-implements
        # `reporter._add_contract_evaluation_fields` rather than importing
        # it. Assert the two name the same decision the same way, so the
        # duplication cannot drift into two vocabularies for one field.
        old, new = mixed_pair
        report, scan = _both(runner, old, new, [], tmp_path)

        def contract_keys(entries: list[dict]) -> set[frozenset[str]]:
            return {
                frozenset(k for k in e if k.startswith("contract_")) for e in entries
            }

        gating = [c for c in report["changes"] if c["severity"] in _GATING_SEVERITIES]
        expected = {
            frozenset(
                {
                    "contract_relevance",
                    "contract_reason_code",
                    "contract_assurance",
                    "contract_evidence_refs",
                }
            )
        }
        assert contract_keys(gating) == expected
        assert contract_keys(scan["diff"]["findings"]) == expected

    def test_contract_alone_implies_contract_evaluation_in_both(
        self, runner: CliRunner, old_snap: Path, new_snap_breaking: Path,
        tmp_path: Path,
    ) -> None:
        # CLI audit PR 3/5: `--contract` alone now *implies*
        # `--contract` in both commands (the one place they must
        # still agree -- abicheck.cli_options.resolve_contract_evaluation is
        # the single shared resolver both route through), rather than the two
        # rejecting it identically as a UsageError. Each now behaves exactly
        # as if `--contract` had also been passed explicitly.
        #
        # Exit-code parity alone doesn't prove the evaluator actually ran --
        # a pre-existing ABI break can produce the same exit code whether or
        # not --contract did anything (Codex review). Assert on the real
        # contract_context receipt instead: it's present only when the
        # evaluator ran, so implicit and explicit must both carry one, and
        # the two must be byte-identical (same real assertion
        # test_the_scan_context_is_byte_identical_to_compares makes for
        # explicit-vs-explicit, extended to implicit-vs-explicit here).
        import json

        implicit_args = ["--contract", "exports"]
        explicit_args = ["--contract", "exports"]

        def _compare_json(args: list[str], name: str) -> dict:
            out = tmp_path / f"{name}.json"
            res = runner.invoke(
                main,
                [
                    "compare", str(old_snap), str(new_snap_breaking),
                    "--format", "json", "-o", str(out), *args,
                ],
            )
            assert res.exit_code != 64, res.output
            return json.loads(out.read_text(encoding="utf-8"))

        def _scan_json(args: list[str], name: str) -> dict:
            out = tmp_path / f"{name}.json"
            res = runner.invoke(
                main,
                [
                    "scan", str(new_snap_breaking), "--against", str(old_snap),
                    "--format", "json", "-o", str(out), *args,
                ],
            )
            assert res.exit_code != 64, res.output
            return json.loads(out.read_text(encoding="utf-8"))

        compare_implicit = _compare_json(implicit_args, "compare-implicit")
        compare_explicit = _compare_json(explicit_args, "compare-explicit")
        scan_implicit = _scan_json(implicit_args, "scan-implicit")
        scan_explicit = _scan_json(explicit_args, "scan-explicit")

        assert compare_implicit["contract_context"] is not None
        assert compare_implicit["contract_context"] == compare_explicit["contract_context"]
        assert scan_implicit["diff"]["contract_context"] is not None
        assert (
            scan_implicit["diff"]["contract_context"]
            == scan_explicit["diff"]["contract_context"]
        )

    def test_contract_flags_without_against_are_rejected(
        self, runner: CliRunner, new_snap_breaking: Path
    ) -> None:
        # Same rule the other comparison-only flags already follow: without
        # a baseline there is no comparison for them to configure, so
        # accepting them would silently discard the request.
        res = runner.invoke(
            main, ["scan", str(new_snap_breaking), "--contract", "public"]
        )
        assert res.exit_code == 64, res.output
        assert "--contract" in res.output


_LIB_V1 = """
struct Cfg { int a; };
extern "C" void keep(void) {}
extern "C" void drop(void) {}
extern "C" int use(struct Cfg *c) { return c->a; }
"""

_LIB_V2 = """
struct Cfg { int a; int b; };
extern "C" void keep(void) {}
extern "C" int use(struct Cfg *c) { return c->a; }
extern "C" void added(void) {}
"""


@pytest.mark.integration
class TestBinaryAndMixedInputParity:
    """§6.4's matrix also names *binaries* and *mixed inputs*, not only
    snapshots -- the two commands resolve their operands through different
    code paths (``scan``'s candidate dump plus ``_run_baseline_compare``'s
    baseline parse vs. ``compare``'s two-sided ``resolve_input``), so
    snapshot-only parity does not imply binary parity.
    """

    @staticmethod
    def _libs(tmp_path: Path) -> tuple[Path, Path]:
        from tests._libabigail import compile_shared_lib

        old = tmp_path / "libfoo.so.1"
        new = tmp_path / "libfoo.so.2"
        compile_shared_lib(_LIB_V1, old, lang="cpp", soname="libfoo.so.1")
        compile_shared_lib(_LIB_V2, new, lang="cpp", soname="libfoo.so.1")
        return old, new

    def test_two_real_binaries_agree_field_for_field(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        old, new = self._libs(tmp_path)
        report, scan = _both(runner, old, new, [], tmp_path)
        assert _compare_rows(report), "no gating findings on the binary pair"
        assert _scan_rows(scan["diff"]) == _compare_rows(report)
        assert scan["diff"]["detectors"] == report["detectors"]

    def test_snapshot_baseline_against_a_real_binary_agrees(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        # The mixed case: a persisted JSON baseline on the old side, a live
        # binary on the new side -- the shape a CI job actually runs, and the
        # one where the two commands' operand resolution differs most.
        old, new = self._libs(tmp_path)
        dumped = tmp_path / "old.abi.json"
        dump_res = runner.invoke(main, ["dump", str(old), "-o", str(dumped)])
        assert dump_res.exit_code == 0, dump_res.output

        report, scan = _both(runner, dumped, new, [], tmp_path)
        assert _compare_rows(report), "no gating findings on the mixed pair"
        assert _scan_rows(scan["diff"]) == _compare_rows(report)


class TestServiceLayerContractValidation:
    """`ScanRequest`'s own half of the `--contract` rule (ADR-049 Phase 6).

    `service.compare_snapshots` has always rejected the combination at the
    Tier-2 boundary, so it was never silently accepted -- but reaching that
    check means a whole scan runs first and then fails, while `scan_cmd`
    rejects the same request before any work. `run_scan` now applies the
    identical rule up front (CodeRabbit review).
    """

    def _request(self, new: Path, old: Path, **kwargs):
        from abicheck.service_scan import ScanRequest

        return ScanRequest(binaries=[new], baseline=old, **kwargs)

    def test_a_mode_without_evaluation_is_rejected_before_scanning(
        self, old_snap: Path, new_snap_breaking: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from abicheck.errors import ValidationError
        from abicheck.service_scan import run_scan

        called: list[object] = []
        monkeypatch.setattr(
            "abicheck.scan_engine.run_scan_core",
            lambda **kw: called.append(kw),
        )
        with pytest.raises(ValidationError, match="contract_mode requires"):
            run_scan(
                self._request(new_snap_breaking, old_snap, contract_mode="exports")
            )
        # The point of moving the check: no scanning happened first.
        assert called == []

    def test_a_mode_with_evaluation_is_accepted(
        self, old_snap: Path, new_snap_breaking: Path
    ) -> None:
        from abicheck.service_scan import run_scan

        result = run_scan(
            self._request(
                new_snap_breaking,
                old_snap,
                contract_mode="exports",
                contract_evaluation=True,
            )
        )
        assert result.exit_code == 4

    def test_the_no_baseline_rejection_still_names_both_fields(
        self, new_snap_breaking: Path
    ) -> None:
        # Without a baseline these configure nothing, so they keep going
        # through the existing comparison-only guard rather than the new one.
        from abicheck.errors import ValidationError
        from abicheck.service_scan import ScanRequest, run_scan

        with pytest.raises(ValidationError, match="contract_evaluation"):
            run_scan(
                ScanRequest(binaries=[new_snap_breaking], contract_evaluation=True)
            )

    def test_max_findings_is_forwarded_to_run_scan_core(
        self, old_snap: Path, new_snap_breaking: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Codex review: ScanRequest.max_findings must actually reach
        # run_scan_core, not just exist as a no-op field on the dataclass.
        from abicheck.service_scan import run_scan

        called: dict[str, object] = {}

        def fake_run_scan_core(**kw):
            called.update(kw)
            raise SystemExit  # short-circuit before any real work

        monkeypatch.setattr("abicheck.scan_engine.run_scan_core", fake_run_scan_core)
        with pytest.raises(SystemExit):
            run_scan(self._request(new_snap_breaking, old_snap, max_findings=3))
        assert called["max_findings"] == 3

    @pytest.mark.parametrize("bad_value", [0, -1])
    def test_a_non_positive_max_findings_is_rejected_before_scanning(
        self,
        old_snap: Path,
        new_snap_breaking: Path,
        monkeypatch: pytest.MonkeyPatch,
        bad_value: int,
    ) -> None:
        # Codex review: a bad value used to only surface once _baseline_summary()
        # built the report (wasting the scan) and as a click.ClickException
        # leaking through this click-free API. Now rejected up front, as a
        # plain ValidationError, before any scanning happens.
        from abicheck.errors import ValidationError
        from abicheck.service_scan import run_scan

        called: list[object] = []
        monkeypatch.setattr(
            "abicheck.scan_engine.run_scan_core",
            lambda **kw: called.append(kw),
        )
        with pytest.raises(ValidationError, match="max_findings"):
            run_scan(
                self._request(new_snap_breaking, old_snap, max_findings=bad_value)
            )
        assert called == []


class TestScanResolvesTheSameTypedConfig:
    """Phase 5's other half: "route both direct compare and scan baseline
    compare through the same core **and same typed config**".

    Routing through the same core landed first. Until this, `scan --against`
    resolved no `CompatibilityEvaluationConfig` at all -- its persisted
    `evaluation_context` carried `checker.compare`'s argument-shaped
    reconstruction, so every field read `api_request` regardless of which
    flag actually chose it -- *and* never emitted the context, so the receipt
    its per-finding decisions rest on was computed and dropped.
    """

    def _scan_context(
        self, runner: CliRunner, old: Path, new: Path, extra: list[str], tmp_path: Path
    ) -> dict:
        import json

        out = tmp_path / "scan-ctx.json"
        # --contract is what turns the evaluator on, so a case that does not
        # override the domain still has to name one for a context to exist.
        domain = [] if "--contract" in extra else ["--contract", "public"]
        res = runner.invoke(
            main,
            [
                "scan",
                str(new),
                "--against",
                str(old),
                "--format",
                "json",
                "-o",
                str(out),
                *domain,
                *extra,
            ],
        )
        assert res.exit_code in (0, 1, 2, 4), res.output
        return json.loads(out.read_text(encoding="utf-8"))["diff"]["contract_context"]

    def test_the_scan_emits_a_contract_context_at_all(
        self, runner: CliRunner, mixed_pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        old, new = mixed_pair
        ctx = self._scan_context(runner, old, new, [], tmp_path)
        assert set(ctx) >= {
            "contract_evidence",
            "evaluation_context",
            "decision_receipt",
        }

    @pytest.mark.parametrize(
        "flag,field,layer",
        [
            (["--contract", "exports"], "contract.mode", "explicit_cli"),
            (["--policy", "sdk_vendor"], "policy.base", "legacy_alias"),
        ],
    )
    def test_a_typed_flag_gets_its_real_d7_layer(
        self,
        runner: CliRunner,
        mixed_pair: tuple[Path, Path],
        tmp_path: Path,
        flag: list[str],
        field: str,
        layer: str,
    ) -> None:
        """The whole point: a receipt that says `api_request` for a value a
        CLI flag chose is honest but useless to an audit consumer asking
        *which* layer selected it."""
        old, new = mixed_pair
        ctx = self._scan_context(runner, old, new, flag, tmp_path)
        prov = ctx["evaluation_context"]["field_provenance"][field]
        assert prov["layer"] == layer, prov

    def test_a_config_only_field_gets_its_real_d7_layer(
        self, runner: CliRunner, mixed_pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """``surface.explicit_scope``'s own D7 layer, after its flag was removed.

        The receipt's whole job is naming *which* layer chose a value. With
        ``--public-symbol`` gone the honest answer for this field is
        ``project_config``, not ``explicit_cli`` -- and a receipt that still
        claimed the CLI layer would be exactly the useless-to-an-auditor
        answer the sibling test above exists to prevent.
        """
        old, new = mixed_pair
        cfg = tmp_path / "public-symbol.abicheck.yml"
        cfg.write_text("scope:\n  public_symbols: [_Z3barv]\n", encoding="utf-8")
        ctx = self._scan_context(runner, old, new, ["--config", str(cfg)], tmp_path)
        prov = ctx["evaluation_context"]["field_provenance"]["surface.explicit_scope"]
        assert prov["layer"] == "project_config", prov

    @pytest.mark.parametrize("command", ["compare", "scan"])
    def test_contract_auto_evaluates_without_claiming_an_explicit_domain(
        self,
        runner: CliRunner,
        mixed_pair: tuple[Path, Path],
        tmp_path: Path,
        command: str,
    ) -> None:
        """``--contract auto`` asks for a decision without naming a domain.

        Regression (Codex review): normalizing ``auto`` -> ``None`` in a local
        variable was enough for `compare`, which hands `resolve_and_apply`
        explicit values, but not for `scan`, which rebuilds its resolver
        inputs from ``ctx.params`` and its typed set from
        ``ctx.get_parameter_source``. There ``auto`` survived as a literal and
        reached ``coerce_contract_mode``, which raised `ValueError` -- the
        documented flag crashed instead of evaluating. Both front ends must
        also record the field as *not* explicitly stated, since deferring is
        the whole point of the value.
        """
        import json

        old, new = mixed_pair
        out = tmp_path / f"{command}-auto.json"
        argv = (
            ["compare", str(old), str(new)]
            if command == "compare"
            else ["scan", str(new), "--against", str(old)]
        )
        res = runner.invoke(
            main, [*argv, "--contract", "auto", "--format", "json", "-o", str(out)]
        )
        assert res.exit_code in (0, 1, 2, 4), res.output
        payload = json.loads(out.read_text(encoding="utf-8"))
        report = payload["diff"] if command == "scan" else payload
        ctx = report["contract_context"]
        # It really evaluated ...
        assert set(ctx) >= {"contract_evidence", "evaluation_context"}
        # ... and did not claim the CLI chose the domain.
        prov = ctx["evaluation_context"]["field_provenance"]["contract.mode"]
        assert prov["layer"] != "explicit_cli", prov

    def test_an_unstated_field_is_a_default_not_a_claim(
        self, runner: CliRunner, mixed_pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        old, new = mixed_pair
        ctx = self._scan_context(runner, old, new, [], tmp_path)
        prov = ctx["evaluation_context"]["field_provenance"]
        assert prov["policy.base"]["layer"] == "built_in_default"
        # `scan` has no severity flags, so the gate resolves entirely from
        # built-in defaults. `gate.exit_code_scheme` is purely derived (PR
        # G2) with no `field_provenance` entry -- `gate.preset` stands in.
        assert prov["gate.preset"]["layer"] == "built_in_default"

    def test_the_scan_context_is_byte_identical_to_compares(
        self, runner: CliRunner, mixed_pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """§6.4 again, one level below the findings: the two commands'
        *receipts* for the same inputs must agree too, not just their
        findings. Both go through `contract_context_io`, so a divergence
        here would mean one of them resolved a different configuration."""
        import json

        old, new = mixed_pair
        report_path = tmp_path / "compare-ctx.json"
        res = runner.invoke(
            main,
            [
                "compare",
                str(old),
                str(new),
                "--format",
                "json",
                "--contract",
                "public",
                "-o",
                str(report_path),
            ],
        )
        assert res.exit_code == 4, res.output
        compare_ctx = json.loads(report_path.read_text(encoding="utf-8"))[
            "contract_context"
        ]
        scan_ctx = self._scan_context(runner, old, new, [], tmp_path)
        assert scan_ctx == compare_ctx

    def test_the_coverage_ledger_travels_with_the_scan_receipt(
        self, runner: CliRunner, mixed_pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        import json

        old, new = mixed_pair
        out = tmp_path / "scan-cov.json"
        res = runner.invoke(
            main,
            [
                "scan",
                str(new),
                "--against",
                str(old),
                "--format",
                "json",
                "--contract",
                "public",
                "-o",
                str(out),
            ],
        )
        assert res.exit_code == 4, res.output
        diff = json.loads(out.read_text(encoding="utf-8"))["diff"]
        assert diff["contract_coverage_failures"] == []
        assert diff["contract_coverage_exit_contribution"] == 0

    def test_no_context_without_contract_evaluation(
        self, runner: CliRunner, mixed_pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        import json

        old, new = mixed_pair
        out = tmp_path / "plain-scan.json"
        res = runner.invoke(
            main,
            [
                "scan",
                str(new),
                "--against",
                str(old),
                "--format",
                "json",
                "-o",
                str(out),
            ],
        )
        assert res.exit_code == 4, res.output
        diff = json.loads(out.read_text(encoding="utf-8"))["diff"]
        assert "contract_context" not in diff
        assert "contract_coverage_failures" not in diff


class TestServiceScanReceipt:
    """The Python API path resolves a config too (Codex review).

    `run_scan` was left behind when the CLI was wired: its persisted context
    kept `GateConfig`'s defaults, so the receipt claimed the `severity`
    scheme while `run_scan` computed its 0/2/4 exit straight from the
    compatibility verdict.
    """

    def _result(self, old: Path, new: Path, **kwargs):
        from abicheck.service_scan import ScanRequest, run_scan

        return run_scan(
            ScanRequest(
                binaries=[new], baseline=old, contract_evaluation=True, **kwargs
            )
        )

    def test_the_api_receipt_does_not_claim_a_gate_it_never_used(
        self, mixed_pair: tuple[Path, Path]
    ) -> None:
        old, new = mixed_pair
        result = self._result(old, new)
        ctx = result.report["diff"]["contract_context"]
        gate = ctx["evaluation_context"]["resolved_config"]["gate"]
        # A scan's exit follows its verdict; nothing selected a severity gate.
        assert gate["exit_code_scheme"] == "legacy"
        assert result.exit_code == 4

    def test_a_request_field_is_read_as_stated(
        self, mixed_pair: tuple[Path, Path]
    ) -> None:
        """A typed request has no "unset", so a caller constructing one chose
        those values -- the same rule `compare_request_inputs` follows."""
        old, new = mixed_pair
        result = self._result(old, new, contract_mode="exports")
        ctx = result.report["diff"]["contract_context"]
        config = ctx["evaluation_context"]["resolved_config"]
        assert config["contract"]["mode"] == "exports"

    def test_no_config_is_resolved_without_contract_evaluation(
        self, mixed_pair: tuple[Path, Path]
    ) -> None:
        from abicheck.service_scan import ScanRequest, run_scan

        old, new = mixed_pair
        result = run_scan(ScanRequest(binaries=[new], baseline=old))
        assert "contract_context" not in result.report["diff"]

    def test_the_api_receipt_names_request_fields_not_cli_flags(
        self, mixed_pair: tuple[Path, Path]
    ) -> None:
        """A `ScanRequest` sets `policy`/`scope_to_public_surface` as typed
        fields; recording them as `--policy`/`--scope-public-headers`
        describes a command line nobody ran (Codex review)."""
        from abicheck.compatibility_evaluation_frontend import unstatable_selectors
        from abicheck.service_scan import ScanRequest, _scan_request_config

        old, new = mixed_pair
        config = _scan_request_config(
            ScanRequest(binaries=[new], baseline=old, contract_evaluation=True)
        )
        assert unstatable_selectors(config, request_type=ScanRequest) == []
        hops = {
            field: [hop.option for hop in prov.selected_by]
            for field, prov in config.provenance.items()
        }
        assert "policy" in hops["policy.base"]
        # `ScanRequest`'s own field, not `CompareRequest`'s `scope_public`.
        assert "scope_to_public_surface" in hops["contract.mode"]

    def test_every_remapped_spelling_is_a_real_scan_request_field(self) -> None:
        """The map is only correct while `ScanRequest` keeps those names, so a
        rename fails here rather than silently restoring a name nobody can
        replay."""
        import dataclasses

        from abicheck.cli_scan_receipt import SCAN_REQUEST_SPELLINGS
        from abicheck.service_scan import ScanRequest

        fields = {f.name for f in dataclasses.fields(ScanRequest)}
        assert set(SCAN_REQUEST_SPELLINGS.values()) <= fields

    def test_a_request_with_every_statable_input_names_only_real_fields(
        self, mixed_pair: tuple[Path, Path]
    ) -> None:
        """The narrow case passed even while the receipt named
        `CompareRequest`'s fields, because those inputs were unset. This
        states all of them."""
        from abicheck.compatibility_evaluation_frontend import unstatable_selectors
        from abicheck.policy_file import PolicyFile
        from abicheck.service_scan import ScanRequest, _scan_request_config

        old, new = mixed_pair
        config = _scan_request_config(
            ScanRequest(
                binaries=[new],
                baseline=old,
                contract_evaluation=True,
                policy="sdk_vendor",
                policy_file=PolicyFile(base_policy="plugin_abi"),
                scope_to_public_surface=False,
                force_public_symbols={"kept"},
            )
        )
        assert unstatable_selectors(config, request_type=ScanRequest) == []

    def test_the_api_receipt_records_the_api_request_layer(
        self, mixed_pair: tuple[Path, Path]
    ) -> None:
        """`contract.mode`, not `policy.base`: `--policy`/`policy` is D7's
        legacy alias for the base and resolves at `legacy_alias` on every
        front end, so it says nothing about which one stated it."""
        from abicheck.compatibility_evaluation_config import SelectorLayer
        from abicheck.service_scan import ScanRequest, _scan_request_config

        old, new = mixed_pair
        config = _scan_request_config(
            ScanRequest(
                binaries=[new],
                baseline=old,
                contract_evaluation=True,
                contract_mode="exports",
            )
        )
        prov = config.provenance["contract.mode"]
        assert prov.layer is SelectorLayer.API_REQUEST
        assert [hop.option for hop in prov.selected_by] == ["contract_mode"]

    def test_the_cli_scan_still_names_its_own_flags(
        self, mixed_pair: tuple[Path, Path]
    ) -> None:
        """The default front end is unchanged: only `run_scan` opts into API
        spellings, so the CLI receipt still names a replayable command."""
        from abicheck.cli_scan_receipt import SCAN_CONFIG_PARAMS, resolve_scan_config

        params = dict.fromkeys(SCAN_CONFIG_PARAMS)
        params["policy"] = "strict_abi"
        params["public_symbols"] = ()
        config = resolve_scan_config(params, typed={"policy"})
        hops = [hop.option for hop in config.provenance["policy.base"].selected_by]
        assert "--policy" in hops


class TestApiRequestValidationErrors:
    """A request the resolver rejects must reach the caller as
    `ValidationError`, which is what every other request-validation failure
    in `run_scan` raises — not as the resolver's own `ValueError`, which a
    caller guarding with `except ValidationError` would not catch
    (CodeRabbit review).
    """

    def _request(self, mixed_pair: tuple[Path, Path], **kwargs):
        from abicheck.service_scan import ScanRequest

        old, new = mixed_pair
        return ScanRequest(
            binaries=[new], baseline=old, contract_evaluation=True, **kwargs
        )

    def test_an_unknown_policy_is_a_validation_error(
        self, mixed_pair: tuple[Path, Path]
    ) -> None:
        """Unreachable from the CLI, whose `--policy` is a `click.Choice`, but
        a typed request's `policy` is a free string."""
        from abicheck.errors import ValidationError
        from abicheck.service_scan import _scan_request_config

        with pytest.raises(ValidationError, match="unknown base policy"):
            _scan_request_config(self._request(mixed_pair, policy="not_a_policy"))

    def test_run_scan_surfaces_it_rather_than_the_raw_resolver_error(
        self, mixed_pair: tuple[Path, Path]
    ) -> None:
        """End to end: the mapping has to hold through `run_scan`, since that
        is the entry point a caller actually guards."""
        from abicheck.errors import ValidationError
        from abicheck.service_scan import run_scan

        with pytest.raises(ValidationError, match="unknown base policy"):
            run_scan(self._request(mixed_pair, policy="not_a_policy"))

    def test_a_valid_request_still_resolves(
        self, mixed_pair: tuple[Path, Path]
    ) -> None:
        """The mapping must not swallow a good request: `ValueError` is a wide
        net, so this pins that the happy path still returns a config."""
        from abicheck.service_scan import _scan_request_config

        assert _scan_request_config(self._request(mixed_pair)) is not None


class TestAnAcceptedRequestIsNotKilledByItsOwnReceipt:
    """Two inputs the comparison accepts must not fail during resolution.

    Both were already fixed once on the MCP path and reappeared here, which
    is why the fixes now live on shared helpers
    (`stated_policy_base`, `SuppressionSource.from_loaded`) rather than in
    each front end (Codex review).
    """

    def _config(self, mixed_pair: tuple[Path, Path], **kwargs):
        from abicheck.service_scan import ScanRequest, _scan_request_config

        old, new = mixed_pair
        return _scan_request_config(
            ScanRequest(
                binaries=[new], baseline=old, contract_evaluation=True, **kwargs
            )
        )

    def test_a_policy_file_overrides_an_unknown_policy_name(
        self, mixed_pair: tuple[Path, Path]
    ) -> None:
        """The file takes precedence, so the name chose nothing and naming it
        would be false either way -- it is dropped, not repaired."""
        from abicheck.policy_file import PolicyFile

        config = self._config(
            mixed_pair,
            policy="not_a_policy",
            policy_file=PolicyFile(base_policy="sdk_vendor"),
        )
        assert config.policy.base.id == "sdk_vendor"

    def test_an_unknown_policy_alone_still_fails_loudly(
        self, mixed_pair: tuple[Path, Path]
    ) -> None:
        """The drop is gated on a file overriding it; with no file the value
        really was the caller's choice and really is invalid."""
        from abicheck.errors import ValidationError

        with pytest.raises(ValidationError, match="unknown base policy"):
            self._config(mixed_pair, policy="not_a_policy")

    def test_an_in_memory_suppression_list_resolves(
        self, mixed_pair: tuple[Path, Path]
    ) -> None:
        """A programmatically built list carries no `source_sha256`; taking
        `""` there made `SuppressionConfig` reject an otherwise-valid scan."""
        from abicheck.suppression import SuppressionList

        config = self._config(mixed_pair, suppression=SuppressionList([]))
        assert config.suppressions is not None
        assert config.suppressions.sha256
