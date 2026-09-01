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


def test_run_scan_set_rejects_bazel_scoping_mismatch_before_discovery(
    monkeypatch, tmp_path: Path
) -> None:
    """Codex review, fresh evidence: a direct ``run_scan_set()`` caller has no
    ``cli_scan.py`` pre-flight of its own, and each member's own
    ``run_scan_core()`` check only fires after ``discover_artifact_set()``/
    ``check_artifact_set_soname_collisions()``/
    ``artifact_set_member_exports()`` have already run for every member.
    Pinned by asserting discovery never even starts. (A later round found
    `--artifact-set`'s own CLI path had the identical gap -- see
    ``test_scan_cli_artifact_set_rejects_bazel_scoping_mismatch_before_discovery``.)"""
    import abicheck.bundle as bundle_mod
    from abicheck.errors import PlanningError

    def _fail_if_called(*_a, **_kw):
        raise AssertionError(
            "discover_artifact_set() ran before the Bazel-scoping pre-flight "
            "check rejected the request"
        )

    monkeypatch.setattr(bundle_mod, "discover_artifact_set", _fail_if_called)

    aquery = _write_bazel_aquery(tmp_path)
    req = ScanRequest(
        binaries=[_artifact(tmp_path, "a"), _artifact(tmp_path, "b")],
        sources=_sources(tmp_path),
        build_info=aquery,
        build_targets=("//:math",),
    )
    with pytest.raises(PlanningError, match="pre-captured Bazel aquery"):
        run_scan_set(req)


def test_scan_cli_artifact_set_rejects_bazel_scoping_mismatch_before_discovery(
    monkeypatch, tmp_path: Path
) -> None:
    """Codex review, fresh evidence: the `--artifact-set` CLI's own pre-flight
    check (`cli_scan._run_artifact_set`) previously ran only *after*
    `_resolve_artifact_set_paths()`/`discover_artifact_set()` -- so a
    directory got traversed and every explicit member statted/format-
    validated before an unsupported request was ultimately rejected anyway,
    and an invalid member's own error could mask the intended usage error.
    Pinned by asserting discovery never even starts, the CLI-level sibling of
    `test_run_scan_set_rejects_bazel_scoping_mismatch_before_discovery`
    above."""
    import abicheck.bundle as bundle_mod

    def _fail_if_called(*_a, **_kw):
        raise AssertionError(
            "discover_artifact_set() ran before the Bazel-scoping pre-flight "
            "check rejected the request"
        )

    monkeypatch.setattr(bundle_mod, "discover_artifact_set", _fail_if_called)

    aquery = _write_bazel_aquery(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "scan",
            "--artifact-set",
            str(_artifact(tmp_path, "a")),
            "--artifact-set",
            str(_artifact(tmp_path, "b")),
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


def test_run_scan_set_depth_binary_exempts_the_early_bazel_scoping_check(
    monkeypatch, tmp_path: Path
) -> None:
    """The sibling of ``test_run_scan_depth_binary_exempts_the_early_bazel_
    scoping_check`` for the plural entry point: ``run_scan_set``'s own
    pre-flight check (see the test above) must not reintroduce the
    depth=binary false positive for a typed ``--artifact-set`` caller
    either."""
    a = _artifact(tmp_path, "a")
    b = _artifact(tmp_path, "b")
    _bypass_discovery_validation(monkeypatch, a, b)

    aquery = _write_bazel_aquery(tmp_path)
    req = ScanRequest(
        binaries=[a, b],
        sources=_sources(tmp_path),
        build_info=aquery,
        build_targets=("//:math",),
        depth="binary",
    )
    # Not, in particular, PlanningError -- depth=binary never consults
    # build_info/build_targets, so the pre-flight check must exempt it.
    result = run_scan_set(req)
    assert result.verdict not in ("BUDGET_OVERFLOW", "EVIDENCE_CONTRACT_ERROR")


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


def test_scan_cli_headerless_depth_headers_exempts_the_bazel_scoping_check(
    monkeypatch, tmp_path: Path
) -> None:
    """Codex review, fresh evidence (ADR-063 Phase 4's second slice): the
    single-binary `scan` pre-flight check was routed through the bare
    `bazel_target_scoping_failure`, not the scan-aware
    `scan_bazel_scoping_failure` -- so it carried none of the
    headers/collect-mode exemption the latter applies. `--depth headers`
    with no `-H` inputs resolves to collect_mode "off": neither
    `embed_build_source` nor the L2 seed ever consult `build_info` at that
    combination, so an explicit `--build-target` + pre-captured Bazel
    jsonproto must be exempt here too, matching what `run_scan_core`'s own
    (already scan-aware) check downstream would accept -- not rejected by
    this earlier, less-aware CLI pre-flight before it ever gets there."""
    monkeypatch.setattr(embed_mod, "embed_build_source", lambda *a, **kw: None)

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
            "--depth",
            "headers",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "pre-captured Bazel aquery" not in result.output


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


def test_scan_cli_artifact_set_unset_depth_low_risk_seed_config_scope_is_unaffected(
    monkeypatch, tmp_path: Path
) -> None:
    """Codex review, fresh evidence, final round: an unset ``--depth`` on
    ``--artifact-set`` must not be *approximated* -- it must be *resolved*,
    the same way ``run_scan_set``/``estimate_artifact_set`` do
    (``_resolve_member_scan_level``, shared per ``workflows/AGENTS.md``'s
    "dry-run and execution must consume the same resolved plan" rule). An
    earlier round of this fix withheld the config-sourced fallback entirely
    whenever depth was unset -- which over-corrected: it silently accepted a
    *high-risk* seed too, one that resolves to a real, non-"off" collect_mode
    the real run would still reject on (see the sibling
    ``..._high_risk_seed_config_scope_still_rejects`` test below). This
    request seeds a low-risk (docs-only) change, which `--source-method auto`
    resolves to S0/collect_mode "off" -- genuinely exempt, not just assumed
    to be."""
    aquery = _write_bazel_aquery(tmp_path)
    a = _artifact(tmp_path, "a")
    b = _artifact(tmp_path, "b")
    _bypass_discovery_validation(monkeypatch, a, b)

    src = _sources(tmp_path)
    (src / ".abicheck.yml").write_text(
        "build:\n  system: bazel\n  targets:\n    - //:math\n", encoding="utf-8"
    )

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
            str(src),
            "--build-info",
            str(aquery),
            "--changed-path",
            "docs/notes.md",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "pre-captured Bazel aquery" not in result.output


def test_scan_cli_artifact_set_high_risk_seed_config_scope_still_rejects(
    monkeypatch, tmp_path: Path
) -> None:
    """Codex review, fresh evidence, final round: the false-negative
    counterpart to the low-risk test above. A seeded *high-risk* change
    (a public-header edit) resolves `--source-method auto` to S5/
    ``source-changed`` -- a real, active collect_mode, not "off" -- so the
    config-sourced-only scope mismatch must still be rejected here, matching
    what ``run_scan_set``'s own per-member ``scan_bazel_scoping_failure``
    call would reject downstream too. An earlier round of this fix withheld
    the config-sourced fallback unconditionally whenever ``--depth`` was
    unset, which silently accepted this exact case in `--dry-run` (a real,
    user-visible dry-run/execution parity gap -- the very defect class this
    whole PR exists to close) rather than resolving the real level."""
    aquery = _write_bazel_aquery(tmp_path)
    a = _artifact(tmp_path, "a")
    b = _artifact(tmp_path, "b")
    _bypass_discovery_validation(monkeypatch, a, b)

    src = _sources(tmp_path)
    (src / ".abicheck.yml").write_text(
        "build:\n  system: bazel\n  targets:\n    - //:math\n", encoding="utf-8"
    )

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
            str(src),
            "--build-info",
            str(aquery),
            "--changed-path",
            "include/api.h",
        ],
    )
    assert result.exit_code == 64, result.output
    assert "pre-captured Bazel aquery" in result.output
    assert "//:math" in result.output


def test_scan_cli_artifact_set_depth_binary_exempts_the_bazel_scoping_check(
    monkeypatch, tmp_path: Path
) -> None:
    """Codex/CodeRabbit review, fresh evidence: the `--artifact-set` path's
    own pre-flight check (mirroring the single-binary path's own) had no
    depth=binary exemption at all, unlike the single-binary path (whose
    `_normalize_depth_inputs` prunes `build_info` to `None` at that depth
    before this same check runs) -- so a valid `--depth binary` request was
    wrongly rejected. `--depth binary` resolves to a collect_mode that never
    consults build_info/build_targets at all."""
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
            "--depth",
            "binary",
            "--sources",
            str(_sources(tmp_path)),
            "--build-info",
            str(aquery),
            "--build-target",
            "//:math",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "pre-captured Bazel aquery" not in result.output


def test_scan_cli_artifact_set_headerless_depth_headers_exempts_the_bazel_scoping_check(
    monkeypatch, tmp_path: Path
) -> None:
    """Codex review, fresh evidence (ADR-063 Phase 4's second slice): the
    `--artifact-set` path's own pre-flight check only ever special-cased
    `--depth binary` textually -- it carried none of the headers/collect-
    mode exemption `scan_bazel_scoping_failure` applies elsewhere, so a
    headerless `--depth headers` set (collect_mode "off", neither
    embed_build_source nor the L2 seed ever consulting build_info) with an
    explicit `--build-target` was wrongly rejected here, before the
    scan-aware guards downstream that would have accepted it."""
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
            "--depth",
            "headers",
            "--sources",
            str(_sources(tmp_path)),
            "--build-info",
            str(aquery),
            "--build-target",
            "//:math",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "pre-captured Bazel aquery" not in result.output


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


def test_run_scan_rejects_before_wasted_pattern_scan_and_poi_work(
    monkeypatch, tmp_path: Path
) -> None:
    """Codex review, fresh evidence: a typed ``run_scan(ScanRequest(...))``
    caller has no ``cli_scan.py`` pre-flight ahead of ``run_scan_core`` the
    way the CLI does, so the Bazel-scoping ``PlanningError`` must be raised
    by ``run_scan_core`` itself, before its S3 pattern scan/POI work runs --
    not only inside ``_build_new_snapshot``, which those two stages already
    precede. Pinned by asserting the S3 pass never even starts."""
    import abicheck.scan_engine as scan_engine_mod
    from abicheck.errors import PlanningError

    def _fail_if_called(*_a, **_kw):
        raise AssertionError(
            "scan_files() (S3 pattern scan) ran before the Bazel-scoping "
            "pre-flight check rejected the request"
        )

    monkeypatch.setattr(scan_engine_mod, "scan_files", _fail_if_called)

    aquery = _write_bazel_aquery(tmp_path)
    req = ScanRequest(
        binaries=[_artifact(tmp_path)],
        sources=_sources(tmp_path),
        build_info=aquery,
        build_targets=("//:math",),
    )
    with pytest.raises(PlanningError, match="pre-captured Bazel aquery"):
        run_scan(req)


def test_run_scan_depth_binary_exempts_the_early_bazel_scoping_check(
    monkeypatch, tmp_path: Path
) -> None:
    """The pre-flight check added to ``run_scan_core`` (see the test above)
    must not reintroduce the depth=binary false positive ``workflows.plan.
    _check_bazel_target_scoping`` already fixed: ``depth="binary"`` resolves
    to a ``collect_mode`` that never consults ``build_info``/``build_targets``
    at all, so the shared ``workflows.plan.scan_bazel_scoping_failure`` guard
    must exempt it before ever calling the inner ``bazel_target_scoping_
    failure``."""
    import abicheck.workflows.plan as plan_mod

    def _fail_if_called(*_a, **_kw):
        raise AssertionError(
            "bazel_target_scoping_failure() was called despite depth=binary "
            "resolving to a collect_mode that never consults build_info/"
            "build_targets"
        )

    monkeypatch.setattr(plan_mod, "bazel_target_scoping_failure", _fail_if_called)

    aquery = _write_bazel_aquery(tmp_path)
    req = ScanRequest(
        binaries=[_artifact(tmp_path)],
        sources=_sources(tmp_path),
        build_info=aquery,
        build_targets=("//:math",),
        depth="binary",
    )
    result = run_scan(req)
    assert result.verdict not in ("BUDGET_OVERFLOW", "EVIDENCE_CONTRACT_ERROR")


def test_run_scan_depth_headers_config_sourced_target_scope_raises_planning_error(
    tmp_path: Path,
) -> None:
    """Codex review, fresh evidence: unlike depth=binary, `--depth headers`
    keeps real headers, so a headers-only scan still runs the L2-seed's own
    independent `collect_inline_pack` call to derive include dirs/compile
    context, which *does* consult `build_info`/`build_targets`. Before the
    l2_seed.py fix (see test_bazel_root_targets_l2_seed.py), that mismatch's
    ValidationError was silently swallowed there and the scan returned a
    clean COMPATIBLE result (exit 0) -- now it fails loudly instead.

    This case is specifically the `.abicheck.yml`-sourced target scope (no
    explicit `build_targets` on the request) -- previously a structural gap
    `run_scan_core`'s own early pre-flight check could not see at all (it
    surfaced only later, leaked as `click.ClickException` from
    `scan_engine._build_new_snapshot`'s pre-existing `except AbicheckError`
    wart, per `docs/contribute/known-gaps.md`'s own account of that state).
    ADR-063 Phase 4's second slice closed it: `scan_bazel_scoping_failure`/
    `bazel_target_scoping_failure` now auto-discover an `.abicheck.yml` at
    `sources` (mirroring `embed_build_source`'s own `cfg.targets` fallback)
    when no explicit `build_targets` is given, so this now raises the
    framework-neutral `PlanningError` from `run_scan_core`'s own early
    pre-flight check -- before ever reaching the L2 seed, matching the
    sibling explicit-`build_targets` case below exactly."""
    from abicheck.errors import PlanningError

    aquery = _write_bazel_aquery(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    header = src / "api.h"
    header.write_text("void f();\n", encoding="utf-8")
    (src / ".abicheck.yml").write_text(
        "build:\n  system: bazel\n  targets:\n    - //:math\n", encoding="utf-8"
    )

    req = ScanRequest(
        binaries=[_artifact(tmp_path)],
        headers=[header],
        sources=src,
        build_info=aquery,
        depth="headers",
    )
    with pytest.raises(PlanningError, match="pre-captured Bazel aquery"):
        run_scan(req)


def test_run_scan_depth_headers_with_explicit_build_target_raises_planning_error(
    tmp_path: Path,
) -> None:
    """Codex review, fresh evidence: when `build_targets` is given explicitly
    on the request (not just via `.abicheck.yml`), `run_scan_core`'s own
    early pre-flight check *can* see it -- so this shape must raise the
    framework-neutral `PlanningError` before ever reaching
    `_build_new_snapshot`'s `except AbicheckError` translation, unlike the
    `.abicheck.yml`-only sibling test above. This is what closes the actual
    Codex finding: the original guard (`collection_for_ci_mode(collect_mode)
    [1]` alone) wrongly treated `--depth headers` the same as `--depth
    binary` -- both resolve to collect_mode "off", but only `--depth binary`
    also clears `headers` to `()`, which is the real reason
    `--depth binary` never consumes `build_info` anywhere. Widened to
    `headers or collection_for_ci_mode(...)[1]` so `--depth headers` (real
    headers, so the L2 seed can still consume build_info) is no longer
    wrongly exempted."""
    from abicheck.errors import PlanningError

    aquery = _write_bazel_aquery(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    header = src / "api.h"
    header.write_text("void f();\n", encoding="utf-8")

    req = ScanRequest(
        binaries=[_artifact(tmp_path)],
        headers=[header],
        sources=src,
        build_info=aquery,
        build_targets=("//:lib",),
        depth="headers",
    )
    with pytest.raises(PlanningError, match="pre-captured Bazel aquery"):
        run_scan(req)
