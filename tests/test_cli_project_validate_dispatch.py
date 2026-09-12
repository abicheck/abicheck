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

"""``project validate INPUT``'s one-command, three-schema dispatch (Phase 7p).

Two levels, deliberately: the classifier's contract stated as invariants
over generated inputs (it is a reusable dispatch primitive, so AGENTS.md's
"primitive-level property tests" rule applies — a fixed-example test only
forecloses the filenames it happens to name), and the CLI end to end.

The invariant that matters, and the one a filename-based implementation
would fail: **classification depends only on a document's content, never
on its name or extension.** That is what makes the consolidation safe —
the three superseded commands each trusted the caller to pick the right
validator, and this one has to derive it.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from abicheck.buildsource.build_output import BUILD_OUTPUT_SCHEMA
from abicheck.buildsource.validation_input import (
    ValidationInputError,
    ValidationInputKind,
    classify_validation_input,
)
from abicheck.cli import main

#: One representative document per recognized shape, with the kind it must
#: classify to. Content only — no filename appears here on purpose.
DOCUMENTS: dict[ValidationInputKind, str] = {
    ValidationInputKind.PROJECT_CONFIG: (
        "targets:\n  lib:\n    kind: library\n    binary_pattern: lib*.so\n"
    ),
    ValidationInputKind.USE_CASE_MANIFEST: "- use_case: training\n  entrypoints: [train]\n",
    ValidationInputKind.BUILD_OUTPUT: json.dumps({"schema": BUILD_OUTPUT_SCHEMA}),
    ValidationInputKind.EMPTY_DOCUMENT: "",
}

#: Names deliberately chosen to disagree with the content they will hold —
#: including each shape's *conventional* name, so a filename-based
#: implementation fails rather than coincidentally passing.
MISLEADING_NAMES = (
    ".abicheck.yml",
    "impact-use-cases.yaml",
    "build-output.json",
    "run-42.json",
    "config.yaml",
    "NOTES",
)


def _run(*args: str) -> Result:
    return CliRunner().invoke(main, ["project", "validate", *args])


class TestClassifierProperties:
    """The classifier's contract, over every (shape, name) combination."""

    @pytest.mark.parametrize(
        ("kind", "name"), list(itertools.product(DOCUMENTS, MISLEADING_NAMES))
    )
    def test_classification_is_content_only(
        self, tmp_path: Path, kind: ValidationInputKind, name: str
    ) -> None:
        # Exhaustive over the product, not a sampled pair: the oracle is
        # the shape the content was *written* as, stated independently of
        # the implementation's own branch order.
        path = tmp_path / name
        path.write_text(DOCUMENTS[kind], encoding="utf-8")
        assert classify_validation_input(path)[0] is kind

    @pytest.mark.parametrize("name", MISLEADING_NAMES)
    def test_a_build_output_directory_is_recognized_under_any_name(
        self, tmp_path: Path, name: str
    ) -> None:
        directory = tmp_path / name
        directory.mkdir()
        (directory / "build-output.json").write_text(
            json.dumps({"schema": BUILD_OUTPUT_SCHEMA}), encoding="utf-8"
        )
        kind, target = classify_validation_input(directory)
        assert kind is ValidationInputKind.BUILD_OUTPUT
        assert target == directory

    def test_a_named_build_output_manifest_is_passed_through(
        self, tmp_path: Path
    ) -> None:
        # The manifest path is handed to the validator as given, never
        # swapped for its parent directory: the validator resolves a
        # build output from either spelling, and substituting the
        # directory would send it looking for the conventional
        # `build-output.json` beside a manifest that is legitimately
        # called something else.
        directory = tmp_path / "abicheck-build"
        directory.mkdir()
        manifest = directory / "run-42.json"
        manifest.write_text(
            json.dumps({"schema": BUILD_OUTPUT_SCHEMA}), encoding="utf-8"
        )
        assert classify_validation_input(manifest) == (
            ValidationInputKind.BUILD_OUTPUT,
            manifest,
        )

    def test_the_schema_tag_outranks_a_mapping_default(self, tmp_path: Path) -> None:
        # A document carrying both a build-output schema tag and
        # config-looking keys follows its explicit discriminator — the
        # mapping default is only for documents that declare nothing.
        path = tmp_path / "ambiguous.yml"
        path.write_text(
            json.dumps({"schema": BUILD_OUTPUT_SCHEMA, "targets": []}),
            encoding="utf-8",
        )
        assert classify_validation_input(path)[0] is ValidationInputKind.BUILD_OUTPUT

    @pytest.mark.parametrize("content", ["42", "just a string", "true"])
    def test_a_scalar_document_is_rejected_by_shape(
        self, tmp_path: Path, content: str
    ) -> None:
        path = tmp_path / "scalar.yml"
        path.write_text(content, encoding="utf-8")
        with pytest.raises(ValidationInputError) as exc:
            classify_validation_input(path)
        # The message must name all three recognized shapes: a user who
        # got here does not yet know which one their document should be.
        assert "mapping" in str(exc.value)
        assert "list" in str(exc.value)
        assert BUILD_OUTPUT_SCHEMA in str(exc.value)

    def test_a_plain_directory_is_rejected_naming_the_contract(
        self, tmp_path: Path
    ) -> None:
        directory = tmp_path / "not-a-build"
        directory.mkdir()
        with pytest.raises(ValidationInputError) as exc:
            classify_validation_input(directory)
        assert "build-output.json" in str(exc.value)

    def test_unparseable_yaml_is_a_classification_error_not_a_crash(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "broken.yml"
        path.write_text("targets: [unclosed\n", encoding="utf-8")
        with pytest.raises(ValidationInputError):
            classify_validation_input(path)


class TestValidateCli:
    """The command itself, once per kind, through the public entry point."""

    def test_a_config_validates(self, tmp_path: Path) -> None:
        path = tmp_path / "anything.yml"
        path.write_text(DOCUMENTS[ValidationInputKind.PROJECT_CONFIG], encoding="utf-8")
        res = _run(str(path))
        assert res.exit_code == 0, res.output
        assert "project validation" in res.output

    def test_a_use_case_manifest_validates(self, tmp_path: Path) -> None:
        path = tmp_path / "anything.yml"
        path.write_text(
            DOCUMENTS[ValidationInputKind.USE_CASE_MANIFEST], encoding="utf-8"
        )
        res = _run(str(path))
        assert res.exit_code == 0, res.output
        assert "use-case manifest validation" in res.output

    def test_a_build_output_directory_validates(self, tmp_path: Path) -> None:
        directory = tmp_path / "abicheck-build"
        directory.mkdir()
        (directory / "build-output.json").write_text(
            json.dumps({"schema": BUILD_OUTPUT_SCHEMA, "targets": []}),
            encoding="utf-8",
        )
        res = _run(str(directory))
        assert res.exit_code == 0, res.output
        assert "build-output validation" in res.output

    def test_an_empty_document_names_every_reading(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.yml"
        path.write_text("", encoding="utf-8")
        res = _run(str(path))
        assert res.exit_code == 0, res.output
        # Both readings survive: the project-config validation actually
        # runs (its "no targets declared" warning is the reason to run it
        # at all) and the manifest reading is stated beside it.
        assert "project validation" in res.output
        assert "warning(s)" in res.output
        assert "0 use case" in res.output

    def test_an_unrecognizable_input_is_a_usage_error(self, tmp_path: Path) -> None:
        path = tmp_path / "scalar.yml"
        path.write_text("42", encoding="utf-8")
        res = _run(str(path))
        assert res.exit_code == 64
        assert "not a recognized project-integration document" in res.output

    def test_toolchain_bindings_outside_a_project_config_is_a_usage_error(
        self, tmp_path: Path
    ) -> None:
        # Recognizing a document never authorizes anything: the bindings
        # file stays an explicit operand of the one validation that uses
        # it, rather than being silently ignored elsewhere.
        manifest = tmp_path / "uc.yml"
        manifest.write_text(
            DOCUMENTS[ValidationInputKind.USE_CASE_MANIFEST], encoding="utf-8"
        )
        bindings = tmp_path / "bindings.json"
        bindings.write_text("{}", encoding="utf-8")
        res = _run(str(manifest), "--toolchain-bindings", str(bindings))
        assert res.exit_code == 64
        assert "--toolchain-bindings" in res.output

    @pytest.mark.parametrize("retired", ["validate-build", "validate-use-cases"])
    def test_the_retired_spellings_are_gone(self, tmp_path: Path, retired: str) -> None:
        res = CliRunner().invoke(main, ["project", retired, str(tmp_path)])
        assert res.exit_code == 64


class TestNameIndependenceEndToEnd:
    """The whole invocation, not just the classification step.

    This class exists because proving the classifier is name-independent
    proved nothing about the pipeline behind it: the first revision
    resolved an explicitly named manifest to its *parent directory*, and
    `validate_build_output` then looked for the conventional
    `build-output.json` beside it — so a valid `run-42.json` exited 64
    while every classifier test passed (CodeRabbit review, PR #1242).
    Registered as `cli_surface.name_independent_dispatch_undone_downstream`
    in `tests/regressions/manifest.py`.

    The oracle is the document's content, stated independently: a
    well-formed document of each kind validates (exit 0) under *any*
    name, and reaches the validator its content names.
    """

    #: The header each kind's validator prints — the evidence that the
    #: right one ran, not merely that something exited 0.
    EXPECTED_HEADER = {
        ValidationInputKind.PROJECT_CONFIG: "project validation",
        ValidationInputKind.USE_CASE_MANIFEST: "use-case manifest validation",
        ValidationInputKind.BUILD_OUTPUT: "build-output validation",
        ValidationInputKind.EMPTY_DOCUMENT: "project validation",
    }

    @pytest.mark.parametrize(
        ("kind", "name"), list(itertools.product(DOCUMENTS, MISLEADING_NAMES))
    )
    def test_a_valid_document_validates_under_any_name(
        self, tmp_path: Path, kind: ValidationInputKind, name: str
    ) -> None:
        path = tmp_path / name
        path.write_text(DOCUMENTS[kind], encoding="utf-8")
        res = _run(str(path))
        assert res.exit_code == 0, res.output
        assert self.EXPECTED_HEADER[kind] in res.output

    @pytest.mark.parametrize("name", MISLEADING_NAMES)
    def test_a_named_manifest_resolves_its_own_artifacts(
        self, tmp_path: Path, name: str
    ) -> None:
        # The parent-directory substitution also decided where relative
        # artifact paths resolve from, so the fix has to keep that right:
        # the root is the directory holding the manifest, whatever the
        # manifest is called.
        directory = tmp_path / "out"
        directory.mkdir()
        binary = directory / "libfoo.so"
        binary.write_bytes(b"\x7fELF fake")
        (directory / name).write_text(
            json.dumps(
                {
                    "schema": BUILD_OUTPUT_SCHEMA,
                    "targets": [{"id": "foo", "binary": "libfoo.so"}],
                }
            ),
            encoding="utf-8",
        )
        res = _run(str(directory / name))
        # The binary resolves (no "does not exist" error); whatever else
        # the validator reports about it is not this test's subject.
        assert "does not exist" not in res.output, res.output


class TestDispatchConsequences:
    """Behavior changes the shape dispatch forces — moved here from
    ``test_project_targets.py``, whose subject is the config parser rather
    than which validator an operand resolves to."""

    def test_a_list_named_like_a_config_is_read_as_a_manifest(
        self, tmp_path: Path
    ) -> None:
        config_path = tmp_path / ".abicheck.yml"
        config_path.write_text("- just\n- a\n- list\n")
        result = _run(str(config_path))
        assert result.exit_code == 64, result.output
        # Phase 7p dispatches on shape, not filename: a top-level list is read
        # as a use-case manifest and still rejected (its entries are strings).
        assert "impact-use-cases" in result.output.lower()
