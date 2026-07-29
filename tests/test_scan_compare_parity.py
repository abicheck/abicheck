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

Deliberately narrow: this covers the concrete case ADR-049 Phase 5 names
(a suppression rule silencing a hard breaking removal), not an exhaustive
matrix over every flag combination -- see the Phase 5 progress note in
`docs/contribute/plans/public-contract-default.md` for what's still open.
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


def test_scan_against_malformed_autodiscovered_config_is_usage_error(
    runner: CliRunner,
    old_snap: Path,
    new_snap_breaking: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same contract as the explicit --config case above, but for the
    # auto-discovered path (no --config/--sources given at all, so
    # cli_options.merge_compile_config's own --sources-scoped auto-discovery
    # never engages -- only cli_scan's own scope/suppression config
    # resolution reaches discover_project_config here).
    import abicheck.cli_helpers_compare as _cch

    bad_config = tmp_path / ".abicheck.yml"
    bad_config.write_text("scope: [unclosed\n  public: true", encoding="utf-8")
    monkeypatch.setattr(_cch, "discover_project_config", lambda start=None: bad_config)

    scan_res = runner.invoke(
        main, ["scan", str(new_snap_breaking), "--against", str(old_snap)]
    )
    assert scan_res.exit_code == 64, scan_res.output


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
