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

"""CLI-only guards for the ``compare-release`` command.

Two checks reject a ``--bundle-facts-out`` path that would collide with an
export the same run writes, and one tears down the temp directories a run
allocated. Everything else a release run decides is owned elsewhere: the
compare owner (:mod:`abicheck.workflows.release_compare`), input resolution
(:mod:`abicheck.workflows.release_inputs`), ADR-064's gate resolution
(:mod:`abicheck.policy.release_gate_options`), and the release report
(:mod:`abicheck.report.release_document`, rendered by
:mod:`abicheck.report.render_release_report`).

Leaf module: it must not import from :mod:`abicheck.cli` or
:mod:`abicheck.cli_compare_release`. It re-exports nothing; a name that moved
is imported from its owner.
"""

from __future__ import annotations

from pathlib import Path

import click


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


def _cleanup_temp_dirs(temp_dir_paths: list[str]) -> None:
    """Remove temporary directories created during package extraction.

    Always cleans up now -- ``--keep-extracted`` is gone (Phase 7d,
    ADR-068 D5), no replacement."""
    import shutil as _shutil

    for td_path in temp_dir_paths:
        _shutil.rmtree(td_path, ignore_errors=True)
