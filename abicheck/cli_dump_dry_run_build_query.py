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

"""``dump --dry-run``'s ``build.query`` trust/execution report.

Split into its own leaf module rather than added inline to
``cli_dump_helpers.py``, which sits at its 2000-line AI-readiness hard cap
(root ``AGENTS.md`` "Files that are large" already flags it).

Closes CLI cleanup phase two's PR 3C prerequisite 3
(``docs/contribute/plans/cli-cleanup-phase-two.md``): "``dump --dry-run``
prints the exact argv, the cwd, the resulting compile-DB path, and why the
query will or will not run." Prerequisites 1 and 2 (only an explicit
``--config``/``--build-query`` authorizes executing ``build.query``; an
auto-discovered ``.abicheck.yml`` never does) were already implemented
(ADR-032 D5) -- this module only adds the missing dry-run *visibility* into
that already-enforced trust decision, mirroring it read-only rather than
re-deciding it. It never invokes the query -- resolution only, matching
every other ``dump --dry-run`` section's contract.

The trust rule mirrored here is ``cli_buildsource.embed_build_source``'s own
(``cfg_trusted_for_query = build_config is not None or build_query is not
None``) and the command/cwd construction is ``buildsource.inline.
_run_build_query``'s own (``shlex.split(cfg.query)``, cwd = the ``--sources``
tree when it is a directory) -- kept in sync by reading, not importing,
since neither of those is a public, dry-run-safe entry point.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .dry_run import DryRunResult

_SECTION = "Build query (trust)"


def add_build_query_dry_run_section(
    result: DryRunResult,
    *,
    sources: Path | None,
    build_config: Path | None,
    build_query: str | None,
    build_compile_db: str | None,
) -> None:
    """Append the ``build.query`` trust/execution report to *result*."""
    from .buildsource.inline import discover_build_config, load_build_config

    # Same source (source-tree-root-only, no upward walk) `embed_build_source`
    # itself resolves from for this purpose -- distinct from `discover_project_
    # config`'s upward walk, which the rest of this dry-run report already uses
    # for the generic ".abicheck.yml:" info line.
    cfg_path = build_config or discover_build_config(sources)
    trusted = build_config is not None or build_query is not None

    effective_query = build_query
    compile_db_hint: str | None = build_compile_db
    if effective_query is None and cfg_path is not None:
        try:
            cfg = load_build_config(cfg_path)
        except ValueError as exc:
            result.add(_SECTION, f"build.query: could not load {cfg_path}: {exc}")
            return
        effective_query = cfg.query or None
        if compile_db_hint is None:
            compile_db_hint = cfg.compile_db or None

    if not effective_query:
        result.add(_SECTION, "build.query: (none configured)")
        return

    if not trusted:
        result.add(
            _SECTION,
            f"build.query: {effective_query!r} -- will NOT run "
            "(sourced from an auto-discovered .abicheck.yml, which is never "
            "trusted to execute; pass --config to authorize it)",
        )
        return

    try:
        argv = shlex.split(effective_query)
    except ValueError as exc:
        result.add(
            _SECTION,
            f"build.query: {effective_query!r} -- will NOT run "
            f"(could not parse as a command: {exc})",
        )
        return

    trust_source = "explicit --config" if build_config is not None else "explicit --build-query"
    cwd = sources if sources is not None and sources.is_dir() else Path.cwd()
    result.add(
        _SECTION,
        f"build.query: will run (trusted -- {trust_source})",
        f"argv: {argv}",
        f"cwd: {cwd}",
        f"resulting compile-DB path: {compile_db_hint}"
        if compile_db_hint
        else "resulting compile-DB path: (build.compile_db not configured -- "
        "the query's own default output location)",
    )
