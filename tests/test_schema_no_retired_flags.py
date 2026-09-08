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

"""The published JSON Schemas must not name a retired CLI flag in a field
``description``.

`scripts/check_docs_contract.py`'s ``_RETIRED_SURFACES`` sweep already
catches this class of staleness in Markdown/YAML docs, but it never reaches
`abicheck/schemas/*.json` -- a real gap: Phase 7d's `--on-incomplete-scope`/
`--fail-on-removed-library`/`--dso-only` removal left five `description`
fields across `compare_report.schema.json`/`aggregate_report.schema.json`
still naming the removed flags, invisible to every doc check because none
of them scan the schema tree (Codex review on PR #1159). This is a narrow,
standalone counterpart rather than a `_RETIRED_SURFACES` entry, since that
module is already at the AI-readiness file-size hard cap.
"""

from __future__ import annotations

import json
from pathlib import Path

SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "abicheck" / "schemas"

# Same four Phase 7d flags _RETIRED_SURFACES already tracks for docs.
_RETIRED_FLAGS = (
    "--on-incomplete-scope",
    "--fail-on-removed-library",
    "--no-fail-on-removed-library",
    "--dso-only",
    "--include-private-dso",
)


def _all_descriptions(schema_dir: Path) -> list[tuple[Path, str, str]]:
    """(file, json-pointer-ish label, text) for every string value in every
    schema under ``schema_dir`` -- not just ``description`` keys, so a
    retired flag hiding in an ``example``/``pattern`` string is caught too."""
    found: list[tuple[Path, str, str]] = []

    def _walk(node: object, path: str, path_file: Path) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                _walk(value, f"{path}.{key}", path_file)
        elif isinstance(node, list):
            for i, value in enumerate(node):
                _walk(value, f"{path}[{i}]", path_file)
        elif isinstance(node, str):
            found.append((path_file, path, node))

    for schema_file in sorted(schema_dir.glob("*.json")):
        _walk(json.loads(schema_file.read_text(encoding="utf-8")), "$", schema_file)
    return found


def test_no_schema_names_a_retired_cli_flag() -> None:
    hits = []
    for path, pointer, text in _all_descriptions(SCHEMAS_DIR):
        for flag in _RETIRED_FLAGS:
            if flag in text:
                hits.append(f"{path.name}{pointer}: contains {flag!r}: {text!r}")
    assert not hits, "\n".join(hits)


def test_the_real_schemas_are_actually_scanned() -> None:
    # The wiring half: a regression that pointed SCHEMAS_DIR at an empty or
    # wrong directory would leave the check above vacuously green.
    descriptions = _all_descriptions(SCHEMAS_DIR)
    assert descriptions, "no schema strings found -- SCHEMAS_DIR is wrong"
    assert any(p.name == "compare_report.schema.json" for p, _, _ in descriptions)


def test_the_sweep_actually_detects_a_planted_retired_flag(tmp_path: Path) -> None:
    # The detection half: prove the walker + substring check actually fires,
    # not just that today's real schemas happen to be clean.
    planted = tmp_path / "planted.schema.json"
    planted.write_text(
        json.dumps({"properties": {"x": {"description": "set --dso-only"}}}),
        encoding="utf-8",
    )
    hits = [
        f"{path.name}{pointer}"
        for path, pointer, text in _all_descriptions(tmp_path)
        for flag in _RETIRED_FLAGS
        if flag in text
    ]
    assert hits, "planted retired flag was not detected"
