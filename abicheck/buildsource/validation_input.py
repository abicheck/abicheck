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

"""Which validator does one ``project validate INPUT`` operand want?

The dispatch behind the single ``project validate`` command (plan
`one-comparison-product.md` Phase 7p): ``project validate``,
``validate-build`` and ``validate-use-cases`` were one question — "is this
project-integration document well formed?" — asked over three input
schemas, each with its own subcommand and its own copy of
``--format``/``-o``/``-v``.

Two rules govern this dispatch, and both exist because the obvious
implementation violates them:

1. **Dispatch on a validated schema discriminator or a recognized
   directory contract — never on a filename.** ``build-output.json``,
   ``.abicheck.yml`` and ``impact-use-cases.yaml`` are conventions, not
   contracts: a caller may hold any of the three under any name (a CI job
   writing ``artifacts/run-42.json``, a repository with several candidate
   configs), and a name-based guess would route it to the wrong validator
   and report confident nonsense about a document of a different kind.
   Every branch below reads the document's own shape.
2. **Recognizing a document never authorizes anything.** Classification
   answers *which validator*, nothing else. ``--toolchain-bindings``
   stays an explicit, separately-supplied operand precisely because an
   untrusted project config must not be able to nominate the executables
   its own validation probes (``ProfileCompileSpec.binding``'s trust
   boundary).

The three shapes are mutually exclusive by construction, which is what
makes a discriminator possible at all:

* a **directory** is a build output, and only when it carries a
  ``build-output.json`` declaring ``schema: abicheck.build-output/v1``
  (:func:`~abicheck.buildsource.build_output.is_build_output_dir`, the
  same contract every other build-output consumer routes on);
* a **sequence** document is a use-case manifest — the only one of the
  three whose top level is a list;
* an **empty** document is vacuously valid under all three, and is
  reported as such rather than assigned to one of them;
* a **mapping** is a build-output manifest when it carries the explicit
  ``schema:`` discriminator, and a project config otherwise. Project
  configs are the one shape with no self-describing tag, so they are the
  mapping default rather than a key sniff: ``targets:``/``bundles:``/
  ``profiles:``/``baseline:`` are all optional in a config that is
  otherwise legitimately empty, and demanding one would reject a document
  today's ``project validate`` accepts.
"""

from __future__ import annotations

import enum
from pathlib import Path
from typing import Any

from .build_output import (
    BUILD_OUTPUT_MANIFEST_NAME,
    BUILD_OUTPUT_SCHEMA,
    is_build_output_dir,
)


class ValidationInputKind(enum.Enum):
    """Which validator a ``project validate`` operand resolves to."""

    EMPTY_DOCUMENT = "empty-document"
    PROJECT_CONFIG = "project-config"
    BUILD_OUTPUT = "build-output"
    USE_CASE_MANIFEST = "use-case-manifest"


class ValidationInputError(ValueError):
    """*INPUT* is not a recognizable project-integration document."""


def classify_validation_input(path: Path) -> tuple[ValidationInputKind, Path]:
    """Classify *path*, returning its kind and the path its validator wants.

    The second element is the path that kind's validator should be given.
    It is *path* itself in every case today; it stays in the return type
    because the two build-output spellings (a directory, or a manifest
    file under any name) are both legal operands and a future kind may
    need the same freedom.

    Raises :class:`ValidationInputError` with a message naming what was
    found and what the three recognized shapes are. It never raises for a
    *valid-but-failing* document: "malformed" is the validator's answer,
    not this function's.
    """
    if path.is_dir():
        if is_build_output_dir(path):
            return ValidationInputKind.BUILD_OUTPUT, path
        raise ValidationInputError(
            f"{path} is a directory but not an abicheck-build/ build output: "
            f"expected a {BUILD_OUTPUT_MANIFEST_NAME} declaring "
            f"schema: {BUILD_OUTPUT_SCHEMA}."
        )

    document = _parse_document(path)

    if document is None:
        # An empty document is the one genuinely ambiguous shape: an empty
        # mapping and an empty list are indistinguishable in YAML, and
        # *both* superseded commands accepted one as valid (an empty
        # ``.abicheck.yml`` declaring no targets; an empty manifest
        # declaring no use cases). It is therefore reported as its own
        # kind rather than silently assigned to one validator — picking
        # either would answer a question the document does not settle,
        # and rejecting it would lose a capability both old commands had.
        return ValidationInputKind.EMPTY_DOCUMENT, path
    if isinstance(document, list):
        return ValidationInputKind.USE_CASE_MANIFEST, path
    if isinstance(document, dict):
        if document.get("schema") == BUILD_OUTPUT_SCHEMA:
            # A build-output manifest named directly, under any name. The
            # path is passed through rather than replaced by its parent:
            # substituting the directory makes the validator look for the
            # conventional ``build-output.json`` beside it, which turns a
            # valid ``artifacts/run-42.json`` into "no manifest here" —
            # a filename dependency re-introduced one layer down from the
            # dispatch that just refused to make one.
            return ValidationInputKind.BUILD_OUTPUT, path
        return ValidationInputKind.PROJECT_CONFIG, path

    raise ValidationInputError(
        f"{path} is not a recognized project-integration document "
        f"(found {_describe(document)}). Expected a project config (a YAML "
        f"mapping), a use-case manifest (a YAML list), or a build output (a "
        f"directory, or a {BUILD_OUTPUT_MANIFEST_NAME} declaring "
        f"schema: {BUILD_OUTPUT_SCHEMA})."
    )


def _parse_document(path: Path) -> Any:
    """Parse *path* as YAML (which subsumes the JSON manifest shape).

    Only the document's top-level shape is read here; every validator
    re-parses under its own strict loader, so a permissive read at this
    step cannot let a malformed document through as valid.
    """
    import yaml

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValidationInputError(f"cannot read {path}: {exc}") from exc
    try:
        return yaml.safe_load(text)
    except (yaml.YAMLError, ValueError) as exc:
        raise ValidationInputError(f"cannot parse {path}: {exc}") from exc


def _describe(document: Any) -> str:
    """Name the shape a document turned out to have, for the error message.

    ``None`` never reaches here — an empty document is its own kind — so
    only the genuinely unroutable shapes (a scalar) are described.
    """
    return f"a {type(document).__name__}"
