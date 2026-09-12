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

"""Either spelling of a build-output operand (PR #1242).

Split out of ``test_build_output.py``, which is at its
``architecture/debt.yaml`` no-growth baseline.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.buildsource.build_output import (
    BUILD_OUTPUT_SCHEMA,
    load_build_output,
    resolve_build_output_manifest,
    validate_build_output,
)


class TestResolveBuildOutputManifest:
    """Either spelling of a build-output operand, and the root each gives.

    A build output may be named as its directory or as the manifest file
    itself -- under any name, since the ``schema:`` tag is what makes a
    document one. Relative artifact paths must resolve against the
    directory *holding the manifest* in both cases; re-deriving the
    conventional filename from a named manifest's parent is the defect
    registered as ``cli_surface.name_independent_dispatch_undone_
    downstream``.
    """

    @pytest.mark.parametrize(
        "name", ["build-output.json", "run-42.json", "artifacts.json", "OUT"]
    )
    def test_a_named_manifest_keeps_its_name_and_yields_its_directory(
        self, tmp_path: Path, name: str
    ) -> None:
        manifest = tmp_path / name
        manifest.write_text(json.dumps({"schema": BUILD_OUTPUT_SCHEMA}))
        assert resolve_build_output_manifest(manifest) == (tmp_path, manifest)

    def test_a_directory_yields_the_conventional_manifest(self, tmp_path: Path) -> None:
        assert resolve_build_output_manifest(tmp_path) == (
            tmp_path,
            tmp_path / "build-output.json",
        )

    @pytest.mark.parametrize("name", ["build-output.json", "run-42.json"])
    def test_load_and_validate_accept_a_named_manifest(
        self, tmp_path: Path, name: str
    ) -> None:
        (tmp_path / name).write_text(
            json.dumps({"schema": BUILD_OUTPUT_SCHEMA, "targets": []})
        )
        assert load_build_output(tmp_path / name).schema == BUILD_OUTPUT_SCHEMA
        assert validate_build_output(tmp_path / name).ok
