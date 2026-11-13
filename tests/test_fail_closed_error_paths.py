# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Three error paths that used to fail open (quality audit at 509cb0d).

Each test fails on the pre-fix behaviour:

* R1 -- a release whose bundle snapshot could not be built returned no bundle
  result at all, so cross-library analysis vanished from the report with
  only a stderr warning. It now records a failed bundle axis in
  ``BundleDiffResult.analysis_errors``, which the JSON and Markdown release
  reports carry.
* R2 -- ``legacy_exit_code`` defaulted an unmapped verdict to ``0`` (a clean
  pass). It is now total over ``Verdict`` and raises on anything else.
* R3 -- ``read_embedded_manifest`` read a corrupt ``ProjectSnapshot``
  ``manifest.json`` as "no manifest", and ``extract_pdb_debug_outcome`` read a
  failed PDB lookup as "no PDB". Both now surface the failure.

Bug class: ``evidence.silent_degradation_to_clean_verdict``.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

import abicheck.bundle as bundle_mod
from abicheck.checker_policy import Verdict
from abicheck.checker_types import DiffResult
from abicheck.errors import ReleaseOperandUsageError, SnapshotError
from abicheck.frontends.cli.release_report import render_release_summary
from abicheck.policy.severity import _LEGACY_VERDICT_EXIT_CODE, legacy_exit_code
from abicheck.report.render_release_markdown import _release_md_bundle_findings
from abicheck.workflows.dump import pe as pe_mod
from abicheck.workflows.release_bundle import (
    collect_bundle_result,
    resolve_bundle_manifest,
    run_bundle_analysis,
)
from abicheck.workflows.release_package import read_embedded_manifest

# ── R1: a failed bundle snapshot build is a recorded failed axis ────────────

_BUILD_ERRORS = (
    ValueError("malformed member snapshot"),
    OSError("permission denied"),
    KeyError("objects/ab/cdef"),
    RuntimeError("unexpected"),
)


def _diff(library: str, verdict: Verdict) -> DiffResult:
    return DiffResult(
        old_version="old", new_version="new", library=library, verdict=verdict
    )


def _failing_build(fail_side: str, exc: Exception):
    calls = {"n": 0}

    def build(libs, *args, **kwargs):
        calls["n"] += 1
        side = "old" if calls["n"] == 1 else "new"
        if fail_side in (side, "both"):
            raise exc
        return bundle_mod.BundleSnapshot(
            root=Path("/fake"),
            libraries={},
            metadata={},
            resolution=bundle_mod._compute_resolution_graph({}, {}),
        )

    return build


@pytest.mark.parametrize("exc", _BUILD_ERRORS, ids=lambda e: type(e).__name__)
@pytest.mark.parametrize("fail_side", ["old", "new", "both"])
@pytest.mark.parametrize("verdict", [Verdict.NO_CHANGE, Verdict.BREAKING])
def test_failed_bundle_snapshot_build_is_recorded_not_dropped(
    monkeypatch, exc, fail_side, verdict
) -> None:
    monkeypatch.setattr(
        bundle_mod, "build_bundle_snapshot", _failing_build(fail_side, exc)
    )
    per_lib = [_diff("libcore.so", verdict), _diff("libplugin.so", Verdict.NO_CHANGE)]
    notices: list[str] = []
    members = {"libcore.so": Path("libcore.so"), "libplugin.so": Path("libplugin.so")}

    result = run_bundle_analysis(
        members,
        dict(members),
        per_lib,
        manifest_path=None,
        bundle_system_providers=(),
        policy="sdk_vendor",
        notice=notices.append,
    )

    # Pre-fix: `result is None` -- the bundle axis was absent, not failed.
    assert result is not None
    assert result.bundle_findings == []
    assert len(result.analysis_errors) == 1
    assert result.analysis_errors[0].startswith("bundle snapshot could not be built")
    assert str(exc) in result.analysis_errors[0]
    # The per-library results and the policy survive the failed stage.
    assert [r.library for r in result.per_library] == ["libcore.so", "libplugin.so"]
    assert result.per_library_verdict is verdict
    assert result.policy == "sdk_vendor"
    assert notices == [f"Warning: {result.analysis_errors[0]}"]


def test_failed_bundle_axis_reaches_both_release_report_formats(monkeypatch) -> None:
    monkeypatch.setattr(
        bundle_mod,
        "build_bundle_snapshot",
        _failing_build("new", ValueError("truncated sub-package")),
    )
    members = {"libcore.so": Path("libcore.so")}
    library_results = [
        {
            "library": "libcore.so",
            "_diff_result": _diff("libcore.so", Verdict.NO_CHANGE),
        }
    ]

    result, worst = collect_bundle_result(
        library_results,
        members,
        dict(members),
        manifest_path=None,
        bundle_system_providers=(),
        bundle_cohorts=(),
        policy="strict_abi",
        policy_file=None,
        worst_verdict="NO_CHANGE",
    )
    assert result is not None
    assert worst == "NO_CHANGE"

    payload = json.loads(
        render_release_summary(
            "json",
            worst,
            Path("/old"),
            Path("/new"),
            [],
            [],
            [],
            {},
            {},
            [],
            bundle_result=result,
            matrix_result=None,
        )
    )
    assert payload["bundle_analysis_errors"] == [
        "bundle snapshot could not be built: truncated sub-package"
    ]
    markdown = "\n".join(_release_md_bundle_findings(result, []))
    assert "Bundle Analysis Warnings" in markdown
    assert "truncated sub-package" in markdown


def test_nothing_to_analyze_still_returns_none() -> None:
    # The one legitimate "absent" case: no members and no manifest.
    assert (
        run_bundle_analysis({}, {}, [], manifest_path=None, bundle_system_providers=())
        is None
    )


# ── R2: the legacy verdict→exit map is total and fails closed ────────────────


def test_legacy_exit_map_covers_every_verdict() -> None:
    assert set(_LEGACY_VERDICT_EXIT_CODE) == set(Verdict)


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        (Verdict.BREAKING, 4),
        (Verdict.API_BREAK, 2),
        (Verdict.COMPATIBLE_WITH_RISK, 0),
        (Verdict.COMPATIBLE, 0),
        (Verdict.NO_CHANGE, 0),
    ],
)
def test_legacy_exit_code_matches_documented_contract(verdict, expected) -> None:
    # Oracle: AGENTS.md "Exit codes" -- 4 ABI break, 2 source break, else 0.
    assert legacy_exit_code(verdict) == expected


@pytest.mark.parametrize("bogus", ["ERROR", "UNCONFIRMED", "", None, 3])
def test_unmapped_verdict_raises_instead_of_exit_zero(bogus) -> None:
    with pytest.raises(ValueError, match="no legacy exit code"):
        legacy_exit_code(bogus)  # type: ignore[arg-type]


# ── R3a: a corrupt ProjectSnapshot manifest is not "no manifest" ────────────

_CORRUPT_MANIFESTS = {
    "truncated_json": b'{"versions": {"package_format_version": 2',
    "not_an_object": b"[1, 2, 3]",
    "binary_garbage": b"\x00\xff\xfe garbage",
    "unreadable_versions": json.dumps(
        {"versions": {"package_format_version": 999}, "variant_ids": []}
    ).encode(),
    "gzip_truncated": gzip.compress(b'{"versions": {}, "variant_ids": [')[:-6],
}


@pytest.mark.parametrize("raw", _CORRUPT_MANIFESTS.values(), ids=_CORRUPT_MANIFESTS)
def test_corrupt_package_manifest_raises(tmp_path: Path, raw: bytes) -> None:
    (tmp_path / "manifest.json").write_bytes(raw)
    with pytest.raises((ValueError, OSError, SnapshotError)):
        read_embedded_manifest(tmp_path)


@pytest.mark.parametrize("raw", _CORRUPT_MANIFESTS.values(), ids=_CORRUPT_MANIFESTS)
def test_corrupt_package_manifest_is_a_release_usage_error(
    tmp_path: Path, raw: bytes
) -> None:
    old_root = tmp_path / "old"
    old_root.mkdir()
    (old_root / "manifest.json").write_bytes(raw)
    with pytest.raises(ReleaseOperandUsageError, match="could not be decoded"):
        resolve_bundle_manifest(None, old_root, None, {}, {})


def test_absent_manifest_reads_as_none(tmp_path: Path) -> None:
    (tmp_path / "libfoo.so").write_bytes(b"\x7fELF")
    assert read_embedded_manifest(tmp_path) is None


def test_foreign_manifest_reads_as_none(tmp_path: Path) -> None:
    # A BuildSourcePack writes its own `manifest.json` at its root.
    (tmp_path / "manifest.json").write_text(
        json.dumps({"pack_version": 1, "layers": {}}), encoding="utf-8"
    )
    assert read_embedded_manifest(tmp_path) is None


# ── R3b: a failed PDB lookup is a failed debug section, not "no PDB" ─────────


@pytest.mark.parametrize(
    "exc",
    [OSError("access denied"), ValueError("bad debug dir"), RuntimeError("bug")],
    ids=lambda e: type(e).__name__,
)
def test_pdb_lookup_failure_is_reported_as_failure(
    monkeypatch, tmp_path: Path, exc: Exception
) -> None:
    import abicheck.pdb_utils as pu

    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(pu, "locate_pdb", boom)
    meta, adv, failure = pe_mod.extract_pdb_debug_outcome(tmp_path / "x.dll", None)
    assert (meta, adv) == (None, None)
    assert failure == f"pdb lookup: x.dll: {exc}"


def test_no_pdb_found_is_not_a_failure(monkeypatch, tmp_path: Path) -> None:
    import abicheck.pdb_utils as pu

    monkeypatch.setattr(pu, "locate_pdb", lambda *a, **k: None)
    assert pe_mod.extract_pdb_debug_outcome(tmp_path / "x.dll", None) == (
        None,
        None,
        None,
    )


def test_pdb_lookup_failure_marks_debug_section_failed(
    monkeypatch, tmp_path: Path
) -> None:
    import abicheck.pdb_utils as pu
    from abicheck.model.pe_facts import PeExport, PeMetadata
    from abicheck.serialization import snapshot_to_json

    p = tmp_path / "lib.dll"
    p.write_bytes(b"MZ" + b"\x00" * 100)
    pe_meta = PeMetadata(machine="AMD64", exports=[PeExport(name="Func", ordinal=1)])
    monkeypatch.setattr(
        "abicheck.pe_metadata.parse_pe_metadata", lambda *a, **k: pe_meta
    )

    def boom(*a, **k):
        raise OSError("access denied")

    monkeypatch.setattr(pu, "locate_pdb", boom)
    snap = pe_mod.extract_pe(p, "1.0")
    # Oracle: the stored document's D15 header entry, not a live helper.
    document = json.loads(snapshot_to_json(snap))
    assert "debug" not in document["sections"]
    assert document["section_availability"]["debug"] == {
        "diagnostics": ["pdb lookup: lib.dll: access denied"],
        "status": "failed",
    }


@pytest.mark.known_gap_canary
def test_failed_bundle_axis_does_not_floor_the_release_exit_code(monkeypatch) -> None:
    """Canary for known-gaps.md "A failed bundle snapshot build is reported
    but does not floor the exit code": the release exit ladder has no input
    for a failed bundle axis, so an otherwise clean release exits 0 even
    though its report records the failure. Raising it needs an ADR (ADR-071
    rules out folding it into the per-pair assurance axis)."""
    from abicheck.policy.release_exit_decision import (
        resolve_release_exit_decision_for_report,
    )

    monkeypatch.setattr(
        bundle_mod, "build_bundle_snapshot", _failing_build("both", OSError("eio"))
    )
    members = {"libcore.so": Path("libcore.so")}
    library_results: list[dict[str, object]] = [
        {
            "library": "libcore.so",
            "verdict": "NO_CHANGE",
            "_diff_result": _diff("libcore.so", Verdict.NO_CHANGE),
        }
    ]
    result, worst = collect_bundle_result(
        library_results,
        members,
        dict(members),
        manifest_path=None,
        bundle_system_providers=(),
        worst_verdict="NO_CHANGE",
    )
    assert result is not None and result.analysis_errors
    decision = resolve_release_exit_decision_for_report(
        worst, False, [], None, 0, library_results, require_complete_analysis=True
    )
    assert decision.code == 0
