# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``scripts/fixture_sync.py`` — the write-or-``--check`` driver
the committed-snapshot generators share.

The generators' own end-to-end behaviour is already covered where their fixtures
are consumed (``tests/test_g20_catalog.py``, ``tests/test_reachability_examples.py``);
what those cannot exercise is the driver's *drift* half against a tree that has
actually drifted, since the committed fixtures are — by construction — in sync.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "fixture_sync.py"
_spec = importlib.util.spec_from_file_location("fixture_sync", _SCRIPT)
assert _spec is not None and _spec.loader is not None
fixture_sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture_sync)


def _identity(obj: object) -> object:
    """A ``to_dict`` that hands back what the builder produced, unchanged."""
    return obj


def _sync(tmp_path: Path, fixtures: dict, *, check: bool) -> int:
    return fixture_sync.sync_fixtures(
        fixtures,
        case_dir=lambda name: tmp_path / "examples" / name,
        root=tmp_path,
        to_dict=_identity,
        check=check,
        label="Test fixtures",
        regen_command="python scripts/gen_test_fixtures.py",
    )


class TestRenderFixture:
    def test_a_builder_is_serialized_as_sorted_indented_json(self) -> None:
        text = fixture_sync.render_fixture(lambda: {"b": 1, "a": 2}, _identity)
        assert text == '{\n  "a": 2,\n  "b": 1\n}\n'

    def test_a_literal_string_fixture_is_written_verbatim(self) -> None:
        """A hand-written sidecar (a YAML suppression file) is not JSON."""
        assert fixture_sync.render_fixture("rules: []\n", _identity) == "rules: []\n"


class TestSyncFixtures:
    def test_writing_creates_the_case_directory_and_its_files(
        self, tmp_path: Path
    ) -> None:
        rc = _sync(
            tmp_path,
            {"case001": {"snapshot.abi.json": lambda: {"library": "libx"}}},
            check=False,
        )
        written = tmp_path / "examples" / "case001" / "snapshot.abi.json"
        assert rc == 0
        assert written.read_text(encoding="utf-8") == '{\n  "library": "libx"\n}\n'

    def test_check_passes_once_the_committed_files_match(self, tmp_path: Path) -> None:
        fixtures = {"case001": {"snapshot.abi.json": lambda: {"library": "libx"}}}
        assert _sync(tmp_path, fixtures, check=False) == 0
        assert _sync(tmp_path, fixtures, check=True) == 0

    def test_check_reports_drift_when_a_committed_file_differs(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert (
            _sync(
                tmp_path,
                {"case001": {"snapshot.abi.json": lambda: {"library": "libx"}}},
                check=False,
            )
            == 0
        )
        rc = _sync(
            tmp_path,
            {"case001": {"snapshot.abi.json": lambda: {"library": "libCHANGED"}}},
            check=True,
        )
        err = capsys.readouterr().err
        assert rc == 1
        assert "drift: examples/case001/snapshot.abi.json" in err.replace("\\", "/")

    def test_check_does_not_write_anything(self, tmp_path: Path) -> None:
        """``--check`` is read-only: it must never create the file it misses."""
        rc = _sync(
            tmp_path,
            {"case001": {"snapshot.abi.json": lambda: {"library": "libx"}}},
            check=True,
        )
        assert rc == 1
        assert not (tmp_path / "examples" / "case001").exists()

    def test_an_absent_file_is_drift_even_when_the_fixture_is_empty(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """CodeRabbit review: reading a missing file as ``""`` would let an
        empty literal fixture pass ``--check`` with nothing committed at all."""
        rc = _sync(tmp_path, {"case001": {"empty.yaml": ""}}, check=True)
        assert rc == 1
        assert "drift: examples/case001/empty.yaml" in capsys.readouterr().err.replace(
            "\\", "/"
        )

    def test_an_empty_fixture_round_trips_through_write_then_check(
        self, tmp_path: Path
    ) -> None:
        fixtures = {"case001": {"empty.yaml": ""}}
        assert _sync(tmp_path, fixtures, check=False) == 0
        assert (tmp_path / "examples" / "case001" / "empty.yaml").read_text() == ""
        assert _sync(tmp_path, fixtures, check=True) == 0
