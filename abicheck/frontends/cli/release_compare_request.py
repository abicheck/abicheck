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

"""The CLI boundary for the release request/plan -- delegation only.

The request, the plan and the resolution moved to
:mod:`abicheck.workflows.release_request` (ADR-061 gap D, closed), together
with the input-resolution chain they call
(:mod:`abicheck.workflows.release_inputs`). See that module's docstring for
why they began here and what moving them closed.

What is left here is what a front end owes and an engine must not do: turning
a typed :class:`~abicheck.errors.ReleaseOperandError` into a
``click.UsageError``, so a malformed operand still exits ``64`` with the
same message it always did, and rejecting a ``--bundle-facts-out`` path that
would overwrite an export the same run writes. Everything else is re-exported
unchanged, so every existing importer of this module -- including
``cli_compare_release.compare_release_cmd`` and this module's own tests -- is
unaffected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from ...errors import (
    AmbiguousLibraryMatchError,
    ReleaseOperandContentError,
    ReleaseOperandUsageError,
)
from ...workflows.release_compare import execute_release_compare
from ...workflows.release_request import (
    ReleaseComparePlan as ReleaseComparePlan,
    ReleaseCompareRequest as ReleaseCompareRequest,
    cleanup_release_compare_plan as cleanup_release_compare_plan,
    resolve_release_compare_plan as _resolve_release_compare_plan,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ...workflows.release_compare import (
        ReleaseCompareOptions,
        ReleaseCompareOutcome,
    )
    from .release_member_reporter import CliReleaseReporter

__all__ = [
    "ReleaseComparePlan",
    "execute_release_compare_checked",
    "ReleaseCompareRequest",
    "cleanup_release_compare_plan",
    "reject_bundle_facts_out_collision",
    "reject_bundle_facts_out_dir_collision",
    "resolve_release_compare_plan",
]


def resolve_release_compare_plan(
    request: ReleaseCompareRequest,
    *,
    make_temp_dir: Callable[[str], Path] | None = None,
) -> ReleaseComparePlan:
    """:func:`abicheck.workflows.release_request.resolve_release_compare_plan`,
    with its typed operand error translated for a Click caller.

    The *only* difference from the engine function: its typed operand
    errors become the ``click`` types that produce the exit codes this
    command has always produced, with the identical messages.

    * :class:`~abicheck.errors.ReleaseOperandUsageError` -- an ambiguous or
      unselectable stored-package variant, i.e. a fact about what the caller
      asked for -- becomes a ``click.UsageError``, exit ``64``.
    * :class:`~abicheck.errors.ReleaseOperandContentError` -- an
      unrecognized package format, a directory holding nothing readable --
      becomes a ``click.ClickException``, exit ``1``.

    Those two exit codes were already distinct before the resolution moved
    engine-side, so both are translated here rather than collapsed: mapping
    everything to ``UsageError`` turned every content error into a usage
    error (``64``), and catching only the usage family let a content error
    escape as an unhandled exception -- still exit ``1``, but with no message
    at all. Both were observed while this moved, which is why the split is
    stated in the error hierarchy instead of left to whichever ``click`` type
    each raise site historically used.

    :class:`~abicheck.errors.AmbiguousLibraryMatchError` is translated the
    same way, for the same reason: the resolution reaches
    ``binary_utils.build_match_map`` -- the pure primitive -- directly now,
    rather than through a Click-translating wrapper,
    so this is the boundary that owes its message. Without it a stale
    ``.gz``/``.zst`` sibling next to a plain snapshot exited ``1`` with no
    output instead of naming the tie it refused to break.

    A Python caller reaching the engine function directly -- through
    :func:`abicheck.service.resolve_release_compare` -- gets the typed
    errors instead, which is the whole point of the split.
    """
    try:
        return _resolve_release_compare_plan(request, make_temp_dir=make_temp_dir)
    except ReleaseOperandUsageError as exc:
        raise click.UsageError(str(exc)) from exc
    except (ReleaseOperandContentError, AmbiguousLibraryMatchError) as exc:
        raise click.ClickException(str(exc)) from exc


def execute_release_compare_checked(
    plan: ReleaseComparePlan,
    options: ReleaseCompareOptions,
    *,
    reporter: CliReleaseReporter,
) -> ReleaseCompareOutcome:
    """:func:`abicheck.workflows.release_compare.execute_release_compare`,
    with its typed operand errors (a bad ``--manifest``, an undecodable
    embedded manifest, an unwritable ``--bundle-facts-out``, a one-sided
    probe matrix) turned into the ``click`` types -- and so the exit codes
    and messages -- this command always produced."""
    try:
        return execute_release_compare(plan, options, reporter=reporter)
    except ReleaseOperandUsageError as exc:
        raise click.UsageError(str(exc)) from exc
    except ReleaseOperandContentError as exc:
        raise click.ClickException(str(exc)) from exc


def reject_bundle_facts_out_collision(
    bundle_facts_out: Path | None,
    output: Path | None,
    *secondary_outputs: Path | None,
) -> None:
    """Reject ``--bundle-facts-out`` naming the same file as ``--output``/
    ``-o`` (G38 Phase 2).

    A command-specific extra check, deliberately not
    ``reject_incoherent_secondary_output()``'s job (see that leaf module's
    own docstring) -- without it, ``--bundle-facts-out result.json --output
    result.json`` silently overwrites the requested baseline with the
    report while still reporting success (Codex review).

    *secondary_outputs* is variadic because ``-o`` is repeatable: every
    requested artifact's PATH is checked, not just the first. Checking one
    let ``-o json=a.json -o markdown=b.md --bundle-facts-out
    b.md`` through.
    """
    if bundle_facts_out is None:
        return
    for label, other in (
        ("--output/-o", output),
        *(("--write", out) for out in secondary_outputs),
    ):
        if other is not None and bundle_facts_out.resolve() == other.resolve():
            raise click.UsageError(
                f"--bundle-facts-out's PATH must differ from {label}: writing "
                "both to the same file would silently overwrite the "
                "requested bundle-facts baseline with the report."
            )


def reject_bundle_facts_out_dir_collision(
    bundle_facts_out: Path | None,
    output_dir: Path | None,
    old_map: dict[str, Path],
) -> None:
    """Reject ``--bundle-facts-out`` naming a path a per-component export will
    also write (G38 Phase 2, Codex review, fresh evidence).

    ``reject_bundle_facts_out_collision()`` above only knows about
    the export set -- it can't see a per-component export's own
    ``summary.json`` or per-library ``<stem>.json`` files, since those
    paths depend on *output_dir* and (for the per-library case) the
    resolved OLD-side library map, neither known at that earlier
    validation point. Called once ``old_map`` is resolved, before
    ``output_dir`` is created or anything is written into it.
    """
    if bundle_facts_out is None or output_dir is None:
        return
    resolved = bundle_facts_out.resolve()
    summary_path = output_dir / "summary.json"
    if resolved == summary_path.resolve():
        raise click.UsageError(
            "--bundle-facts-out's PATH must differ from the per-component "
            "export's own summary.json: writing both to the same file would silently "
            "overwrite the requested bundle-facts baseline with the "
            "per-library summary report."
        )
    for name, old_path in old_map.items():
        lib_path = output_dir / f"{old_path.stem}.json"
        if resolved == lib_path.resolve():
            raise click.UsageError(
                f"--bundle-facts-out's PATH must differ from the per-component "
                f"export's own report for {name!r} ({lib_path}): writing "
                "both to the same file would silently overwrite whichever "
                "was written second."
            )
