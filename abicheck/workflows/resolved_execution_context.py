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

"""``one-semantic-pipeline.md`` plan, "PR 1": ``ResolvedExecutionContext`` --
one typed container for the resolved-configuration pieces a run already
produces separately today, landed as pure, additive infrastructure before
any consumer is migrated onto it.

**What this module deliberately is not.** It is not a new resolver. Every
value a :class:`ResolvedExecutionContext` carries is produced by a primitive
this codebase already treats as the authority for that value --
:class:`~abicheck.compatibility_evaluation_config.CompatibilityEvaluationConfig`
for policy/contract/gate/surface/evidence/assurance configuration (ADR-049
D7, resolved by
:func:`abicheck.compatibility_evaluation_frontend.resolve_compatibility_evaluation_config`),
:class:`~abicheck.compile_context.CompileContext` for a side's resolved L2
compile-context inputs, and :class:`~abicheck.workflows.plan.AnalysisPlan`
for the pre-flight requested-depth/operation pair (ADR-063 Phase 4). This
module composes references to those objects into one container a caller can
hold and pass around; it never re-derives, re-parses, or duplicates the
logic that produced any of them. That is a deliberate application of this
repository's own governing invariant ("one concept, one representation") to
the gap the plan's own analysis names: today a caller who wants "the
resolved configuration for this run" has to know to go collect three
separately-threaded objects from three different call sites, with no single
type describing what a fully resolved run's own inputs actually are.

**How this closes the "requested/effective/available depth" axis without
duplicating its one existing authority.** "Effective depth" already has an
authority -- :class:`abicheck.analysis_assurance.AnalysisAssurance` -- and
it is necessarily a *post*-execution fact: what a side's resolved snapshot
actually turned out to carry, not something knowable at the point a
:class:`ResolvedExecutionContext` is first assembled (before extraction has
run at all, mirroring :class:`~abicheck.workflows.plan.AnalysisPlan`'s own
"requested, not resolved" scope for the identical reason -- see that
module's docstring). Recomputing "effective"/"available" independently here
would be exactly the "two independently constructible representations of
the same fact" shape the Governing Invariant forbids. :class:`EvidenceView`
resolves this by construction rather than by omission: it always carries
``requested_depth`` (knowable pre-execution) and ``available_depths`` (the
static four-rung ``--depth`` ladder, read through
:data:`~abicheck.evidence_depth.DEPTH_RANK` (the shared leaf
`workflows/AGENTS.md` names for this vocabulary) and restated as plain
values -- build-time vocabulary, not a per-run computed fact, so stating it
here duplicates nothing); ``effective_depth``/``depth_satisfied`` stay
``None`` until :meth:`EvidenceView.from_assurance` copies them verbatim off
a real, already-computed ``AnalysisAssurance`` -- never re-derived. A
:class:`ResolvedExecutionContext` built before execution therefore carries
a genuinely partial :class:`EvidenceView` (by construction, not as a
missing feature), and :meth:`ResolvedExecutionContext.with_assurance`
returns a *new* context (frozen dataclasses don't mutate) whose
:class:`EvidenceView` is complete, once a caller has one to attach.

**Why this does not compute a rich-tier effective-config digest.**
:mod:`abicheck.effective_config_digest` is explicit that no single object
holds every configuration axis for every run today, because several of the
rich tier's own fields (``policy.pattern_verdicts``,
``surface.scope_to_public_surface``, ``gate.scope``, ...) are themselves
outcomes of a completed comparison (``DiffResult``), not inputs a
pre-execution context could ever carry. Reusing that module's own algorithm
here would either silently omit those fields (a digest that *looks* like the
rich tier's but is not comparable to it) or require this module to accept a
``DiffResult`` and stop being a pre-execution type. Neither is honest, so
:meth:`ResolvedExecutionContext.resolution_digest` (removed) is a separate, narrower
fingerprint -- deliberately named differently from
``effective_config_digest`` -- covering only what is genuinely available
before a run executes: the resolved evaluation config, the resolved compile
contexts, the operation, and the requested depth. It answers "did the
*resolved input* change", not "did the *effective, outcome-aware*
configuration change" (the latter question stays
:mod:`abicheck.effective_config_digest`'s alone).

**Not yet wired into any live command.** Exactly like
:mod:`abicheck.compatibility_evaluation_frontend` before it ("resolves
configuration, does not apply it"), this module is landed as its own first
step -- the type, tested, before any call site is migrated to build or
consume one. See ``docs/contribute/plans/one-semantic-pipeline.md``'s Phase 4
section for the follow-on consumer-migration work this enables but does not
itself perform.
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

from ..evidence_depth import DEPTH_RANK

if TYPE_CHECKING:
    from ..compatibility_evaluation_config import (
        CompatibilityEvaluationConfig,
    )
    from ..compile_context import CompileContext
    from .plan import AnalysisPlan

__all__ = ["EvidenceView", "ResolvedExecutionContext"]

#: The public ``--depth`` ladder, restated as plain string values -- read
#: through :mod:`abicheck.evidence_depth` (Codex review, PR #1027, fifth
#: round), the one shared leaf `workflows/AGENTS.md` names for this exact
#: vocabulary ("Shared vocabulary ... lives in leaves any layer may depend
#: on: `abicheck/evidence_depth.py` (the depth ladder) ... Prefer them over
#: re-deriving") -- not `model.evidence_depth_levels.USER_DEPTHS` directly, which
#: would restore the workflow-to-buildsource coupling that module exists to
#: isolate. `DEPTH_RANK`'s keys are already this exact ordered ladder (that
#: module derives it from `USER_DEPTHS` itself, once); a plain `dict`
#: preserves insertion order, so `tuple(DEPTH_RANK)` is the ladder's values in
#: rank order, verbatim. Module-level so it is computed once, not once per
#: :class:`EvidenceView` construction.
_AVAILABLE_DEPTHS: tuple[str, ...] = tuple(DEPTH_RANK)


def _sha256_of(*parts: str) -> str:
    """NUL-delimited SHA-256 over *parts*, prefixed ``sha256:`` (hex) --
    the identical framing :func:`abicheck.effective_config_digest._sha256_of`
    uses, kept as an independent copy rather than importing that module's
    private helper: the two digests are deliberately not the same
    computation (see this module's own docstring), and importing a
    leading-underscore name across a module boundary would misstate that
    as accidental duplication rather than the considered choice it is."""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return f"sha256:{digest.hexdigest()}"


@dataclass(frozen=True)
class EvidenceView:
    """The coarse ``--depth`` evidence-ladder view for one run.

    *requested_depth* is knowable pre-execution (the same value
    :attr:`abicheck.workflows.plan.AnalysisPlan.requested_depth` already
    carries). *available_depths* is the static four-rung public ladder
    (read through :data:`abicheck.evidence_depth.DEPTH_RANK`) -- always
    populated, since it names what a request *could* have asked for, not
    what this run resolved; a **read-only property**, not a constructor
    parameter or dataclass field (Codex review, PR #1027, fourth round) --
    a plain field would let a caller construct a value like
    ``EvidenceView(available_depths=("bogus",))``, competing with the one
    real ladder this class exists to state invariantly. *effective_depth*/
    *depth_satisfied* are ``None`` until :meth:`from_assurance` copies them
    off a real :class:`abicheck.analysis_assurance.AnalysisAssurance` --
    this class never computes them itself (see module docstring)."""

    requested_depth: str | None = None
    effective_depth: str | None = None
    depth_satisfied: bool | None = None

    @property
    def available_depths(self) -> tuple[str, ...]:
        """The static four-rung public ``--depth`` ladder -- the same value
        for every instance, so it is a computed property over the one
        module-level constant rather than a per-instance field a caller
        could override."""
        return _AVAILABLE_DEPTHS

    @classmethod
    def for_request(cls, requested_depth: str | None) -> EvidenceView:
        """The pre-execution view: only *requested_depth* is knowable yet."""
        return cls(requested_depth=requested_depth)

    @classmethod
    def from_assurance(
        cls, assurance: object, *, requested_depth: str | None = None
    ) -> EvidenceView:
        """The post-execution view, copied verbatim off *assurance* -- a
        real :class:`abicheck.analysis_assurance.AnalysisAssurance` in
        practice. Reads ``requested_depth``/``effective_depth``/
        ``depth_satisfied`` via ``getattr`` rather than importing that
        class and ``isinstance``-checking against it: `analysis_assurance.py`
        imports `checker_types.DiffResult` and sits well above this
        `workflows`-layer module in the dependency graph (`workflows` may
        import `model`/`storage`/`extract`/`compare`/`policy`, never a
        checker-layer module), so a structural read is what lets this leaf
        module stay import-cycle-free while still accepting the real
        object any caller already has in hand.

        *requested_depth* is a fallback used only when *assurance* itself
        carries none (Codex review, PR #1027, fourth round):
        ``analysis_assurance.compute_analysis_assurance()``'s own
        ``not_comparable`` short-circuit returns a real ``AnalysisAssurance``
        whose ``requested_depth`` is ``None`` -- "every other assurance axis
        is unreliable for this run" there means exactly that, not "no depth
        was ever requested." Naively copying that ``None`` through would
        silently discard a genuinely known pre-execution value (e.g. from
        ``AnalysisPlan.requested_depth``) the moment a run turned out
        not-comparable, changing this run's own resolved identity for a reason that has nothing to do with what was
        requested. *assurance*'s own value always wins when present."""
        assurance_requested = getattr(assurance, "requested_depth", None)
        return cls(
            requested_depth=(
                assurance_requested
                if assurance_requested is not None
                else requested_depth
            ),
            effective_depth=getattr(assurance, "effective_depth", None),
            depth_satisfied=getattr(assurance, "depth_satisfied", None),
        )


@dataclass(frozen=True)
class ResolvedExecutionContext:
    """One resolved run's configuration, composed from already-resolved parts.

    *operation* mirrors :attr:`abicheck.workflows.plan.AnalysisPlan.operation`
    (``"dump"``/``"compare"``/``"scan"``). *evidence* is the
    :class:`EvidenceView` for this run -- built pre-execution via
    :meth:`EvidenceView.for_request` (only ``requested_depth``/
    ``available_depths`` known), or post-execution via
    :meth:`EvidenceView.from_assurance` once a real
    :class:`abicheck.analysis_assurance.AnalysisAssurance` exists (see
    :meth:`with_assurance`). *evaluation_config* is the ADR-049 D7 resolved
    :class:`~abicheck.compatibility_evaluation_config.CompatibilityEvaluationConfig`
    for this run, when one was resolved (a plain run with no
    ``--pack``/``--contract`` may have none -- this field is ``None`` rather
    than a synthesized stand-in, so a reader can tell "no rich config was
    resolved" from "a rich config resolved to defaults"). *compile_contexts*
    maps each side's label (mirroring
    :attr:`abicheck.workflows.plan.SidePlan.label` -- ``"old"``/``"new"`` for
    a comparison, a single arbitrary label for a `dump`) to that side's
    resolved :class:`~abicheck.compile_context.CompileContext`; a dump/side
    that resolved no header-AST compile context at all (a binary-only depth)
    is simply absent from the mapping rather than present with placeholder
    values.
    """

    operation: str
    evidence: EvidenceView = field(default_factory=EvidenceView)
    evaluation_config: CompatibilityEvaluationConfig | None = None
    compile_contexts: Mapping[str, CompileContext] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Freeze the mapping so a caller can't mutate a supposedly-resolved
        # context in place after construction -- the same immutability
        # `CompatibilityEvaluationConfig`'s own namespaces enforce.
        object.__setattr__(
            self, "compile_contexts", MappingProxyType(dict(self.compile_contexts))
        )

    @property
    def requested_depth(self) -> str | None:
        """Convenience alias for ``evidence.requested_depth`` -- the coarse
        ``--depth`` request, never a resolved/effective value (see module
        docstring). Reads through :attr:`evidence` rather than duplicating
        it as a second field, so the two can never disagree."""
        return self.evidence.requested_depth

    @classmethod
    def from_plan(
        cls,
        plan: AnalysisPlan,
        *,
        compile_contexts: Mapping[str, CompileContext] | None = None,
    ) -> ResolvedExecutionContext:
        """Compose a pre-execution context from an already-resolved
        :class:`~abicheck.workflows.plan.AnalysisPlan` plus whatever compile
        contexts the caller separately resolved for the same run. Reads
        *plan.operation* verbatim and *plan.requested_depth* case-normalized
        (see below) -- it does not re-run planning, and it does not require
        *plan* to be the source of *compile_contexts* (a caller that has not
        resolved a compile context for every side simply omits them). The
        evidence view is the requested-only one; the evaluation config and a
        completed run's assurance attach later, through
        :meth:`with_evaluation_config`/:meth:`for_classification` and
        :meth:`with_assurance`. *plan.requested_depth* is lower-cased here
        (Codex review, PR #1031) -- unlike *plan.operation*,
        ``AnalysisPlan.requested_depth`` is not itself normalized (a
        typed-API caller can spell a valid depth case-insensitively, e.g.
        ``"HEADERS"``, the same way
        :func:`~abicheck.service_compare_pipeline.classify_compare_pair`'s
        ``result.requested_depth = request.depth.lower()`` normalizes it for
        ``DiffResult`` before this context exists), and this class's own
        ``_AVAILABLE_DEPTHS``/:meth:`resolution_digest` (removed) are case-sensitive:
        an unnormalized depth would both fail to appear in its own
        :attr:`EvidenceView.available_depths` and hash differently from an
        equivalent lower-case request. The normalized depth is also what
        :meth:`with_assurance` later passes as
        :meth:`EvidenceView.from_assurance`'s fallback (Codex review, PR
        #1027, fourth round)."""
        normalized_depth = (
            plan.requested_depth.lower() if plan.requested_depth is not None else None
        )
        return cls(
            operation=plan.operation,
            evidence=EvidenceView.for_request(normalized_depth),
            compile_contexts=compile_contexts or {},
        )

    def with_evaluation_config(
        self, evaluation_config: CompatibilityEvaluationConfig
    ) -> ResolvedExecutionContext:
        """A new context carrying the ADR-049 D7 *evaluation_config* the run
        was actually scored under. For a caller that built a pre-execution
        context (:meth:`from_plan`) before the policy/suppression/pack inputs
        the config resolves from were loaded -- ``classify_compare_pair`` is
        the one, since those loads belong to classification, not to
        artifact resolution. Every other field is carried over unchanged."""
        return dataclasses.replace(self, evaluation_config=evaluation_config)

    def for_classification(
        self,
        evaluation_config: CompatibilityEvaluationConfig,
        requested_depth: str | None,
    ) -> ResolvedExecutionContext:
        """The context a classification actually ran under: *evaluation_config*
        attached, and *requested_depth* (the depth the classification projected
        both sides to) replacing a different pre-execution one. A caller may
        classify a pair under a different request than resolved it
        (``classify_compare_pair``'s two-phase split), and the returned context
        must not report a depth -- or a ``resolution_digest`` (removed) -- the
        classification never saw."""
        ctx = self.with_evaluation_config(evaluation_config)
        if ctx.requested_depth != requested_depth:
            ctx = dataclasses.replace(
                ctx, evidence=EvidenceView.for_request(requested_depth)
            )
        return ctx

    def with_assurance(self, assurance: object) -> ResolvedExecutionContext:
        """A new context (frozen dataclasses don't mutate) whose
        :attr:`evidence` is the full post-execution
        :class:`EvidenceView`, copied off *assurance* via
        :meth:`EvidenceView.from_assurance`. Every other field is carried
        over unchanged -- this exists for a caller that built a
        pre-execution context (via :meth:`from_plan`) and only later, once a
        run completes, has a real
        :class:`abicheck.analysis_assurance.AnalysisAssurance` to attach.
        Passes this context's own already-known
        ``self.evidence.requested_depth`` through as
        :meth:`EvidenceView.from_assurance`'s fallback (Codex review, PR
        #1027, fourth round): a ``not_comparable`` *assurance* must not
        silently erase a requested depth this context already had."""
        return dataclasses.replace(
            self,
            evidence=EvidenceView.from_assurance(
                assurance, requested_depth=self.evidence.requested_depth
            ),
        )
