# Copyright 2026 Nikolay Petrov
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

"""``execute_dump_request``'s own execution-options value object.

Split out of :mod:`abicheck.service_dump_pipeline` purely to keep that
module under the architecture gate's 800-line new-file cap once ADR-063
Track T4's second item (a real source-only dump execution variant) needed
more room there -- both classes here are plain, dependency-free value
objects with no reference to :class:`~abicheck.model.AbiSnapshot` or
anything else that would make ``workflows`` importing them a layering
problem, so moving them (rather than the execution logic that *constructs*
:class:`~abicheck.service_dump_pipeline.DumpResult`, which cannot move here
without ``workflows`` importing back up into the flat ``service_`` family)
is the safe half of this split. Mirrors the precedent set when
``service_scan.py`` split ``_descendant_pgids``/``_kill_process_tree`` out
to a ``workflows/scan_subprocess.py`` for the identical reason (that module
went with ``run_scan_subprocess`` in ADR-068 Phase 4; see
``docs/contribute/plans/one-semantic-pipeline.md``'s "Closed in a later
session" note on that split) -- a genuine one-directional move, since
neither class here calls back into ``service_dump_pipeline``.
``service_dump_pipeline.py`` re-exports both names
(``DumpExecutionOptions as DumpExecutionOptions`` /
``_DumpAssuranceView as _DumpAssuranceView``) so every existing import site
and monkeypatch target is unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["DumpExecutionOptions", "_DumpAssuranceView"]


@dataclass(frozen=True)
class _DumpAssuranceView:
    """The minimal shape :meth:`~abicheck.workflows.resolved_execution_context.
    EvidenceView.from_assurance` reads via ``getattr`` -- ``requested_depth``/
    ``effective_depth``/``depth_satisfied`` -- built from a completed
    :class:`~abicheck.service_dump_pipeline.DumpResult`'s own facts rather
    than a real :class:`~abicheck.analysis_assurance.AnalysisAssurance`.

    ``AnalysisAssurance`` is comparison-shaped: pair symmetry (L0/header/DWARF
    context drift between two sides), target/TU/export accounting, fact-set
    comparability -- none of it meaningful for a single ``dump`` with no other
    side to compare against. But the one axis ``AnalysisAssurance`` and a
    single dump genuinely share -- "did the requested ``--depth`` get
    reached" -- *is* knowable here, from
    :func:`~abicheck.service_dump_pipeline.execute_dump_request`'s own
    already-computed ``effective_depth`` (a real post-execution fact, the
    identical value :class:`~abicheck.service_dump_pipeline.DumpResult`
    itself carries), so this class states exactly that axis rather than
    leaving :meth:`~abicheck.workflows.resolved_execution_context.
    ResolvedExecutionContext.with_assurance` fully unwired for ``dump``.
    """

    requested_depth: str | None
    effective_depth: str | None
    depth_satisfied: bool | None


@dataclass(frozen=True)
class DumpExecutionOptions:
    """The out-of-band execution semantics
    :func:`~abicheck.service_dump_pipeline.execute_dump_request` needs beyond
    *resolved* itself and *notify*, folded into one typed value -- ADR-063
    ``duplication-and-convergence-assessment.md`` Track T4 ("Dump request
    contract"). Before this, the nine fields below were nine separate
    keyword parameters on :func:`~abicheck.service_dump_pipeline.
    execute_dump_request`: reaching the same function did not mean a caller
    stated a coherent, nameable execution plan, only that it happened to
    pass the same nine positional names. Grouping them is additive, not a
    new decision point -- every field keeps the exact default
    :func:`~abicheck.service_dump_pipeline.execute_dump_request` already gave
    it, so ``DumpExecutionOptions()`` (the parameter's own default) is
    bit-for-bit equivalent to omitting all nine kwargs before this change.

    Since this track's follow-up, an instance is also attachable to the
    *resolved* request (see :attr:`~abicheck.service_dump_pipeline.
    ResolvedDumpRequest.execution_options`) rather than only assembled fresh
    at :func:`~abicheck.service_dump_pipeline.execute_dump_request`'s own
    call boundary -- so ``dump --dry-run`` can render what a real run would
    pass, instead of the ``dump`` CLI threading the nine values through
    :func:`~abicheck.frontends.cli.dump_execute.execute_dump_cli_run` as
    separate parameters.

    *build_config*/*build_query*/*build_compile_db*/*changed_paths*/
    *allow_build_query* (PR 3A, dump/scan resolver convergence): optional
    pass-throughs to
    :func:`~abicheck.workflows.artifact.execute._resolve_side_snapshot_impl`.
    These exist only for the ELF ``dump`` CLI path's ``--config`` flag --
    and, for a programmatic caller, its own ``build_query``/
    ``build_compile_db`` arguments (PR 3C removed the CLI flags of those
    names; these fields stay because a Python API caller is the operator,
    exactly as an explicit ``--config`` is) -- to route through this one
    shared primitive instead of a second, independent call to the same
    underlying fold.

    *legacy_compile_db_tokens* (ADR-063 Phase 1): the castxml flags the CLI's
    own legacy ``-p``/``--compile-db`` auto-match
    (``cli_helpers_compare._resolve_build_context_flags``) already derived,
    forwarded verbatim to :func:`~abicheck.workflows.artifact.execute._resolve_side_snapshot_impl`
    -- see that function's own docstring for the precedence rule (the P0.3
    fold's own result wins whenever it applies) and
    ``docs/contribute/known-gaps.md``'s "ADR-063 Phase 1" entry for exactly
    what this closes and what still doesn't. *legacy_compile_db_matched*
    (Codex review, fresh evidence) is a separate signal from whether any
    tokens were actually derived -- see the resolve-layer function's own
    docstring for why a real match with zero derived flags still must set
    it. Both are passed by the migrated ``dump`` CLI's real run for either
    binary format (``frontends.cli.dump_execute.execute_dump_cli_run``) --
    ADR-063 Phase 1 migrated PE/Mach-O onto this same function after ELF, so
    ``cli_dump_non_elf.handle_non_elf_dump`` stopped being called from
    ``dump_cmd`` for either format; ADR-063 Track 1 then deleted it, and
    ``cli_dump_helpers.perform_elf_dump`` with it, once the only thing left
    holding either alive was its own unit tests.

    *seed_collect_mode*/*source_frontend_from_folded_context* (Codex review
    on the initial ELF migration -- two real regressions it introduced):
    forwarded verbatim to
    :func:`~abicheck.workflows.artifact.execute._resolve_side_snapshot_impl`,
    whose own docstring documents each. Both default to the pre-typed-object
    behavior (``seed_collect_mode=None`` pins the L2 seed's collect mode to
    ``"off"``; ``source_frontend_from_folded_context=False`` keeps L4 replay
    pointed at the pre-fold compiler). The retired ``perform_elf_dump``
    always forwarded its own resolved ``collect_mode`` to the identical L2
    seed call (unconditionally running a zero-config inferred build query
    for a ``--sources`` tree with no compile database) and always
    reassigned ``gcc_path``/``gcc_prefix``/``effective_gcc_options`` from
    the L3 fold's context once it applied (so an L4 source replay used the
    compiler the L3 fold actually matched, not the caller's pre-fold
    default) -- the migrated ELF run builds
    ``DumpExecutionOptions(seed_collect_mode=resolved.collect_mode,
    source_frontend_from_folded_context=True, ...)`` to preserve both,
    exactly as ``scan``'s own candidate resolution already does for the
    identical reasons (see that call site's comments).
    """

    build_config: Path | None = None
    build_query: str | None = None
    build_compile_db: str | None = None
    changed_paths: tuple[str, ...] = ()
    allow_build_query: bool | None = None
    legacy_compile_db_tokens: tuple[str, ...] = ()
    legacy_compile_db_matched: bool = False
    seed_collect_mode: str | None = None
    source_frontend_from_folded_context: bool = False
