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

"""Characterization tests for the compare-side evidence report contract.

Written *before* ADR-061 Phase 3 moved ``diff_embedded_build_source`` /
``prepare_embedded_build_source`` / ``attach_evidence_metrics`` (and the
coverage/capability rendering they drive) out of ``cli_buildsource_helpers``
into the engine, for the same reason
``tests/test_build_source_embed_errors.py`` was written before
``embed_build_source`` moved: the move relocates an **error contract** and an
**output stream**, and both are observable to a CI consumer.

Two things are pinned here and must survive the move unchanged:

* **Exit codes.** A malformed out-of-band pack passed to
  ``compare --build-info old=...`` is an *operational* failure (the invocation was
  well-formed, the data was not), so it exits **1** -- not the 64 a usage error
  gets. The engine raises ``SnapshotError``; only the CLI adapter turns that
  into ``click.ClickException``.
* **The stderr report.** The D7 coverage table, the by-side table and the
  capability list are written to **stderr** for every output format, so a
  ``--format json`` consumer's stdout stays parseable. A non-CLI caller
  (``service.run_compare_request``) must produce none of it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.errors import SnapshotError
from abicheck.model import AbiSnapshot


def _snap(tmp_path: Path, name: str, version: str) -> Path:
    from abicheck.serialization import write_snapshot

    snap = AbiSnapshot(library=name, version=version)
    path = tmp_path / f"{name}-{version}.abi.json"
    write_snapshot(snap, path)
    return path


def _corrupt_pack(tmp_path: Path, name: str = "badpack") -> Path:
    """A directory that *is* a pack (it has a manifest.json) but cannot load."""
    pack = tmp_path / name
    pack.mkdir()
    (pack / "manifest.json").write_text("{ this is not json", encoding="utf-8")
    return pack


class TestSidePackErrorContract:
    """A malformed out-of-band compare-side pack is operational, not usage."""

    def test_corrupt_old_build_info_pack_exits_1(self, tmp_path):
        old = _snap(tmp_path, "libtest", "1.0")
        new = _snap(tmp_path, "libtest", "2.0")
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old),
                str(new),
                "--build-info",
                f"old={_corrupt_pack(tmp_path)}",
            ],
        )
        # 1 == ClickException (operational). Explicitly NOT 64 (usage error):
        # the command line was well-formed; the pack's bytes were not.
        assert result.exit_code == 1, result.output
        assert result.exit_code != 64

    def test_corrupt_new_sources_pack_exits_1(self, tmp_path):
        old = _snap(tmp_path, "libtest", "1.0")
        new = _snap(tmp_path, "libtest", "2.0")
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old),
                str(new),
                "--sources",
                f"new={_corrupt_pack(tmp_path)}",
            ],
        )
        assert result.exit_code == 1, result.output

    def test_engine_side_raises_snapshot_error_not_a_click_error(self, tmp_path):
        """The engine boundary speaks ``SnapshotError``, never a Click class.

        This is the half that makes the CLI translation above a *translation*
        rather than the only definition of the contract: a typed-API caller
        gets the engine class directly.
        """
        from abicheck.buildsource.evidence_report import resolve_side_pack

        with pytest.raises(SnapshotError):
            resolve_side_pack(_corrupt_pack(tmp_path), None, None)


class TestEvidenceReportGoesToStderr:
    """The D7 tables are stderr-only so ``--format json`` stdout stays clean."""

    def _pair_with_embedded_evidence(self, tmp_path):
        from abicheck.buildsource.model import (
            BuildSourceManifest,
            CoverageStatus,
            DataLayer,
            LayerCoverage,
        )
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.serialization import write_snapshot

        paths = []
        for version in ("1.0", "2.0"):
            snap = AbiSnapshot(library="libtest", version=version)
            snap.build_source = BuildSourcePack(
                root=tmp_path,
                manifest=BuildSourceManifest(
                    coverage=[
                        LayerCoverage(
                            layer=DataLayer.L3_BUILD.value,
                            status=CoverageStatus.NOT_COLLECTED,
                        )
                    ]
                ),
            )
            path = tmp_path / f"libtest-{version}.abi.json"
            write_snapshot(snap, path)
            paths.append(path)
        return paths

    def test_json_stdout_is_parseable_while_tables_go_to_stderr(self, tmp_path):
        old, new = self._pair_with_embedded_evidence(tmp_path)
        result = CliRunner().invoke(
            main, ["compare", str(old), str(new), "--format", "json"]
        )
        assert result.exit_code in (0, 2, 4), result.stderr
        # The whole point: stdout is *only* the report document.
        json.loads(result.stdout)
        assert "Evidence coverage:" in result.stderr

    def test_typed_api_emits_no_evidence_report(self, tmp_path, capsys):
        """``run_compare_request`` has no stream, so it must write nothing."""
        from abicheck.api_types import CompareRequest, InputSpec
        from abicheck.service import run_compare_request

        old, new = self._pair_with_embedded_evidence(tmp_path)
        capsys.readouterr()
        run_compare_request(
            CompareRequest(old=InputSpec.of(old), new=InputSpec.of(new))
        )
        captured = capsys.readouterr()
        assert "Evidence coverage:" not in captured.err
        assert "Evidence coverage:" not in captured.out


class TestEmitCallbackReplacesTheQuietFlag:
    """The engine owns no stream: it hands lines to a caller-supplied sink."""

    def test_prepare_collects_lines_through_on_output(self, tmp_path):
        from abicheck.buildsource.evidence_report import prepare_embedded_build_source
        from abicheck.buildsource.model import (
            BuildSourceManifest,
            CoverageStatus,
            DataLayer,
            LayerCoverage,
        )
        from abicheck.buildsource.pack import BuildSourcePack

        pack = BuildSourcePack(
            root=tmp_path,
            manifest=BuildSourceManifest(
                coverage=[
                    LayerCoverage(
                        layer=DataLayer.L3_BUILD.value,
                        status=CoverageStatus.NOT_COLLECTED,
                    )
                ]
            ),
        )
        old = AbiSnapshot(library="l", version="1")
        new = AbiSnapshot(library="l", version="2")
        old.build_source = pack
        new.build_source = pack

        lines: list[str] = []
        prepare_embedded_build_source(
            old, new, "off", None, None, None, None, None, on_output=lines.append
        )
        assert any("Evidence coverage:" == line for line in lines)

        # And with no sink at all, nothing is produced or raised.
        prepare_embedded_build_source(old, new, "off", None, None, None, None, None)


def _inputs_pack_with_warnings(tmp_path: Path, name: str = "inputs") -> Path:
    """A Flow-2 pack that validates with warnings but no errors.

    Minimal-but-valid is already enough: an empty ``source_facts/`` yields
    "zero readable TU records" and "no fact_set identity", both non-fatal.
    """
    pack = tmp_path / name
    pack.mkdir()
    (pack / "source_facts").mkdir()
    (pack / "manifest.json").write_text(
        json.dumps(
            {
                "kind": "abicheck_inputs",
                "abicheck_inputs_version": 1,
                "library": "libfoo.so",
                "version": "1.0",
                "created_by": "test",
            }
        ),
        encoding="utf-8",
    )
    return pack


class TestSidePackWarningsReachTheCaller:
    """A Flow-2 pack's non-fatal findings must not be swallowed.

    Before the loader moved into the engine the CLI printed every
    ``report.warnings`` entry to stderr itself. The engine owns no stream, so
    it hands them back through ``on_warning`` — and the compare-side resolver
    initially forgot to thread it, which let a successful comparison conceal
    incomplete fact families and empty source surfaces (Codex review, P2).
    """

    def test_engine_forwards_every_warning_to_the_sink(self, tmp_path: Path) -> None:
        from abicheck.buildsource.evidence_report import resolve_side_pack

        seen: list[str] = []
        pack = resolve_side_pack(
            _inputs_pack_with_warnings(tmp_path), None, None, on_warning=seen.append
        )
        assert pack is not None
        assert seen, "the pack's non-fatal findings never reached the sink"
        assert any("TU records" in line for line in seen), seen

    def test_engine_stays_silent_for_a_caller_that_owns_no_stream(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Omitting the sink discards them rather than printing: a typed API
        caller must not have output appear on its process's streams."""
        from abicheck.buildsource.evidence_report import resolve_side_pack

        assert resolve_side_pack(_inputs_pack_with_warnings(tmp_path), None, None)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_the_cli_adapter_supplies_the_stderr_sink(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The behaviour a user actually sees, restored end-to-end."""
        from abicheck.cli_buildsource_helpers import _resolve_side_pack

        assert _resolve_side_pack(_inputs_pack_with_warnings(tmp_path), None, None)
        captured = capsys.readouterr()
        assert "TU records" in captured.err
        assert captured.out == ""
