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
* The typed API -- ``EstimateOperand.build_targets`` reaching the same call,
  through both ``run_scan`` and ``run_scan_set`` (``--artifact-set``'s own
  Python-API entry point).
"""

from __future__ import annotations

from pathlib import Path

from scan_estimate_helpers import EstimateOperand, estimate

from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, AccessLevel, Function, ScopeOrigin, Visibility
from abicheck.serialization import snapshot_to_json


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


# ── `scan --dry-run` preview (Codex review) ────────────────────────────────


def test_dry_run_preview_mentions_requested_build_target_and_flags_estimate(
    tmp_path: Path,
) -> None:
    """Codex review: a --build-target dry-run silently omitted the requested
    root(s) from the preview, and the TU-count estimate looked scoped when
    it's actually a workspace-wide probe. Fixed by stating the requested
    target(s) and flagging the estimate as unscoped."""
    from abicheck.frontends.cli.scan_dry_run import render_scan_dry_run
    from abicheck.model.evidence_depth_levels import EvidenceDepth, SourceMethod

    estimates = estimate(
        EstimateOperand(
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
    from abicheck.frontends.cli.scan_dry_run import render_scan_dry_run
    from abicheck.model.evidence_depth_levels import EvidenceDepth, SourceMethod

    estimates = estimate(
        EstimateOperand(binaries=[tmp_path / "lib.so"], sources=tmp_path, mode="audit"),
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


# ── Typed API: estimate()'s TU-count rows flag UNSCOPED (Codex review) ──
#
# Fresh evidence beyond the CLI dry-run preview above: a Python caller can
# construct EstimateOperand(build_targets=...) directly and call estimate()
# (or read ScanResult.estimate off a real run_scan()) without ever going
# through cli_scan.py's own dry-run renderer -- which previously was the
# *only* place this workspace-wide-vs-scoped caveat was surfaced. Verifies
# the caveat now lives in the CostEstimate rows themselves, so every API
# caller sees it, not only the CLI's rendered text.


def _compile_db_request(tmp_path: Path, *, build_targets: tuple[str, ...] = ()):
    import json


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
    return EstimateOperand(
        binaries=[snap],
        compile_db=cdb,
        mode="baseline",
        build_targets=build_targets,
    )


def test_estimate_scan_flags_unscoped_l3_row_when_build_targets_set(
    tmp_path: Path,
) -> None:

    req = _compile_db_request(tmp_path, build_targets=("//:math",))
    l3 = next(e for e in estimate(req) if e.layer == "L3_build")
    assert "UNSCOPED" in l3.note


def test_estimate_scan_l3_row_unflagged_when_build_targets_unset(
    tmp_path: Path,
) -> None:

    req = _compile_db_request(tmp_path)
    l3 = next(e for e in estimate(req) if e.layer == "L3_build")
    assert "UNSCOPED" not in l3.note


def test_estimate_scan_flags_unscoped_l4_l5_rows_too(tmp_path: Path) -> None:
    # L4/L5 TU counts derive from the same unscoped total_tus the L3 row's own
    # note flags -- each carries its own short back-reference (Codex review).

    req = _compile_db_request(tmp_path, build_targets=("//:math",))
    estimates = estimate(req)
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
