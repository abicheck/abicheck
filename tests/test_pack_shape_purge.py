# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""``purge_external_outputs``'s return-value contract, and
``cli_buildsource_helpers._purge_and_record``'s escalation of a purge
failure to ``record.status = "failed"``.

The bug class (CodeRabbit review, PR #974): a real removal failure (a
locked file, a permissions error) was silently swallowed -- the caller had
no way to learn a stale, un-purged normalized output might still be sitting
under ``pack_root`` for a later hashing pass to fold into the published
pack's content identity as if it were valid, current-run evidence. Fixed by
returning whether every declared output/directory was confirmed absent, and
(at the three call sites, exercised here via the shared
``_purge_and_record`` helper) escalating a failure to ``record.status =
"failed"`` -- which plugs directly into the pre-existing
``_enforce_strict_mode`` D9 gate (``tests/test_build_source_extractor.py``
already proves that gate raises on any ``"failed"``-status record under
``--collection-mode strict``, so a purge failure now aborts a strict run
the same way any other extractor failure does).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from abicheck.buildsource.build_evidence import BuildEvidence
from abicheck.buildsource.model import ExtractorRecord
from abicheck.buildsource.pack_shape import purge_external_outputs
from abicheck.cli_buildsource_helpers import _purge_and_record


def _manifest(name: str, *output_paths: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        outputs=[SimpleNamespace(path=p) for p in output_paths],
    )


class TestPurgeExternalOutputsReturnValue:
    def test_removes_declared_outputs_and_returns_true(self, tmp_path: Path) -> None:
        out = tmp_path / "build" / "build_evidence.json"
        out.parent.mkdir(parents=True)
        out.write_text("{}")
        manifest = _manifest("ex", "build/build_evidence.json")

        assert purge_external_outputs(tmp_path, manifest) is True
        assert not out.exists()

    def test_already_absent_output_is_not_a_failure(self, tmp_path: Path) -> None:
        # Nothing written at all -- FileNotFoundError must not read as a
        # removal failure, only a genuine inability to remove an existing
        # file should.
        manifest = _manifest("ex", "build/never_written.json")

        assert purge_external_outputs(tmp_path, manifest) is True

    def test_removes_normalized_directory(self, tmp_path: Path) -> None:
        norm_dir = tmp_path / "normalized" / "ex"
        norm_dir.mkdir(parents=True)
        (norm_dir / "artifact.json").write_text("{}")
        manifest = _manifest("ex")

        assert purge_external_outputs(tmp_path, manifest) is True
        assert not norm_dir.exists()

    def test_returns_false_when_a_declared_output_cannot_be_removed(
        self, tmp_path: Path
    ) -> None:
        # A directory sitting where the manifest declares a file output:
        # Path.unlink() raises a real OSError (IsADirectoryError on POSIX),
        # never FileNotFoundError -- exactly the "exists but can't be
        # removed" case this return value exists to surface.
        stubborn = tmp_path / "build" / "build_evidence.json"
        stubborn.mkdir(parents=True)
        manifest = _manifest("ex", "build/build_evidence.json")

        assert purge_external_outputs(tmp_path, manifest) is False
        # Left in place -- this function never raises or force-removes;
        # it only ever reports.
        assert stubborn.exists()

    def test_one_failure_does_not_stop_purging_the_rest(self, tmp_path: Path) -> None:
        stubborn = tmp_path / "a" / "stubborn.json"
        stubborn.mkdir(parents=True)
        removable = tmp_path / "b" / "removable.json"
        removable.parent.mkdir(parents=True)
        removable.write_text("{}")
        manifest = _manifest("ex", "a/stubborn.json", "b/removable.json")

        assert purge_external_outputs(tmp_path, manifest) is False
        assert stubborn.exists()
        assert not removable.exists()


class TestPurgeAndRecordEscalation:
    def test_success_leaves_status_and_diagnostics_untouched(
        self, tmp_path: Path
    ) -> None:
        out = tmp_path / "build" / "build_evidence.json"
        out.parent.mkdir(parents=True)
        out.write_text("{}")
        manifest = _manifest("ex", "build/build_evidence.json")
        record = ExtractorRecord(name="ex", status="failed", detail="tool exit 1")
        merged = BuildEvidence()

        _purge_and_record(tmp_path, manifest, record, merged)

        # Unrelated to the purge outcome -- whatever the caller already set
        # this to (here, "failed" for the tool's own reason) is preserved,
        # and no purge-specific diagnostic is added on a clean purge.
        assert record.status == "failed"
        assert record.diagnostics == []
        assert merged.diagnostics == []

    def test_purge_failure_forces_status_failed_and_records_both_diagnostics(
        self, tmp_path: Path
    ) -> None:
        stubborn = tmp_path / "build" / "build_evidence.json"
        stubborn.mkdir(parents=True)
        manifest = _manifest("ex", "build/build_evidence.json")
        # Starts "skipped" (e.g. an action-ceiling gate), not "failed" --
        # the purge failure must still force it to "failed" regardless of
        # the status it already carried, since an un-purged file is a data-
        # integrity problem independent of why the extractor didn't run.
        record = ExtractorRecord(name="ex", status="skipped", detail="gated out")
        merged = BuildEvidence()

        _purge_and_record(tmp_path, manifest, record, merged)

        assert record.status == "failed"
        assert len(record.diagnostics) == 1
        assert "ex" in record.diagnostics[0]
        assert merged.diagnostics == record.diagnostics
