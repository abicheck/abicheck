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

"""Resolving `compare`'s default export format against its operand shape.

`compare`'s human default is the bounded terminal projection, but several
of its operand shapes render only a restricted set of formats and validate
the *resolved* format rather than the requested one. For those, a default
outside the set does not degrade the output -- it makes every invocation of
that shape a usage error before any comparison runs.

Its own module rather than another block inside `compare.py` (already at its
`architecture/debt.yaml` no-growth baseline): the decision is a small, pure,
separately-testable rule over one export set and two operand paths, and
`compare.py`'s job is to dispatch, not to own it. The restricted sets
themselves stay with the checks that enforce them (`_RELEASE_FORMATS`,
`STORED_BUNDLE_FACTS_FORMATS`) and are passed in, so this module cannot
form a second opinion about which formats exist.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..options.export import ExportSet


#: What an unrenderable default falls back to. Markdown for both restricted
#: paths, which produce the detailed report and have no bounded single-
#: comparison digest of their own to render.
FALLBACK_FORMAT = "markdown"


def renderable_formats(
    old_input: str | Path | None,
    new_input: str | Path | None,
    *,
    release_formats: frozenset[str],
    stored_formats: frozenset[str],
) -> frozenset[str] | None:
    """The formats the dispatch selected by these operands can render.

    ``None`` means the ordinary single-pair path, which renders every format
    `compare` offers and so constrains nothing.
    """
    from ....cli_resolve import classify_compare_operand
    from .compare_bundle_operand_dispatch import resolve_bundle_compare_dispatch

    if old_input is not None and new_input is not None:
        dispatch = resolve_bundle_compare_dispatch(Path(old_input), Path(new_input))
        if dispatch.old_is_stored:
            return stored_formats
    for value in (old_input, new_input):
        if value is not None and classify_compare_operand(Path(value)) in {
            "directory",
            "package",
        }:
            return release_formats
    return None


def resolve_export_set(
    exports: ExportSet,
    *,
    renderable: frozenset[str] | None,
    rewrite_all: bool,
) -> ExportSet:
    """*exports*, with unrenderable **untyped** targets moved to Markdown.

    Decided per *target*, not per export set. ``ExportSet.explicit`` is true
    as soon as the user typed any ``-o``, but a directory-only export
    (``-o json=out/``) still carries a document target this command inserted
    on its own (``build_export_set``, ``spelling=""``) holding the command
    default -- so a set-level test both rewrote formats the user really did
    ask for and left that inserted one stranded. An empty ``spelling`` is
    exactly "the user never typed this target".

    Rewriting a target the user *did* type would be worse than the usage
    error it replaces: it renders a different artifact than the one asked
    for, into the destination the user named.

    *rewrite_all* is the pre-existing non-`full` view / `--no-baseline`
    case, where the bounded projection does not apply at all regardless of
    which formats the path can render.
    """
    from ..options.export import ExportSet as _ExportSet, ExportTarget

    def _needs_rewrite(target: ExportTarget) -> bool:
        if target.spelling:
            return False
        if rewrite_all and renderable is None:
            return True
        return renderable is not None and target.fmt not in renderable

    rewritten = tuple(
        ExportTarget(
            fmt=FALLBACK_FORMAT,
            destination=target.destination,
            is_directory=target.is_directory,
            spelling=target.spelling,
        )
        if _needs_rewrite(target)
        else target
        for target in exports.targets
    )
    if rewritten == exports.targets:
        return exports
    return _ExportSet(targets=rewritten, explicit=exports.explicit)
