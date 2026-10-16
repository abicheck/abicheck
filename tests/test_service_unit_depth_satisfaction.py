"""Depth-shortfall contract for ``compare`` at the typed-API boundary.

Moved out of ``tests/test_service_unit.py`` to keep that file under its
architecture/debt.yaml no-growth baseline. Test names are unchanged.
"""

from __future__ import annotations

from abicheck.checker_types import DiffResult
from abicheck.service import CompareRequest, InputSpec, run_compare_request


class TestCompareRequestDepthSatisfaction:
    """`compare`'s depth-shortfall contract at the typed-API boundary.

    Originally (Codex review, P1) this pinned a hard `ValidationError` from
    `run_compare_request`, mirroring `dump`'s own
    `check_requested_depth_satisfied`. **PR #1195 changed that contract**:
    a `compare` shortfall is ADR-064's exit-7 axis, *recorded* on the result
    (`policy.depth_evidence_contract`), never raised -- because the raise
    lived on only one of `compare`'s resolution paths and made the same
    comparison exit differently depending on which front end reached it.

    A pre-serialized snapshot side is carved out entirely (this run never
    extracted it), and only `build`/`source` can be outrun at all -- the
    liveness half is pinned across every side/liveness combination by
    `tests/test_depth_evidence_contract_liveness.py`. Still monkeypatches
    `gated_source_label` to simulate reached vs. not-reached without a real
    compile database or source tree.
    """

    def _make_snap_file(self, tmp_path, name, version):
        from abicheck.model import AbiSnapshot
        from abicheck.serialization import save_snapshot

        path = tmp_path / f"{name}_{version}.json"
        save_snapshot(AbiSnapshot(library=name, version=version), path)
        return path

    def test_depth_not_reached_is_recorded_not_raised(self, tmp_path, monkeypatch):
        """The behaviour change itself: no `ValidationError` any more.

        Both operands here are stored snapshots, so this also pins the
        live-only carve-out -- the axis is not merely unraised, it is
        correctly *not set*, because neither side was extracted by this run.
        """
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        monkeypatch.setattr(
            "abicheck.evidence_depth.gated_source_label",
            lambda *a, **k: "build",
        )

        request = CompareRequest(
            old=InputSpec.of(old_p), new=InputSpec.of(new_p), depth="source"
        )
        result, _, _ = run_compare_request(request).as_tuple()
        assert isinstance(result, DiffResult)
        assert result.evidence_contract_error is False, result

    def test_depth_reached_passes(self, tmp_path, monkeypatch):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        monkeypatch.setattr(
            "abicheck.evidence_depth.gated_source_label",
            lambda *a, **k: "source",
        )

        request = CompareRequest(
            old=InputSpec.of(old_p), new=InputSpec.of(new_p), depth="source"
        )
        result, _, _ = run_compare_request(request).as_tuple()
        assert isinstance(result, DiffResult)
        assert (result.old_version, result.new_version, result.library) == (
            "1.0",
            "2.0",
            "libtest",
        )

    def test_depth_binary_always_satisfied(self, tmp_path, monkeypatch):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        monkeypatch.setattr(
            "abicheck.evidence_depth.gated_source_label",
            lambda *a, **k: "binary",
        )

        request = CompareRequest(
            old=InputSpec.of(old_p), new=InputSpec.of(new_p), depth="binary"
        )
        result, _, _ = run_compare_request(request).as_tuple()
        assert isinstance(result, DiffResult)
        assert (result.old_version, result.new_version, result.library) == (
            "1.0",
            "2.0",
            "libtest",
        )

    def test_no_depth_skips_the_gate(self, tmp_path, monkeypatch):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        monkeypatch.setattr(
            "abicheck.evidence_depth.gated_source_label",
            lambda *a, **k: "binary",
        )

        request = CompareRequest(old=InputSpec.of(old_p), new=InputSpec.of(new_p))
        result, _, _ = run_compare_request(request).as_tuple()
        assert isinstance(result, DiffResult)
        assert (result.old_version, result.new_version, result.library) == (
            "1.0",
            "2.0",
            "libtest",
        )
