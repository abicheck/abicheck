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

**Why this does not attempt a single "requested/effective/available depth"
computation, though the plan's own PR 1 description names one.** That fact
already has one authority --
:class:`abicheck.analysis_assurance.AnalysisAssurance` -- and it is
necessarily a *post*-execution fact: "effective depth" is what a side's
resolved snapshot actually turned out to carry, not something knowable at
the point a :class:`ResolvedExecutionContext` is assembled (before
extraction has run at all, mirroring :class:`~abicheck.workflows.plan.AnalysisPlan`'s
own "requested, not resolved" scope for the identical reason -- see that
module's docstring). Inventing a second, pre-execution "effective depth"
field here would be exactly the "two independently constructible
representations of the same fact" shape the Governing Invariant forbids, so
this module carries only ``requested_depth`` (the same value
:class:`~abicheck.workflows.plan.AnalysisPlan` already resolves) and leaves
"effective"/"available" to the existing post-execution authority.

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
:meth:`ResolvedExecutionContext.resolution_digest` is a separate, narrower
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

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..compatibility_evaluation_config import (
        CompatibilityEvaluationConfig,
        ValueProvenance,
    )
    from ..compile_context import CompileContext
    from .plan import AnalysisPlan

__all__ = ["ResolvedExecutionContext"]


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
class ResolvedExecutionContext:
    """One resolved run's configuration, composed from already-resolved parts.

    *operation* mirrors :attr:`abicheck.workflows.plan.AnalysisPlan.operation`
    (``"dump"``/``"compare"``/``"scan"``). *requested_depth* mirrors
    :attr:`~abicheck.workflows.plan.AnalysisPlan.requested_depth` -- the
    coarse ``--depth`` request, never a resolved/effective value (see module
    docstring). *evaluation_config* is the ADR-049 D7 resolved
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
    requested_depth: str | None = None
    evaluation_config: CompatibilityEvaluationConfig | None = None
    compile_contexts: Mapping[str, CompileContext] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Freeze the mapping so a caller can't mutate a supposedly-resolved
        # context in place after construction -- the same immutability
        # `CompatibilityEvaluationConfig`'s own namespaces enforce.
        object.__setattr__(
            self, "compile_contexts", MappingProxyType(dict(self.compile_contexts))
        )

    @classmethod
    def from_plan(
        cls,
        plan: AnalysisPlan,
        *,
        evaluation_config: CompatibilityEvaluationConfig | None = None,
        compile_contexts: Mapping[str, CompileContext] | None = None,
    ) -> ResolvedExecutionContext:
        """Compose a context from an already-resolved
        :class:`~abicheck.workflows.plan.AnalysisPlan` plus whatever
        evaluation config / compile contexts the caller separately resolved
        for the same run. Reads *plan.operation*/*plan.requested_depth*
        verbatim -- it does not re-run planning, and it does not require
        *plan* to be the source of the other two arguments (a caller that
        has not resolved a compile context for every side, or any
        evaluation config at all, simply omits them)."""
        return cls(
            operation=plan.operation,
            requested_depth=plan.requested_depth,
            evaluation_config=evaluation_config,
            compile_contexts=compile_contexts or {},
        )

    def provenance_for(self, field_name: str) -> ValueProvenance | None:
        """The D7 :class:`~abicheck.compatibility_evaluation_config.ValueProvenance`
        recorded for *field_name* (e.g. ``"contract.mode"``), or ``None``
        when no :attr:`evaluation_config` was resolved or *field_name* has
        no recorded provenance. Delegates to
        ``evaluation_config.provenance`` -- the one place per-field
        provenance already lives; this is a convenience accessor, not a
        second copy of it."""
        if self.evaluation_config is None:
            return None
        return self.evaluation_config.provenance.get(field_name)

    def resolution_digest(self) -> str:
        """A structural fingerprint of this *resolved input* -- distinct
        from :func:`abicheck.effective_config_digest.effective_config_digest`,
        which fingerprints the outcome-aware effective configuration of a
        *completed* comparison (see module docstring for why the two are not
        the same computation and are not cross-comparable). Two contexts
        that resolved the same operation, requested depth, evaluation
        config, and compile contexts produce the same digest; this says
        nothing about whether the run they describe would produce the same
        findings.

        Built from each part's own already-deterministic ``repr()`` --
        every dataclass this context composes (`CompatibilityEvaluationConfig`
        and its namespaces, `CompileContext`) is frozen with plain
        value/enum/tuple fields, so its default ``repr()`` is a stable,
        content-only encoding with no memory addresses or nondeterministic
        ordering, the same property :mod:`abicheck.effective_config_digest`
        relies on for its own per-field string encodings -- just applied to
        the whole composed object at once rather than field-by-field, since
        this fingerprint (unlike that module's) is not trying to remain
        stable across a schema change to either composed type.
        """
        compile_context_parts = [
            f"{label}={self.compile_contexts[label]!r}"
            for label in sorted(self.compile_contexts)
        ]
        return _sha256_of(
            self.operation,
            self.requested_depth or "",
            repr(self.evaluation_config),
            "\x1f".join(compile_context_parts),
        )
