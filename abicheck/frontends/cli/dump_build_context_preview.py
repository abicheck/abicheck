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

"""``dump --dry-run``'s "Execution options" report section (including what
the pipeline's compile-database match would derive).

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

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ...dry_run import DryRunResult
    from ...workflows.dump.pipeline import DumpExecutionOptions, ResolvedDumpRequest

__all__ = [
    "add_execution_options_dry_run_section",
    "add_ownership_dry_run_section",
]


def _compile_db_line(
    opts: DumpExecutionOptions, resolved: ResolvedDumpRequest
) -> str | None:
    """What the pipeline's compile-database match would derive, computed by the
    same :func:`~abicheck.workflows.artifact.compile_db_match.try_match_compile_db`
    the dry run's depth check uses (read-only, never raises)."""
    from ...workflows.artifact.compile_db_match import try_match_compile_db

    match = try_match_compile_db(
        opts.compile_db, resolved.request.input.headers, opts.compile_db_filter
    )
    if match is None:
        return None
    verdict = "matched" if match.matched else "no match"
    return f"compile-db flags: {len(match.tokens)} derived ({verdict})"


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
        _compile_db_line(opts, resolved),
        f"seed collect mode: {opts.seed_collect_mode}"
        if opts.seed_collect_mode
        else None,
        f"source frontend from folded context: "
        f"{opts.source_frontend_from_folded_context}",
    )


def add_ownership_dry_run_section(
    result: DryRunResult, config_path: Path | None, headers: Sequence[Path]
) -> None:
    """The ownership rules a real dump would apply, and each ``-H`` header's
    owner and contract under them. Silent when no ownership key is set."""
    from ...workflows.ownership_preview import is_unexpected, ownership_preview

    preview = ownership_preview(config_path, headers)
    if preview is None:
        return
    rules = preview.rules
    result.add(
        "Ownership",
        f"project root: {preview.project_root}",
        *(f"target root: {r}" for r in rules.target_roots),
        *(
            f"dependency {d.name}: {', '.join(d.header_roots)}"
            for d in rules.dependencies
        ),
        *(f"private header: {p}" for p in rules.private_headers),
        *(f"private namespace: {n}" for n in rules.private_namespaces),
        *(
            f"{h}: owner={d.owner} contract={d.contract} ({d.rule_id})"
            for h, d in preview.headers
        ),
        "per-declaration ownership needs a parse; not shown by --dry-run",
    )
    if preview.error is not None:
        result.block(f"scope ownership rules: {preview.error}")
    for header, decision in preview.headers:
        if is_unexpected(decision):
            result.warn(
                f"-H {header} is owner={decision.owner} contract={decision.contract}, "
                "not public target API"
            )
