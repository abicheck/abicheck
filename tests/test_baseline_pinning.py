"""Tests for baseline pinning: provenance metadata (schema v4)."""
import json
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from abicheck.model import AbiSnapshot
from abicheck.serialization import (
    SCHEMA_VERSION,
    load_snapshot,
    save_snapshot,
    snapshot_from_dict,
    snapshot_to_dict,
)


def _sample_snap(**kwargs) -> AbiSnapshot:
    defaults = dict(library="libfoo.so.1", version="2.0.0")
    defaults.update(kwargs)
    return AbiSnapshot(**defaults)


# ---------------------------------------------------------------------------
# 1a. Provenance metadata (schema v4)
# ---------------------------------------------------------------------------

class TestSchemaV4:
    def test_schema_version_is_7(self):
        assert SCHEMA_VERSION == 8

    def test_provenance_fields_roundtrip(self):
        snap = _sample_snap(
            git_commit="abc1234def5678",
            git_tag="v2.0.0",
            created_at="2026-03-24T12:00:00+00:00",
            build_id="ci-run-42",
        )
        d = snapshot_to_dict(snap)
        assert d["schema_version"] == SCHEMA_VERSION
        assert d["git_commit"] == "abc1234def5678"
        assert d["git_tag"] == "v2.0.0"
        assert d["created_at"] == "2026-03-24T12:00:00+00:00"
        assert d["build_id"] == "ci-run-42"

        snap2 = snapshot_from_dict(d)
        assert snap2.git_commit == "abc1234def5678"
        assert snap2.git_tag == "v2.0.0"
        assert snap2.created_at == "2026-03-24T12:00:00+00:00"
        assert snap2.build_id == "ci-run-42"

    def test_provenance_fields_default_none(self):
        snap = _sample_snap()
        assert snap.git_commit is None
        assert snap.git_tag is None
        assert snap.created_at is None
        assert snap.build_id is None

    def test_v3_snapshot_loads_without_provenance(self):
        """Old v3 snapshots should load fine — provenance defaults to None."""
        d = {
            "schema_version": 3,
            "library": "libold.so",
            "version": "1.0",
        }
        snap = snapshot_from_dict(d)
        assert snap.library == "libold.so"
        assert snap.git_commit is None
        assert snap.git_tag is None
        assert snap.created_at is None
        assert snap.build_id is None

    def test_provenance_file_roundtrip(self):
        snap = _sample_snap(
            git_commit="deadbeef",
            git_tag="v2.0.0",
            created_at="2026-03-24T00:00:00+00:00",
            build_id="gh-actions-1234",
        )
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = Path(f.name)
        try:
            save_snapshot(snap, tmp)
            # Verify JSON on disk has provenance
            raw = json.loads(tmp.read_text())
            assert raw["git_commit"] == "deadbeef"
            assert raw["schema_version"] == SCHEMA_VERSION

            snap2 = load_snapshot(tmp)
            assert snap2.git_commit == "deadbeef"
            assert snap2.build_id == "gh-actions-1234"
        finally:
            tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Provenance stamping (_stamp_provenance)
# ---------------------------------------------------------------------------

class TestStampProvenance:
    def test_stamp_sets_created_at(self):
        from abicheck.cli import _stamp_provenance
        snap = _sample_snap()
        _stamp_provenance(snap, git_tag=None, build_id=None, no_git=True)
        assert snap.created_at is not None
        # Verify ISO 8601 format
        assert "T" in snap.created_at

    def test_stamp_sets_git_tag_and_build_id(self):
        from abicheck.cli import _stamp_provenance
        snap = _sample_snap()
        _stamp_provenance(snap, git_tag="v1.0", build_id="run-99", no_git=True)
        assert snap.git_tag == "v1.0"
        assert snap.build_id == "run-99"

    def test_stamp_auto_detects_git_commit(self):
        from abicheck.cli import _stamp_provenance
        snap = _sample_snap()
        fake_result = mock.Mock(returncode=0, stdout="abc1234\n", stderr="")
        with mock.patch("subprocess.run", return_value=fake_result) as m:
            _stamp_provenance(snap, git_tag=None, build_id=None, no_git=False)
        assert snap.git_commit == "abc1234"
        m.assert_called_once_with(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )

    def test_stamp_no_git_skips_detection(self):
        from abicheck.cli import _stamp_provenance
        snap = _sample_snap()
        with mock.patch("subprocess.run") as m:
            _stamp_provenance(snap, git_tag=None, build_id=None, no_git=True)
        m.assert_not_called()
        assert snap.git_commit is None

    def test_stamp_git_not_found_graceful(self):
        from abicheck.cli import _stamp_provenance
        snap = _sample_snap()
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            _stamp_provenance(snap, git_tag=None, build_id=None, no_git=False)
        assert snap.git_commit is None

    def test_stamp_git_timeout_graceful(self):
        from abicheck.cli import _stamp_provenance
        snap = _sample_snap()
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=5)):
            _stamp_provenance(snap, git_tag=None, build_id=None, no_git=False)
        assert snap.git_commit is None
