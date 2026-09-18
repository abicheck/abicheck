# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""What ``-o/--output`` guarantees that shell redirection (``> snap.json``)
does not -- asserted, so the documentation recommending it is backed by tests
rather than by a claim.

Motivated by the ADR-074 field report, whose PVXS workflow persisted
snapshots with ``> /tmp/pvxs-CUR.json``. Redirection is *not* invalid --
plain stdout is a supported clean-JSON transport for piping, and that is
asserted here too -- but a file-producing CI step gets four things from
``-o`` that the shell cannot give it, and each is pinned below.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.snapshot_io import (
    SnapshotCompression,
    read_snapshot_bytes,
    write_snapshot_bytes,
)


class TestAtomicDestination:
    """(1) An abicheck-owned write replaces the destination atomically, so a
    failure cannot leave a successful-looking partial snapshot behind. Shell
    redirection truncates the destination *before* abicheck runs, so a failed
    run leaves an empty file that later steps read as a snapshot."""

    def test_a_failed_encode_leaves_an_existing_destination_untouched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dest = tmp_path / "snap.json"
        dest.write_bytes(b'{"previous": "content"}')

        def _boom(*_args: object, **_kwargs: object) -> bytes:
            raise RuntimeError("serialization failed")

        monkeypatch.setattr("abicheck.snapshot_io.encode_snapshot_bytes", _boom)
        with pytest.raises(RuntimeError, match="serialization failed"):
            write_snapshot_bytes(b'{"new": true}', dest)
        assert dest.read_bytes() == b'{"previous": "content"}'

    def test_a_failed_encode_creates_no_destination_at_all(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dest = tmp_path / "fresh.json"

        def _boom(*_args: object, **_kwargs: object) -> bytes:
            raise RuntimeError("serialization failed")

        monkeypatch.setattr("abicheck.snapshot_io.encode_snapshot_bytes", _boom)
        with pytest.raises(RuntimeError):
            write_snapshot_bytes(b'{"new": true}', dest)
        assert not dest.exists()
        # ...and no temp debris is left beside it either.
        assert list(tmp_path.iterdir()) == []

    @pytest.mark.parametrize(
        "compression",
        [SnapshotCompression.NONE, SnapshotCompression.GZIP, SnapshotCompression.ZSTD],
    )
    def test_the_destination_is_never_observed_half_written(
        self, tmp_path: Path, compression: SnapshotCompression
    ) -> None:
        """Every supported envelope, not just the one with a known incident:
        after a successful write the destination decodes whole, and a second
        write over it replaces it whole."""
        dest = tmp_path / "snap.json"
        first = json.dumps({"schema_version": 1, "n": list(range(2000))}).encode()
        second = json.dumps({"schema_version": 1, "n": [1]}).encode()
        write_snapshot_bytes(first, dest, compression=compression)
        assert read_snapshot_bytes(dest) == first
        write_snapshot_bytes(second, dest, compression=compression)
        assert read_snapshot_bytes(dest) == second


@pytest.mark.integration
class TestCliOutputContract:
    """(2)-(4) through the real ``dump`` CLI: an abicheck-owned diagnostic for
    an unwritable destination, native compression selection that is only
    available with ``-o``, and a stdout that stays clean JSON so piping keeps
    working."""

    @staticmethod
    def _library(tmp_path: Path) -> tuple[Path, Path]:
        import shutil
        import subprocess

        if shutil.which("gcc") is None:  # pragma: no cover - env-dependent
            pytest.skip("needs a real gcc")
        include = tmp_path / "include"
        include.mkdir()
        (include / "h.h").write_text("int only_fn(int);\n")
        src = tmp_path / "s.c"
        src.write_text("int only_fn(int x){return x;}\n")
        so = tmp_path / "libs.so"
        subprocess.run(
            ["gcc", "-shared", "-fPIC", "-o", str(so), str(src)],
            check=True,
            capture_output=True,
        )
        return so, include

    def test_unwritable_destination_gets_an_abicheck_diagnostic(
        self, tmp_path: Path
    ) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main

        so, include = self._library(tmp_path)
        # A missing *parent directory* is not the failure case: abicheck
        # creates the tree, which is itself part of what -o buys over `>`.
        # An unwritable destination is a parent that exists and is a FILE.
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")
        dest = blocker / "out.json"
        result = CliRunner().invoke(
            main, ["dump", str(so), "-H", str(include), "-o", str(dest)]
        )
        assert result.exit_code == 1
        assert f"Cannot write to {dest}" in result.output

    def test_a_missing_parent_directory_is_created(self, tmp_path: Path) -> None:
        """The other half of the same guarantee, pinned so the test above
        cannot be read as "any missing path fails"."""
        from click.testing import CliRunner

        from abicheck.cli import main

        so, include = self._library(tmp_path)
        dest = tmp_path / "a" / "b" / "out.json"
        result = CliRunner().invoke(
            main, ["dump", str(so), "-H", str(include), "-o", str(dest)]
        )
        assert result.exit_code == 0, result.output
        assert dest.is_file()

    def test_compression_requires_an_output_file(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main

        so, include = self._library(tmp_path)
        result = CliRunner().invoke(
            main, ["dump", str(so), "-H", str(include), "--compression", "gzip"]
        )
        assert result.exit_code == 64
        assert "requires -o/--output" in result.output

    def test_plain_stdout_stays_valid_json_for_piping(self, tmp_path: Path) -> None:
        """Redirection is a supported transport, not a mistake -- so this must
        keep holding, and the docs may keep showing `| jq` examples."""
        import subprocess
        import sys

        so, include = self._library(tmp_path)
        proc = subprocess.run(
            [sys.executable, "-m", "abicheck", "dump", str(so), "-H", str(include)],
            capture_output=True,
            check=True,
        )
        assert json.loads(proc.stdout)  # parses, and is non-empty
