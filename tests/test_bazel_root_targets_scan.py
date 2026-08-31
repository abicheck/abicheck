# Copyright 2026 Nikolay Petrov
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

"""P0.2 Bazel root-target scoping -- ``scan`` side (lab report follow-up).

``tests/test_bazel_root_targets.py`` covers ``dump --build-target`` reaching
``embed_build_source``, but ``scan``'s own identical ``embed_build_source``
call (``scan_engine._build_new_snapshot``) never threaded ``build_targets``
through at all -- a `scan --build-target //:math --against
dump-produced-baseline.json` silently ran an UNSCOPED workspace-wide query
even when the `dump` baseline it's compared against was itself
target-scoped, capturing unrelated fixture/test targets and diverging from
the baseline's own evidence. Split into its own sibling file (not added to
``test_bazel_root_targets.py`` directly) purely to keep that file under the
AI-readiness soft-limit -- same ``_extra``-style convention used elsewhere
in this suite.

Covers, bottom-up:

* The CLI -- ``scan --build-target`` (single-binary and ``--artifact-set``)
  reaching ``embed_build_source`` via ``scan_engine._build_new_snapshot``.
* The typed API -- ``ScanRequest.build_targets`` reaching the same call,
  through both ``run_scan`` and ``run_scan_set`` (``--artifact-set``'s own
  Python-API entry point).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.buildsource import embed as embed_mod
from abicheck.cli import main
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, AccessLevel, Function, ScopeOrigin, Visibility
from abicheck.serialization import snapshot_to_json
from abicheck.service_scan import ScanRequest, run_scan, run_scan_set


def _bypass_discovery_validation(monkeypatch, *binaries: Path) -> None:
    """Patch discover_artifact_set() to a trivial passthrough -- avoids
    needing real ELF fixtures for tests exercising run_scan_set()'s
    per-member scanning, not the bundle-audit layer itself. Same pattern as
    test_scan_artifact_set.py's identical helper."""
    import abicheck.bundle as bundle_mod

    def _fake_discover(paths, *, explicit):
        return {p.name: p for p in binaries}

    monkeypatch.setattr(bundle_mod, "discover_artifact_set", _fake_discover)


def _elf(*names: str) -> ElfMetadata:
    return ElfMetadata(symbols=[ElfSymbol(name=n) for n in names])


def _func(name: str, mangled: str) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        visibility=Visibility.PUBLIC,
        access=AccessLevel.PUBLIC,
        origin=ScopeOrigin.PUBLIC_HEADER,
    )


def _write_snapshot(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _artifact(tmp_path: Path, name: str = "artifact") -> Path:
    snap = AbiSnapshot(
        library=f"lib{name}.so",
        version="1.0",
        from_headers=True,
        functions=[_func("foo", "_Z3foov")],
        elf=_elf("_Z3foov"),
    )
    return _write_snapshot(tmp_path / f"{name}.abi.json", snap)


def _sources(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.cpp").write_text("int f() { return 0; }\n", encoding="utf-8")
    return src


# ── CLI: `scan --build-target` (single-binary) ────────────────────────────


def test_scan_cli_build_target_flag_reaches_embed_build_source(
    monkeypatch, tmp_path: Path
):
    captured: dict = {}

    def _fake_embed(snap, build_info, sources, *, build_targets=(), **kwargs):
        captured["build_targets"] = build_targets

    monkeypatch.setattr(embed_mod, "embed_build_source", _fake_embed)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "scan",
            str(_artifact(tmp_path)),
            "--sources",
            str(_sources(tmp_path)),
            "--build-target",
            "//:math",
            "--build-target",
            "//:util",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["build_targets"] == ("//:math", "//:util")


def test_scan_cli_no_build_target_leaves_it_empty(monkeypatch, tmp_path: Path):
    captured: dict = {}

    def _fake_embed(snap, build_info, sources, *, build_targets=(), **kwargs):
        captured["build_targets"] = build_targets

    monkeypatch.setattr(embed_mod, "embed_build_source", _fake_embed)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["scan", str(_artifact(tmp_path)), "--sources", str(_sources(tmp_path))],
    )
    assert result.exit_code == 0, result.output
    assert captured["build_targets"] == ()


# ── CLI: `scan --artifact-set --build-target` ──────────────────────────────


def test_scan_cli_artifact_set_build_target_reaches_embed_build_source(
    monkeypatch, tmp_path: Path
):
    captured: dict = {}

    def _fake_embed(snap, build_info, sources, *, build_targets=(), **kwargs):
        captured.setdefault("build_targets", []).append(build_targets)

    monkeypatch.setattr(embed_mod, "embed_build_source", _fake_embed)

    a = _artifact(tmp_path, "a")
    b = _artifact(tmp_path, "b")
    _bypass_discovery_validation(monkeypatch, a, b)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "scan",
            "--artifact-set",
            str(a),
            "--artifact-set",
            str(b),
            "--sources",
            str(_sources(tmp_path)),
            "--build-target",
            "//:math",
        ],
    )
    # The snapshot-JSON members aren't real ELF, so the separate bundle-audit
    # pass (build_bundle_snapshot, unaffected by the discovery bypass above)
    # reads BUNDLE_INCOMPLETE -- exit 1, not 0 -- same as
    # test_scan_artifact_set.py's own use of this bypass; what this test
    # checks is that per-member scanning (which the bypass does cover)
    # still reached embed_build_source with build_targets.
    assert result.exit_code in (0, 1), result.output
    assert captured["build_targets"] == [("//:math",), ("//:math",)]


# ── Typed API: `ScanRequest.build_targets` ─────────────────────────────────


def test_scan_request_build_targets_reaches_embed_build_source_via_run_scan(
    monkeypatch, tmp_path: Path
):
    captured: dict = {}

    def _fake_embed(snap, build_info, sources, *, build_targets=(), **kwargs):
        captured["build_targets"] = build_targets

    monkeypatch.setattr(embed_mod, "embed_build_source", _fake_embed)

    req = ScanRequest(
        binaries=[_artifact(tmp_path)],
        sources=_sources(tmp_path),
        build_targets=("//:math",),
    )
    result = run_scan(req)
    assert result.verdict not in ("BUDGET_OVERFLOW", "EVIDENCE_CONTRACT_ERROR")
    assert captured["build_targets"] == ("//:math",)


def test_scan_request_build_targets_reaches_embed_build_source_via_run_scan_set(
    monkeypatch, tmp_path: Path
):
    captured: dict = {}

    def _fake_embed(snap, build_info, sources, *, build_targets=(), **kwargs):
        captured.setdefault("build_targets", []).append(build_targets)

    monkeypatch.setattr(embed_mod, "embed_build_source", _fake_embed)

    a = _artifact(tmp_path, "a")
    b = _artifact(tmp_path, "b")
    _bypass_discovery_validation(monkeypatch, a, b)

    req = ScanRequest(
        binaries=[a, b],
        sources=_sources(tmp_path),
        build_targets=("//:math",),
    )
    run_scan_set(req)
    assert captured["build_targets"] == [("//:math",), ("//:math",)]


def test_scan_request_default_build_targets_is_empty_tuple():
    """Additive default -- an existing ScanRequest() caller is unaffected."""
    assert ScanRequest().build_targets == ()


def test_scan_request_build_targets_is_keyword_only():
    """Codex review: ScanRequest is public API -- an ordinary positional
    insertion would silently rebind bundle_system_providers/changed_src/
    max_findings for any existing positional caller. build_targets must be
    unreachable positionally."""
    import dataclasses

    from abicheck.service_scan import ScanRequest as _SR

    f = next(
        fld for fld in dataclasses.fields(_SR) if fld.name == "build_targets"
    )
    assert f.kw_only is True


# ── `scan --dry-run` preview (Codex review) ────────────────────────────────


def test_dry_run_preview_mentions_requested_build_target_and_flags_estimate(
    tmp_path: Path,
) -> None:
    """Codex review: a --build-target dry-run silently omitted the requested
    root(s) from the preview, and the TU-count estimate looked scoped when
    it's actually a workspace-wide probe. Fixed by stating the requested
    target(s) and flagging the estimate as unscoped."""
    from abicheck.buildsource.scan_levels import EvidenceDepth, SourceMethod
    from abicheck.frontends.cli.scan_dry_run import render_scan_dry_run
    from abicheck.service_scan import ScanRequest, estimate_scan

    estimates = estimate_scan(
        ScanRequest(
            binaries=[tmp_path / "lib.so"],
            sources=tmp_path,
            build_targets=("//:math",),
            mode="audit",
        ),
        resolved_level=(SourceMethod.S0, EvidenceDepth.BINARY),
    )
    result = render_scan_dry_run(
        artifact=tmp_path / "lib.so",
        against=None,
        sources=tmp_path,
        effective_build_info=None,
        changed=[],
        changed_src="none",
        seeded=False,
        depth=None,
        eff_depth_enum=EvidenceDepth.BINARY,
        resolved=SourceMethod.S0,
        collect_mode="off",
        header_backend="auto",
        fmt="text",
        build_targets=("//:math",),
        estimates=estimates,
    )
    build_lines = " ".join(result.sections.get("Build/source inputs", []))
    assert "--build-target: //:math" in build_lines
    scope_lines = " ".join(result.sections.get("Resolved depth and source scope", []))
    assert "UNSCOPED" in scope_lines


def test_dry_run_preview_omits_build_target_note_when_unset(tmp_path: Path) -> None:
    from abicheck.buildsource.scan_levels import EvidenceDepth, SourceMethod
    from abicheck.frontends.cli.scan_dry_run import render_scan_dry_run
    from abicheck.service_scan import ScanRequest, estimate_scan

    estimates = estimate_scan(
        ScanRequest(binaries=[tmp_path / "lib.so"], sources=tmp_path, mode="audit"),
        resolved_level=(SourceMethod.S0, EvidenceDepth.BINARY),
    )
    result = render_scan_dry_run(
        artifact=tmp_path / "lib.so",
        against=None,
        sources=tmp_path,
        effective_build_info=None,
        changed=[],
        changed_src="none",
        seeded=False,
        depth=None,
        eff_depth_enum=EvidenceDepth.BINARY,
        resolved=SourceMethod.S0,
        collect_mode="off",
        header_backend="auto",
        fmt="text",
        estimates=estimates,
    )
    build_lines = " ".join(result.sections.get("Build/source inputs", []))
    assert "--build-target" not in build_lines
    scope_lines = " ".join(result.sections.get("Resolved depth and source scope", []))
    assert "UNSCOPED" not in scope_lines


# ── Typed API: estimate_scan()'s TU-count rows flag UNSCOPED (Codex review) ──
#
# Fresh evidence beyond the CLI dry-run preview above: a Python caller can
# construct ScanRequest(build_targets=...) directly and call estimate_scan()
# (or read ScanResult.estimate off a real run_scan()) without ever going
# through cli_scan.py's own dry-run renderer -- which previously was the
# *only* place this workspace-wide-vs-scoped caveat was surfaced. Verifies
# the caveat now lives in the CostEstimate rows themselves, so every API
# caller sees it, not only the CLI's rendered text.


def _compile_db_request(tmp_path: Path, *, build_targets: tuple[str, ...] = ()):
    import json

    from abicheck.service_scan import ScanRequest

    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [{"file": "a.cpp", "command": "c++ a.cpp", "directory": "."}]
        ),
        encoding="utf-8",
    )
    snap = tmp_path / "new.abi.json"
    from abicheck.model import AbiSnapshot
    from abicheck.serialization import snapshot_to_json

    snap.write_text(snapshot_to_json(AbiSnapshot(library="libfoo.so", version="1.0")), encoding="utf-8")
    return ScanRequest(
        binaries=[snap],
        compile_db=cdb,
        mode="baseline",
        build_targets=build_targets,
    )


def test_estimate_scan_flags_unscoped_l3_row_when_build_targets_set(
    tmp_path: Path,
) -> None:
    from abicheck.service_scan import estimate_scan

    req = _compile_db_request(tmp_path, build_targets=("//:math",))
    l3 = next(e for e in estimate_scan(req) if e.layer == "L3_build")
    assert "UNSCOPED" in l3.note


def test_estimate_scan_l3_row_unflagged_when_build_targets_unset(
    tmp_path: Path,
) -> None:
    from abicheck.service_scan import estimate_scan

    req = _compile_db_request(tmp_path)
    l3 = next(e for e in estimate_scan(req) if e.layer == "L3_build")
    assert "UNSCOPED" not in l3.note


def test_estimate_scan_flags_unscoped_l4_l5_rows_too(tmp_path: Path) -> None:
    # L4/L5 TU counts derive from the same unscoped total_tus the L3 row's own
    # note flags -- each carries its own short back-reference (Codex review).
    from abicheck.service_scan import estimate_scan

    req = _compile_db_request(tmp_path, build_targets=("//:math",))
    estimates = estimate_scan(req)
    l4 = next(e for e in estimates if e.layer == "L4_source_abi")
    l5_rows = [e for e in estimates if e.layer == "L5_source_graph"]
    assert "UNSCOPED" in l4.note
    assert l5_rows and all("UNSCOPED" in e.note for e in l5_rows)


# ── ADR-063 Phase 4: `--build-target` + pre-captured Bazel jsonproto ───────
#
# docs/contribute/known-gaps.md's named gap, closed via
# workflows.plan.bazel_target_scoping_failure: previously the combination
# silently ran an unscoped workspace-wide query with no diagnostic at all.
# Codex review (fresh evidence) found the real-execution check
# (scan_engine._build_new_snapshot) never ran on either dry-run path, so a
# `--dry-run` preview claimed success (and, for the single-binary path, an
# "UNSCOPED" estimate that reads as informational rather than rejected) for
# a request the real run would then reject -- fixed by running the identical
# check in cli_scan.py before either dry-run renderer, not only at execution.


def _write_bazel_aquery(tmp_path: Path) -> Path:
    import json

    path = tmp_path / "aquery.json"
    path.write_text(
        json.dumps({"actions": [], "pathFragments": [], "artifacts": [], "targets": []})
    )
    return path


def test_scan_cli_dry_run_rejects_build_target_with_precaptured_aquery(
    tmp_path: Path,
) -> None:
    aquery = _write_bazel_aquery(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "scan",
            str(_artifact(tmp_path)),
            "--dry-run",
            "--sources",
            str(_sources(tmp_path)),
            "--build-info",
            str(aquery),
            "--build-target",
            "//:math",
        ],
    )
    assert result.exit_code == 64, result.output
    assert "pre-captured Bazel aquery" in result.output


def test_scan_cli_real_run_rejects_the_identical_combination(tmp_path: Path) -> None:
    """The non-dry-run invocation of the exact same request must reject
    identically -- pins that the dry-run fix above did not accidentally
    make the two paths disagree."""
    aquery = _write_bazel_aquery(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "scan",
            str(_artifact(tmp_path)),
            "--sources",
            str(_sources(tmp_path)),
            "--build-info",
            str(aquery),
            "--build-target",
            "//:math",
        ],
    )
    assert result.exit_code == 64, result.output
    assert "pre-captured Bazel aquery" in result.output


def test_scan_cli_artifact_set_dry_run_rejects_build_target_with_precaptured_aquery(
    monkeypatch, tmp_path: Path
) -> None:
    aquery = _write_bazel_aquery(tmp_path)
    a = _artifact(tmp_path, "a")
    b = _artifact(tmp_path, "b")
    _bypass_discovery_validation(monkeypatch, a, b)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "scan",
            "--artifact-set",
            str(a),
            "--artifact-set",
            str(b),
            "--dry-run",
            "--sources",
            str(_sources(tmp_path)),
            "--build-info",
            str(aquery),
            "--build-target",
            "//:math",
        ],
    )
    assert result.exit_code == 64, result.output
    assert "pre-captured Bazel aquery" in result.output


def test_run_scan_typed_api_raises_planning_error_not_click_usage_error(
    tmp_path: Path,
) -> None:
    """Codex review: ``scan_engine._build_new_snapshot`` backs the typed
    ``run_scan(ScanRequest(...))`` API too, which has no Click context to
    catch a ``click.UsageError`` -- a caller importing abicheck as a library
    must see the framework-neutral ``PlanningError`` instead. Only
    ``cli_scan.py``'s own ``scan_cmd`` (see
    ``test_scan_cli_real_run_rejects_the_identical_combination`` above)
    translates it to a usage error at the actual CLI boundary."""
    import click

    from abicheck.errors import PlanningError

    aquery = _write_bazel_aquery(tmp_path)
    req = ScanRequest(
        binaries=[_artifact(tmp_path)],
        sources=_sources(tmp_path),
        build_info=aquery,
        build_targets=("//:math",),
    )
    with pytest.raises(PlanningError, match="pre-captured Bazel aquery"):
        run_scan(req)
    # Not, in particular, a click.UsageError -- PlanningError is a plain
    # ValueError subclass with no Click dependency.
    assert not issubclass(PlanningError, click.UsageError)
