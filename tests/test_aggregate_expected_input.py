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

"""``aggregate --manifest``'s content dispatch (plan slice 7q).

The primitive-level property suite AGENTS.md asks for, and the same shape
`tests/test_validation_input.py` gives slice 7p's classifier: the claim
under test is "the document's own content decides, the filename never
does", so it is stated as an exhaustive sweep of every recognized shape
against every filename — **including each shape's own conventional name
sitting on the other shape's content**. A filename-keyed implementation
passes a test that only ever pairs `run-plan.json` with a run plan; it
fails this one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.workflows.aggregate.expected_input import (
    ExpectedInputError,
    ExpectedInputKind,
    classify_expected_input,
)

#: shape label -> (document, expected kind). Every shape the dispatch
#: recognizes, including the untagged run plan `RunPlan.from_dict` accepts.
SHAPES: dict[str, tuple[dict[str, object], ExpectedInputKind]] = {
    "tagged-run-plan-v1": (
        {
            "schema": "abicheck.run-plan/v1",
            "checks": [{"check_id": "libfoo@linux", "required": True}],
        },
        ExpectedInputKind.RUN_PLAN,
    ),
    "tagged-run-plan-v2-with-gate": (
        {
            "schema": "abicheck.run-plan/v2",
            "gate": {"missing_required": "warn"},
            "checks": [{"check_id": "libfoo@linux", "required": True}],
        },
        ExpectedInputKind.RUN_PLAN,
    ),
    "tagged-run-plan-newer-than-this-build": (
        # Classified as a run plan on purpose: the version gate is
        # `RunPlan.from_dict`'s, and it fails loudly. Classifying this as
        # "not a run plan" would route it to the manifest reader and report
        # a missing `targets` key instead.
        {"schema": "abicheck.run-plan/v99", "checks": []},
        ExpectedInputKind.RUN_PLAN,
    ),
    "untagged-run-plan": (
        {"checks": [{"check_id": "libfoo@linux", "required": True}]},
        ExpectedInputKind.RUN_PLAN,
    ),
    "untagged-empty-run-plan": ({"checks": []}, ExpectedInputKind.RUN_PLAN),
    "manifest": (
        {"targets": [{"id": "libfoo@linux", "required": True}]},
        ExpectedInputKind.MANIFEST,
    ),
    "versioned-manifest-with-gate": (
        {
            "aggregate_manifest_version": "2.0",
            "gate": {"unexpected_target": "fail"},
            "targets": [{"id": "libfoo@linux", "required": True}],
        },
        ExpectedInputKind.MANIFEST,
    ),
    "empty-mapping": ({}, ExpectedInputKind.MANIFEST),
}

#: Every filename each shape might arrive under — each shape's own
#: conventional name is in here, which is what makes the sweep adversarial
#: rather than confirmatory.
FILENAMES = (
    "run-plan.json",
    "targets.json",
    "manifest.json",
    "abi-manifest.json",
    "plan-42.json",
    "run-plan.yaml",
    "expected",
    "RUN-PLAN.JSON",
)


def _write(tmp_path: Path, name: str, document: object) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


class TestContentDecidesNotTheFilename:
    @pytest.mark.parametrize("shape", sorted(SHAPES))
    @pytest.mark.parametrize("filename", FILENAMES)
    def test_every_shape_under_every_filename(
        self, tmp_path: Path, shape: str, filename: str
    ) -> None:
        document, want = SHAPES[shape]
        kind, parsed = classify_expected_input(_write(tmp_path, filename, document))
        assert kind is want, f"{shape} named {filename!r} classified as {kind}"
        assert parsed == document

    def test_the_sweep_is_not_vacuous(self) -> None:
        """Both kinds must actually occur, or the sweep above would pass
        against an implementation that answers one constant."""
        assert {kind for _doc, kind in SHAPES.values()} == set(ExpectedInputKind)
        for shape, (_doc, kind) in SHAPES.items():
            # Each shape's conventional name is among the filenames, so
            # every classification is exercised against a misleading one.
            conventional = (
                "run-plan.json"
                if kind is ExpectedInputKind.RUN_PLAN
                else ("manifest.json")
            )
            assert conventional in FILENAMES, shape


class TestRejections:
    """Classification answers *which reader*; it never validates. These are
    the documents that have no reader at all."""

    @pytest.mark.parametrize(
        "document",
        [[], ["a"], "text", 42, None, {"schema": "abicheck.build-output/v1"}],
    )
    def test_non_object_documents(self, tmp_path: Path, document: object) -> None:
        if isinstance(document, dict):
            # A mapping with someone else's schema tag is still a mapping:
            # it is read as a manifest and fails in that reader, not here.
            kind, _ = classify_expected_input(_write(tmp_path, "x.json", document))
            assert kind is ExpectedInputKind.MANIFEST
            return
        with pytest.raises(ExpectedInputError):
            classify_expected_input(_write(tmp_path, "x.json", document))

    def test_document_claiming_both_shapes(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "x.json", {"checks": [], "targets": []})
        with pytest.raises(ExpectedInputError, match="both"):
            classify_expected_input(path)

    def test_unparseable_and_unreadable(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with pytest.raises(ExpectedInputError, match="cannot parse"):
            classify_expected_input(bad)
        with pytest.raises(ExpectedInputError, match="cannot read"):
            classify_expected_input(tmp_path / "missing.json")
