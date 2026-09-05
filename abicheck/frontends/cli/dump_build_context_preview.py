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

"""``dump --dry-run``'s legacy compile-db flags preview and its own
"Execution options" report section.

ADR-063 Track T4 ("Dump request contract") follow-up. A genuinely new
module rather than an addition to ``cli_helpers_compare.py`` (its own
``no_growth`` debt entry, ``architecture/debt.yaml``) or ``cli_dump_helpers.py``
(same) -- both are already at or over their adoption baseline, with zero
headroom for either this preview function or the report section it feeds.
Per ``abicheck/CLAUDE.md``'s "New code goes to its ADR-061 target owner,
not the flat legacy namespace", a new module belongs in ``frontends/cli/``
rather than a new flat ``cli_*.py`` root module (``ADR-061``'s
``architecture/modules.yaml`` freezes that root family's member list) --
see ``frontends/cli/dump_execute.py``'s own docstring for the identical
reasoning. :func:`add_execution_options_dry_run_section` mirrors
``cli_dump_dry_run_build_query.add_build_query_dry_run_section``'s shape
(a plain function appending its own section onto an already-built
:class:`~abicheck.dry_run.DryRunResult`) for the identical reason: the
section it renders belongs next to ``render_dump_dry_run``'s own report
construction, but that module has no line budget left to build it inline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from pathlib import Path

    from ...dry_run import DryRunResult
    from ...service_dump_pipeline import ResolvedDumpRequest

__all__ = [
    "add_execution_options_dry_run_section",
    "dry_run_build_context_preview",
]


def dry_run_build_context_preview(
    compile_db_path: Path | None,
    headers: tuple[Path, ...],
    compile_db_filter: str | None,
) -> tuple[list[str], bool] | None:
    """Silent, non-raising sibling of
    ``cli_helpers_compare._resolve_build_context_flags`` that also returns
    the derived castxml flags themselves -- ``dump --dry-run``'s
    :class:`~abicheck.service_dump_pipeline.DumpExecutionOptions` preview.

    Same relationship to ``_resolve_build_context_flags`` that
    ``cli_helpers_compare.dry_run_compile_db_matched`` already has (loading
    and matching a compile database is cheap, deterministic, read-only
    resolution, so a dry run may perform it) -- extended to return ``flags``
    too, since a dry run reporting ``legacy_compile_db_tokens`` needs the
    actual list, not just the match verdict. Never echoes to stderr and
    never raises: an unreadable/malformed compile database folds to
    ``([], False)`` rather than the ``click.ClickException`` the real run
    would raise -- the same accepted imprecision
    ``dry_run_compile_db_matched`` already documents for the identical
    failure shape.

    Returns ``None`` when no compile database was given at all (the ``dump``
    CLI's dry-run preview reads this as "no legacy compile-db flags to
    show", the same as an execution that never threads any).
    """
    if not compile_db_path:
        return None
    from ...cli_helpers_compare import _matched_build_context
    from ...errors import AbicheckError

    try:
        ctx, _entry_count = _matched_build_context(
            compile_db_path, headers, compile_db_filter
        )
        return ctx.to_castxml_flags(), ctx.compile_db_path is not None
    except (AbicheckError, OSError, ValueError, click.ClickException):
        return [], False


def add_execution_options_dry_run_section(
    result: DryRunResult, resolved: ResolvedDumpRequest
) -> None:
    """Append the nine execution-option values `execute_dump_request` would
    receive to *result*, when the caller resolved them
    (``resolved.execution_options`` -- see
    ``frontends.cli.commands.dump.dump_cmd``'s own attach step). A no-op
    when unset, same as every other optional section
    ``render_dump_dry_run`` builds -- an older caller that resolved no
    ``execution_options`` sees the unchanged report.
    """
    opts = resolved.execution_options
    if opts is None:
        return
    result.add(
        "Execution options",
        f"build config: {opts.build_config}" if opts.build_config else None,
        f"allow build query: {opts.allow_build_query}",
        f"legacy compile-db flags: {len(opts.legacy_compile_db_tokens)} "
        f"derived ({'matched' if opts.legacy_compile_db_matched else 'no match'})"
        if opts.legacy_compile_db_tokens or opts.legacy_compile_db_matched
        else None,
        f"seed collect mode: {opts.seed_collect_mode}"
        if opts.seed_collect_mode
        else None,
        f"source frontend from folded context: "
        f"{opts.source_frontend_from_folded_context}",
    )
