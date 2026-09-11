# SPDX-License-Identifier: Apache-2.0
"""``compare-release``'s declared-deployment-floor digest projection --

split out of ``test_compare_release.py`` (that file sits at its own
``architecture/debt.yaml`` ``no_growth`` baseline with no headroom, the
same reason ``test_compare_release_annotations.py`` was split out).

Codex review, P2 (PR #1221 follow-up): a directory/package release with a
declared ``deployment:`` contract (``.abicheck.yml``'s ``deployment:``
block / ``EnvironmentMatrix``) must publish the same
``env_matrix_source_sha256`` content digest the scalar ``compare`` report
and the ``--no-baseline`` audit report already carry -- both per library
(surviving ``_strip_diff_results_and_adjust_verdict`` discarding each
library's ``DiffResult``) and once, at the release envelope, since one
release run threads the identical ``EnvironmentMatrix`` to every library.
Before the fix, neither was present: a within-floor release was
indistinguishable, in the rendered JSON, from one with no deployment
contract at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json

# ── helpers (mirrors test_compare_release.py's own) ────────────────────────


def _snap(
    version: str = "1.0",
    funcs: list[Function] | None = None,
    library: str = "libfoo.so",
) -> AbiSnapshot:
    if funcs is None:
        funcs = [
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
        ]
    return AbiSnapshot(
        library=library, version=version, functions=funcs, from_headers=True
    )


def _write_snap(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.output


class TestReleaseJsonEnvMatrixDigest:
    def test_carries_the_digest_per_library_and_at_the_envelope(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        work = tmp_path / "project"
        work.mkdir()
        old_dir = work / "old"
        old_dir.mkdir()
        new_dir = work / "new"
        new_dir.mkdir()
        for name in ("libfoo.json", "libbar.json"):
            snap = _snap()
            _write_snap(old_dir / name, snap)
            _write_snap(new_dir / name, snap)
        (work / ".abicheck.yml").write_text(
            'deployment:\n  runtime_floors:\n    GLIBC: "2.28"\n'
        )
        monkeypatch.chdir(work)

        code, out = _invoke("compare", str(old_dir), str(new_dir), "--format", "json")
        assert code == 0, out
        data = json.loads(out)

        digest = data["env_matrix_source_sha256"]
        assert isinstance(digest, str) and digest.startswith("sha256:")
        assert len(data["libraries"]) == 2
        for lib in data["libraries"]:
            assert lib["env_matrix_source_sha256"] == digest, (
                f"{lib['library']}: per-library digest lost or diverged from "
                "the release-wide contract"
            )

    def test_omits_the_digest_with_no_deployment_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The converse: no ``deployment:`` contract means no digest
        anywhere -- never a fabricated one, and never present on one
        library but not another."""
        work = tmp_path / "project"
        work.mkdir()
        old_dir = work / "old"
        old_dir.mkdir()
        new_dir = work / "new"
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        monkeypatch.chdir(work)

        code, out = _invoke("compare", str(old_dir), str(new_dir), "--format", "json")
        assert code == 0, out
        data = json.loads(out)

        assert "env_matrix_source_sha256" not in data
        for lib in data["libraries"]:
            assert "env_matrix_source_sha256" not in lib
