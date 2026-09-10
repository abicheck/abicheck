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

"""``ResolvedDumpRequest`` -- split out of :mod:`abicheck.service_dump_pipeline`.

ADR-063 Track T4 ("Dump request contract"): this dataclass carries zero
dependency on :class:`~abicheck.service_dump_pipeline.DumpResult` or
anything else in that module, so it can live in a real ADR-061
responsibility-package leaf (``workflows/artifact/``, the same layer
``service_dump_pipeline.py`` is itself classified into via
``architecture/modules.yaml``'s ``layers.workflows.legacy_paths``) instead
of growing that flat module further.

The direct motivation: :mod:`abicheck.workflows.artifact.execute_source_only`
needs this type for its own function signature, and
:mod:`abicheck.service_dump_pipeline` needs to call that function -- a type
importable only from ``service_dump_pipeline.py`` itself would force a
module-level import back into it, which the AI-readiness
``import-cycle-growth`` gate's cycle scan (an AST walk that does not
special-case ``TYPE_CHECKING`` blocks or function-local imports) flags as a
new two-module cycle regardless of how the import is spelled. Both modules
importing this type from one shared, one-directional leaf avoids the cycle
outright. ``service_dump_pipeline.py`` re-exports the name
(``ResolvedDumpRequest as ResolvedDumpRequest``) so every existing
``from abicheck.service_dump_pipeline import ResolvedDumpRequest`` import,
``isinstance`` check, and ``dataclasses.replace(...)`` call site is
unaffected -- it is the identical class object, not a copy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import ResolvedArtifactPlan
from .dump_execution_options import DumpExecutionOptions

if TYPE_CHECKING:
    from pathlib import Path

    from ...service_compare_evidence import SideEvidence
    from ...workflows.resolved_execution_context import ResolvedExecutionContext
    from ..contracts import DumpRequest

__all__ = ["ResolvedDumpRequest"]


@dataclass(frozen=True)
class ResolvedDumpRequest:
    """A ``DumpRequest`` after resolution, before execution.

    CLI cleanup phase two, PR C / PR 3A (see
    ``docs/contribute/plans/cli-cleanup-phase-two.md``): the object
    ``dump --dry-run`` is meant to render, once its rendering path
    (``cli_dump_helpers.render_dump_dry_run``, currently a hand-written
    second implementation) is migrated to build from this instead of
    re-deriving the same facts independently.

    Carries only what ``resolve_dump_request`` can determine without
    invoking castxml/clang or writing anything: the normalized language, the
    requested header-AST backend, the detected binary format, the requested
    ``depth`` (an input, known up front) and the effective *collect mode*
    (the build/source evidence level ``depth`` resolves to). Deliberately
    does **not** carry an *achieved* depth — that can only be read off the
    completed snapshot (``cli_dump_helpers.fold_dump_provenance_into_dict``
    derives it via ``_gated_source_label(snap.build_source, snap)``), so a
    resolve-only object reporting it would have to guess, and a guess that
    disagrees with the real run defeats the point of rendering ``--dry-run``
    from a real resolved object (Codex review, fresh evidence). See
    ``DumpResult`` for the achieved depth.

    Also deliberately excludes the P0.3 L3→L2 compile-context fold's result:
    that fold (``buildsource.l2_seed.seed_includes_and_fold_compile_context``)
    can raise ``HeaderCompileContextAmbiguousError`` on genuinely ambiguous
    build evidence, and ``--dry-run``'s existing contract
    (``render_dump_dry_run``'s own docstring) is to never raise on anything
    but a usage error. Folding it in here would be a real behavior change to
    that contract, not merely an additive one — it stays inside
    ``execute_dump_request``, unchanged from where ``run_dump_request``
    already runs it today (via ``service_input_resolution.resolve_side_snapshot``).

    ``artifact_plan`` (dedup-and-convergence plan, Phase 1 item 1
    "Milestone B"): the same facts this object already carries, also
    attached to a :class:`~abicheck.workflows.artifact.ResolvedArtifactPlan`
    -- the general, cross-consumer shape the plan's target architecture
    names. Built with an empty ``pending_cleanups`` (this function allocates
    no resource -- see :mod:`abicheck.workflows.artifact.contracts`'s own
    module docstring for why the two fields that *would* require one,
    effective include search and effective compile context, stay excluded
    here too), so it is
    additive, inert data today: nothing yet reads it. It exists so a future
    consumer of the general shape (e.g. a migrated ``render_dump_dry_run``)
    has one object to build from instead of this dump-specific one, without
    this dataclass's own field surface changing again when that lands.

    Excluded from this dataclass's generated ``__eq__``/``__hash__``
    (``compare=False``, Codex review): ``ResolvedArtifactPlan`` is a plain
    class, not a dataclass, so it compares by identity. Two structurally
    identical ``DumpRequest``s resolved independently would otherwise
    produce two ``ResolvedDumpRequest``s that compare unequal purely
    because each carries its own, distinct ``ResolvedArtifactPlan``
    instance -- silently breaking equality-based comparison or caching for
    every existing and future caller of this frozen dataclass, over a field
    that is itself inert today.

    ``resolved_execution_context`` (One Semantic Pipeline plan, sub-phase 4B
    -- ``dump``'s own slice of the gap
    :class:`~abicheck.service_compare_pipeline.ResolvedComparePair`'s
    identically-named field already closed for ``compare``): the
    :class:`~abicheck.workflows.resolved_execution_context.
    ResolvedExecutionContext` built from this same call's own, otherwise-
    discarded :class:`~abicheck.workflows.plan.AnalysisPlan`
    (``resolve_dump_request`` already calls ``AnalysisPlanner.resolve``
    for its ADR-063 Phase 4 pre-flight check -- this is not a second
    resolution). ``operation`` reads ``"dump"`` off the plan; ``evidence``
    carries only ``requested_depth``/``available_depths`` at this point --
    the pre-execution view, via :meth:`~abicheck.workflows.
    resolved_execution_context.ResolvedExecutionContext.from_plan` with no
    *assurance*. Optional and additive: excluded from ``compare=False`` for
    the same reason ``artifact_plan`` is -- a fresh, distinct
    ``ResolvedExecutionContext`` instance must not make two structurally
    identical resolutions compare unequal. Carries no ``evaluation_config``/
    ``compile_contexts`` yet, for the identical reason
    ``ResolvedComparePair.resolved_execution_context`` does not: neither
    resolves at this seam. See ``execute_dump_request`` for the
    post-execution counterpart, attached via
    :meth:`~abicheck.workflows.resolved_execution_context.
    ResolvedExecutionContext.with_assurance`.
    """

    request: DumpRequest
    lang: str
    lang_explicit: bool
    header_backend: str
    # A *reporting-only* projection of the concrete header-AST backend
    # resolution currently favors -- what a future `--dry-run` render would
    # show. `header_backend` alone under-reports this: service.py's own
    # eff_backend computation gives an explicit `evidence.compile.frontend`
    # precedence over the bare `header_backend` arg, and resolves "auto" to
    # a concrete backend either way, so a naive render of `header_backend`
    # can name a different frontend than what would currently be chosen.
    #
    # Deliberately NOT what execute_dump_request passes to execution
    # (Codex review, two rounds -- the first attempt did pass this value
    # through, which is a real regression, not a pin: `dumper.
    # _header_ast_parser`'s own `_auto_ast_fallback_eligible(backend)`
    # checks whether `backend` is *literally* the string "auto" to decide
    # whether a CastXML failure may gracefully fall back to Clang, and a
    # non-"host" `frontend_context` has its own "auto"-specific routing --
    # pre-resolving "auto" to a concrete choice before it reaches that
    # function silently strips those behaviors). Execution therefore keeps
    # passing the bare `header_backend` through unchanged, and this field
    # is accepted as a best-effort preview that can, in principle, disagree
    # with what execution ends up doing if the environment changes between
    # resolve and execute, or if the genuinely-unpinned-"auto" fallback
    # path fires -- the same class of accepted imprecision every other
    # resolve-time preview in this object already carries.
    effective_header_backend: str
    fmt: str | None
    debug_format: str | None
    requested_depth: str | None
    evidence: SideEvidence
    public_headers: tuple[Path, ...]
    public_header_dirs: tuple[Path, ...]
    artifact_plan: ResolvedArtifactPlan | None = field(default=None, compare=False)
    resolved_execution_context: ResolvedExecutionContext | None = field(
        default=None, compare=False
    )
    # ADR-063 Track T4 ("Dump request contract"): the `DumpExecutionOptions`
    # a real execution would pass to `execute_dump_request`, when a caller
    # has resolved one -- so `dump --dry-run`
    # (`cli_dump_helpers.render_dump_dry_run`) can show these nine values.
    # `resolve_dump_request` never populates this itself (`build_config`/
    # `legacy_compile_db_tokens`/`legacy_compile_db_matched` are CLI-only
    # values with no `DumpRequest` equivalent); the `dump` CLI attaches its
    # own via `dataclasses.replace(resolved, execution_options=...)`.
    # `execute_dump_request` reads this as its default when its own
    # `options` is `None` -- an explicit `options=` argument still wins.
    # Comparable like any plain value (unlike `artifact_plan`/
    # `resolved_execution_context` above, which compare by identity).
    execution_options: DumpExecutionOptions | None = None

    @property
    def collect_mode(self) -> str:
        """The effective build/source evidence level ``depth`` resolved to."""
        return self.evidence.collect_mode

    @property
    def headers(self) -> tuple[Path, ...]:
        """The resolved public-header set (files only; see ``public_header_dirs``)."""
        return tuple(self.evidence.headers)
