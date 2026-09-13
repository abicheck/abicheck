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

"""Which document did ``aggregate --manifest`` just get?

The dispatch behind one ``--manifest`` operand (plan
``one-comparison-product.md`` slice 7q). ``--manifest`` and ``--run-plan``
were two flags for one input — *the set of targets this matrix was supposed
to produce* — differing only in which schema the caller happened to be
holding, and the run-plan half was already projected to the manifest shape
internally (:func:`~abicheck.buildsource.run_plan.to_aggregate_manifest`)
before anything read it. So the projection stays and the second flag goes:
one operand, classified by the document's own content.

Two rules, both carried over from ``project validate``'s own consolidation
(`buildsource/validation_input.py`, slice 7p), because the obvious
implementation violates both:

1. **Dispatch on a validated schema discriminator, never on a filename.**
   ``run-plan.json`` and ``targets.json`` are conventions. A CI job writes
   ``artifacts/plan-42.json``; a repository keeps several candidate
   manifests; a caller pipes either through a temp file with a name of the
   runner's choosing. A name-based guess routes the document to the wrong
   reader and then reports confident nonsense about the wrong kind of
   input. A run plan is the shape carrying a self-describing
   ``schema: abicheck.run-plan/vN`` tag, or — for the untagged plans
   ``RunPlan.from_dict`` has always accepted — the one carrying that
   shape's own required ``checks`` list. An expected-target manifest is the
   mapping default, exactly as a project config is in slice 7p: it has no
   tag of its own, and demanding one would reject every manifest already
   written. A document claiming both structures at once and declaring
   neither schema is rejected rather than guessed at.
2. **Classifying is not validating.** This module answers *which reader*
   and nothing else. Each reader then applies its own full validation
   (version gate, ``gate`` block rules, duplicate target ids), so a
   permissive read here cannot let a malformed document through as valid.

What deliberately did *not* fold in: ``--discovered-only``. It says the
operator has **no** expected inventory, which is a different statement from
any document's contents — inferring it from an empty or absent manifest is
how a CI matrix silently goes green with half its jobs missing.
"""

from __future__ import annotations

import enum
import json
from pathlib import Path
from typing import Any


class ExpectedInputKind(enum.Enum):
    """Which reader an ``aggregate --manifest`` operand resolves to."""

    MANIFEST = "manifest"
    RUN_PLAN = "run-plan"


class ExpectedInputError(ValueError):
    """*MANIFEST* is not a recognizable expected-target document."""


#: The prefix of every ``run-plan.json``'s self-describing ``schema`` tag
#: (``abicheck.run-plan/v1``, ``/v2``, ...). Matched as a prefix, not
#: against one exact version: the version gate is
#: :meth:`~abicheck.buildsource.run_plan.RunPlan.from_dict`'s job, and it
#: fails loudly on a plan newer than this build. Classifying a too-new plan
#: as "not a run plan" would instead route it to the manifest reader and
#: report a missing ``targets`` key.
RUN_PLAN_SCHEMA_PREFIX = "abicheck.run-plan/v"


def classify_expected_input(path: Path) -> tuple[ExpectedInputKind, dict[str, Any]]:
    """Classify *path*, returning its kind and its already-parsed document.

    The parsed document is returned rather than re-read by the caller: both
    readers need it, and a second read is a second chance for the file to
    have changed underneath the classification.

    Raises :class:`ExpectedInputError` for an unreadable, unparseable, or
    non-object document — never for a *valid-but-failing* one, which is the
    reader's answer, not this function's.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ExpectedInputError(f"cannot read {path}: {exc}") from exc
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise ExpectedInputError(f"cannot parse {path}: {exc}") from exc

    if not isinstance(document, dict):
        raise ExpectedInputError(
            f"{path} must contain a JSON object: an expected-target manifest "
            f'({{"targets": [...]}}) or an `abicheck project plan` run-plan '
            f"(declaring schema: {RUN_PLAN_SCHEMA_PREFIX}N), not "
            f"{_describe(document)}."
        )

    schema = document.get("schema")
    if isinstance(schema, str) and schema.startswith(RUN_PLAN_SCHEMA_PREFIX):
        return ExpectedInputKind.RUN_PLAN, document

    # An *untagged* plan is still a real input: `RunPlan.to_dict` has always
    # stamped `schema`, but `RunPlan.from_dict` accepts a document without
    # one (defaulting it to v1), so hand-authored and older plans exist and
    # the retired `--run-plan` flag read them. `checks` is that shape's own
    # required structure, and `targets` is the manifest's, so the two are
    # separable without either tag.
    has_checks = isinstance(document.get("checks"), list)
    has_targets = "targets" in document
    if has_checks and has_targets:
        raise ExpectedInputError(
            f"{path} declares both 'checks' (a run-plan) and 'targets' (an "
            "expected-target manifest) and no 'schema' saying which it is. "
            "Split it, or stamp the run-plan's own "
            f"schema: {RUN_PLAN_SCHEMA_PREFIX}1."
        )
    if has_checks:
        return ExpectedInputKind.RUN_PLAN, document
    return ExpectedInputKind.MANIFEST, document


def _describe(document: Any) -> str:
    """Name the shape a document turned out to have, for the error message."""
    if isinstance(document, list):
        return "a list"
    return f"a {type(document).__name__}"
