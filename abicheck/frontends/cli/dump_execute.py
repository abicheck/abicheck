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

"""``dump_cmd``'s real ELF execution, via the shared typed pipeline.

CLI cleanup phase two, PR C (PR 3A). Split out of
``frontends/cli/commands/dump.py`` purely to stay under the architecture
gate's 800-line production file cap -- same reason ``cli_dump_request.py``/
``cli_dump_non_elf.py``/``cli_dump_dry_run_build_query.py`` are their own
modules rather than growing an already-large sibling. Lives under
``frontends/cli/`` itself (alongside ``runtime.py``/``artifact_set_dry_run.py``)
rather than as a new flat ``cli_*.py`` root module: ADR-061's
``architecture/modules.yaml`` freezes that root family's member list, so a
genuinely new module goes into its responsibility-package tree instead.

Deliberately a thin wrapper with no import back into the CLI-registration
family (``cli_resolve``, ``cli_buildsource``, ...): those already form an
accepted, by-design import cycle (``IMPORT_CYCLE_ALLOWLIST`` in
``scripts/check_ai_readiness.py``) that ``frontends.cli.commands.dump``
itself already sits in, but a *new* module joining that cycle is flagged as
unapproved growth (``import-cycle-growth``) even when it is simply another
member of the same already-accepted family -- adding one is a maintainer
decision, not a side effect of a routine split. So the caller
(``dump_cmd``) does its own ``ResolvedDumpRequest`` re-resolution (which
needs ``cli_buildsource.resolve_dump_request_for_cli``, already one of its
existing imports) and passes the *already-resolved* execution plan and a
*notify* callable in, rather than this module reaching back for either
itself.

No behavior change of its own: this is the real-run half of ``dump_cmd``
that used to call ``perform_elf_dump`` directly, now calling
``execute_dump_request`` instead -- see
``docs/contribute/plans/cli-cleanup-phase-two.md``'s PR C section ("Slice
landed: the real ELF run is migrated") for the full account of what changed
and why, and ``docs/contribute/known-gaps.md``'s "PR C" entry for the
precise mechanism.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from ...errors import AbicheckError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ...model import AbiSnapshot
    from ...service_dump_pipeline import ResolvedDumpRequest

__all__ = ["execute_dump_cli_run"]


def execute_dump_cli_run(
    exec_resolved: ResolvedDumpRequest,
    *,
    notify: Callable[[str], None],
    build_config: Path | None,
    build_query: str | None,
    build_compile_db: str | None,
    allow_build_query: bool,
    legacy_compile_db_tokens: tuple[str, ...],
    legacy_compile_db_matched: bool,
) -> AbiSnapshot:
    """Run the real ELF ``dump`` extraction and return its snapshot.

    *exec_resolved* is the caller's own execution-only
    :class:`~abicheck.service_dump_pipeline.ResolvedDumpRequest` -- built by
    the caller (``dump_cmd``) from the same
    :class:`~abicheck.api_types.DumpRequest` ``--dry-run`` already resolved,
    but re-pointed at the *normalized* ``so_path`` (following a GNU ld
    linker script / dev symlink: ``resolve_dump_request``'s own
    ``detect_binary_format(side.path)`` call runs before any such
    following, so feeding it the pre-follow path risks a wrong ``fmt`` for
    a symlink-to-linker-script input) and with ``requested_depth`` nulled
    out.

    That null-out matters here too, not just at the call site that does
    it: the shared pipeline's own depth gate
    (:func:`~abicheck.service_dump_pipeline.execute_dump_request`'s
    ``enforce_requested_depth``) raises a ``ValidationError`` worded
    differently than ``cli_dump_helpers.check_requested_depth_satisfied``'s
    ``DumpDepthNotSatisfiedError`` -- both enforce the identical rule (the
    same ``evidence_depth.gated_source_label``/``depth_rank`` primitives),
    so calling both would be redundant, not wrong, but it would change the
    *type* and *text* of the CLI-visible error for this case
    (``tests/test_depth_vocabulary.py`` pins the exact
    ``DumpDepthNotSatisfiedError`` text). With ``requested_depth`` already
    ``None`` on *exec_resolved*, that gate never fires here; the caller's
    own ``_write_snapshot_output`` call stays the sole enforcement point,
    exactly as it already is today.

    *legacy_compile_db_tokens*/*legacy_compile_db_matched* (ADR-063 Phase
    1): the legacy ``-p``/``--compile-db`` auto-match's own derived
    signal, which has no equivalent inside the shared pipeline's typed
    ``InputSpec`` -- threaded through as an explicit pass-through
    (``execute_dump_request``'s own docstring states the precedence rule:
    the P0.3 fold's own result wins whenever it applies) rather than a new
    typed field, so the migrated real run keeps seeing it exactly like
    ``perform_elf_dump`` did via its own ``legacy_build_context_flags``
    parameter.

    *notify*: ``perform_elf_dump`` used ``click.echo(..., err=True)``
    directly for every user-facing progress note along this path; the
    shared pipeline's own default (no ``notify`` -- log instead) would
    silently move these onto the logger, which a plain ``dump`` invocation
    with no ``-v``/logging configured never surfaces -- and could,
    depending on the logging configuration in effect, land on stdout and
    corrupt ``dump``'s own JSON-to-stdout output. The caller passes
    ``cli_resolve._click_notify``, ``compare``'s own CLI convention for the
    identical pipeline, reused verbatim rather than invented a second time.

    Raises:
        click.ClickException: If extraction fails (mirrors
            ``perform_elf_dump``'s own ``except`` clause exactly).
    """
    from ...service_dump_pipeline import execute_dump_request

    try:
        result = execute_dump_request(
            exec_resolved,
            notify=notify,
            build_config=build_config,
            build_query=build_query,
            build_compile_db=build_compile_db,
            allow_build_query=allow_build_query,
            legacy_compile_db_tokens=legacy_compile_db_tokens,
            legacy_compile_db_matched=legacy_compile_db_matched,
        )
    except (AbicheckError, RuntimeError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    # `execute_dump_request` already ran the dependency walk itself
    # (`DumpRequest.follow_dependencies`, set from the CLI's own
    # `--follow-deps`) -- a second call by the caller would double it.
    return result.snapshot
