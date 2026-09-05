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

"""``dump_cmd``'s real binary execution (ELF, PE, Mach-O), via the shared
typed pipeline.

CLI cleanup phase two, PR C (PR 3A). Split out of
``frontends/cli/commands/dump.py`` purely to stay under the architecture
gate's 800-line production file cap -- same reason ``cli_dump_request.py``/
``cli_dump_dry_run_build_query.py`` are their own modules rather than
growing an already-large sibling. Lives under
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
that used to call ``perform_elf_dump`` (ELF) or ``handle_non_elf_dump``
(PE/Mach-O) directly, now calling ``execute_dump_request`` instead for
either format -- see ``docs/contribute/plans/cli-cleanup-phase-two.md``'s PR
C section ("Slice landed: the real ELF run is migrated", and its later
"PE/Mach-O" addendum for the second half of this same migration) for the
full account of what changed and why, and
``docs/contribute/known-gaps.md``'s "PR C" entry for the precise mechanism.
``execute_dump_cli_run`` itself took no format-specific branch to support
PE/Mach-O -- ``execute_dump_request``/``_resolve_side_snapshot_impl`` were
already format-generic (``fmt``-parameterized) before this migration; only
the *caller* (``dump_cmd``) previously routed PE/Mach-O around this module
entirely, through the separate ``handle_non_elf_dump`` path.
``execute_and_write_dump_cli_run`` below is the tail both formats' real-run
branches in ``commands/dump.py`` share (execute, stamp provenance, write
the snapshot), factored out purely to keep that already-near-cap module
from growing net lines when the PE/Mach-O branch stopped being a single
delegating call to ``handle_non_elf_dump``.
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

__all__ = ["execute_and_write_dump_cli_run", "execute_dump_cli_run"]


def execute_dump_cli_run(
    exec_resolved: ResolvedDumpRequest,
    *,
    notify: Callable[[str], None],
) -> AbiSnapshot:
    """Run the real ``dump`` extraction (ELF, PE, or Mach-O) and return its
    snapshot.

    *exec_resolved* is the caller's own execution-only
    :class:`~abicheck.service_dump_pipeline.ResolvedDumpRequest` -- built by
    the caller (``dump_cmd``) from the same
    :class:`~abicheck.api_types.DumpRequest` ``--dry-run`` already resolved,
    but re-pointed at the *normalized* ``so_path`` (following a GNU ld
    linker script / dev symlink: ``resolve_dump_request``'s own
    ``detect_binary_format(side.path)`` call runs before any such
    following, so feeding it the pre-follow path risks a wrong ``fmt`` for
    a symlink-to-linker-script input), with ``requested_depth`` nulled
    out, and its own :attr:`~abicheck.service_dump_pipeline.
    ResolvedDumpRequest.execution_options` already attached -- the nine
    out-of-band execution kwargs this function used to accept as its own
    separate parameters (ADR-063 Track T4, "Dump request contract";
    :class:`~abicheck.service_dump_pipeline.DumpExecutionOptions` documents
    each field). Folded onto *exec_resolved* itself, not threaded through
    here, so the object the caller resolved is the one thing describing the
    run -- the same reasoning that already governs every other value this
    function reads off *exec_resolved* rather than taking as its own
    parameter. :func:`~abicheck.service_dump_pipeline.execute_dump_request`
    reads ``exec_resolved.execution_options`` itself whenever its own
    ``options`` keyword is left unset, which is what this function relies on
    below.

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

    *exec_resolved.execution_options*'s own ``legacy_compile_db_tokens``/
    ``legacy_compile_db_matched`` fields (ADR-063 Phase 1): the legacy
    ``-p``/``--compile-db`` auto-match's own derived signal, which has no
    equivalent inside the shared pipeline's typed ``InputSpec`` -- threaded
    through as an explicit pass-through (``execute_dump_request``'s own
    docstring states the precedence rule: the P0.3 fold's own result wins
    whenever it applies) rather than a new typed field on ``InputSpec``
    itself, so the migrated real run keeps seeing it exactly like
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

    *exec_resolved.execution_options*'s own ``seed_collect_mode``/
    ``source_frontend_from_folded_context`` fields (Codex review, two real
    regressions the initial migration introduced): forwarded verbatim to
    :func:`~abicheck.service_dump_pipeline.execute_dump_request`, whose own
    docstring documents each and states the precedence/behavior
    ``perform_elf_dump`` had that these preserve. The caller attaches
    ``seed_collect_mode=resolved.collect_mode`` (the same collect mode
    ``--dry-run`` already projects and ``_write_snapshot_output`` already
    consumes) and ``source_frontend_from_folded_context=True`` unconditionally
    -- matching ``scan``'s own candidate resolution, which passes the
    identical value for the identical reason.

    *exec_resolved.execution_options.allow_build_query* -- the caller
    attaches ``True`` unconditionally here. ``dump``'s CLI is itself the
    trust boundary an explicit ``--config`` already crossed by being typed
    here at all -- unlike ``scan``'s config-file-sourced ``build.query``,
    which needs its own ``resolve_effective_allow_query``
    "level-implies-query" decision (ADR-037 D4) precisely because it is not
    operator-typed. (The CLI used to also carry its own always-``False``
    ``--allow-build-query``/``--build-query`` no-op flags; both are gone now
    -- an explicit ``--config`` is the only authorizer left.)

    Raises:
        click.UsageError: If *exec_resolved* (or the input it resolves) is
            unusable -- a ``ValidationError`` from ``execute_dump_request``
            (e.g. no exports matched, an invalid include directory, an
            unreachable requested depth) -- preserving exit 64, the same
            translation ``cli_resolve._dump_native_binary``'s own docstring
            documents for the retired ``perform_elf_dump``/
            ``handle_non_elf_dump`` call sites (Codex review on PR #980:
            this shared executor's own generic ``except`` clause below
              was silently collapsing that distinction to exit 1 for
            every caller, ELF included, since the earlier PR C migration).
        click.ClickException: For any other extraction failure (exit 1).
    """
    from ...errors import ValidationError
    from ...service_dump_pipeline import execute_dump_request

    try:
        # `options` is left unset: `execute_dump_request` itself falls back
        # to `exec_resolved.execution_options` (or `DumpExecutionOptions()`
        # if that is also unset) whenever its own `options` keyword is
        # `None` -- see that function's own docstring. The caller
        # (`dump_cmd`) is responsible for attaching the real
        # `DumpExecutionOptions` onto `exec_resolved` before calling this
        # function; this module deliberately does not assemble a second one
        # of its own (ADR-063 Track T4).
        result = execute_dump_request(exec_resolved, notify=notify)
    except ValidationError as exc:
        raise click.UsageError(str(exc)) from exc
    except (AbicheckError, RuntimeError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    # `execute_dump_request` already ran the dependency walk itself
    # (`DumpRequest.follow_dependencies`, set from the CLI's own
    # `--follow-deps`) -- a second call by the caller would double it.
    return result.snapshot


def execute_and_write_dump_cli_run(
    exec_resolved: ResolvedDumpRequest,
    *,
    notify: Callable[[str], None],
    build_config: Path | None,
    stamp_provenance: Callable[..., None],
    write_snapshot_output: Callable[..., None],
    git_tag: str | None,
    build_id: str | None,
    no_git: bool,
    output: Path | None,
    build_info: Path | None,
    sources: Path | None,
    collect_mode: str | None,
    build_targets: tuple[str, ...],
    header_backend: str,
    requested_depth: str | None,
    include_dependencies: bool,
    header_roots: tuple[Path, ...],
    clang_bin: str,
    snapshot_compression: str,
    public_headers: tuple[Path, ...],
    public_header_dirs: tuple[Path, ...],
) -> None:
    """Run :func:`execute_dump_cli_run`, then stamp and write its snapshot.

    This is the tail ``dump_cmd``'s ELF and PE/Mach-O real-run branches in
    ``commands/dump.py`` both need -- execute, stamp git/build-id
    provenance, write the snapshot to ``-o``/stdout -- factored out here so
    neither branch repeats it inline. The two branches differ only in how
    they resolve ``header_roots`` (the ELF branch also folds in
    ``dump_manifest_header_roots``, since only ELF supports
    ``--dump-manifest``) and in what precedes this call (the debug-artifact
    UX echo, ELF only); both differences stay in the caller, which passes
    its own already-resolved ``header_roots`` in rather than this function
    re-deriving it.

    ``stamp_provenance``/``write_snapshot_output`` are callables (mirroring
    this module's own ``notify`` parameter) rather than direct imports: this
    module deliberately does not import back into the CLI-registration
    family (``cli_resolve``/``cli_buildsource``, ...) that supplies both --
    see this module's own docstring for why a *new* member of that already-
    accepted import cycle needs a maintainer decision, not a routine split.

    *build_config* is still its own parameter here (unlike
    :func:`execute_dump_cli_run`'s nine execution-option kwargs, which moved
    onto ``exec_resolved.execution_options`` -- ADR-063 Track T4): it is also
    needed below by ``write_snapshot_output``, a genuinely separate
    provenance/write-step concern from execution, so it stays a parameter of
    this function rather than being read a second time off
    ``exec_resolved.execution_options.build_config`` (the caller already
    attached the identical value there for execution's own use).
    """
    snap = execute_dump_cli_run(exec_resolved, notify=notify)

    stamp_provenance(snap, git_tag=git_tag, build_id=build_id, no_git=no_git)
    write_snapshot_output(
        snap,
        output,
        build_info,
        sources,
        build_config,
        collect_mode,
        build_targets=build_targets,
        extractor=header_backend,
        depth=requested_depth,
        include_dependencies=include_dependencies,
        header_roots=header_roots,
        clang_bin=clang_bin,
        snapshot_compression=snapshot_compression,
        public_headers=public_headers,
        public_header_dirs=public_header_dirs,
    )
