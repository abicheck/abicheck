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
static four-rung ``--depth`` ladder,
:data:`~abicheck.buildsource.scan_levels.USER_DEPTHS` restated as plain
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

import dataclasses
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

from ..buildsource.scan_levels import USER_DEPTHS

if TYPE_CHECKING:
    from ..compatibility_evaluation_config import (
        CompatibilityEvaluationConfig,
        ValueProvenance,
    )
    from ..compile_context import CompileContext
    from .plan import AnalysisPlan

__all__ = ["EvidenceView", "ResolvedExecutionContext"]

#: The public ``--depth`` ladder, restated as plain string values --
#: build-time vocabulary derived from the one authority
#: (:data:`abicheck.buildsource.scan_levels.USER_DEPTHS`), not a per-run
#: computed fact. Module-level so it is computed once, not once per
#: :class:`EvidenceView` construction.
_AVAILABLE_DEPTHS: tuple[str, ...] = tuple(depth.value for depth in USER_DEPTHS)


def _canonical_repr(obj: object) -> str:
    """A ``repr()``-like encoding that is order-independent for any
    ``Mapping`` it finds, recursively -- unlike plain ``repr()``, which
    preserves a dict's insertion order verbatim. Both composed types this
    module cares about carry a `Mapping`-typed field
    (`CompatibilityEvaluationConfig.provenance`,
    `CompatibilityPolicyConfig.overrides`) whose dataclass ``__eq__``
    already ignores insertion order -- so two configs a resolver treats as
    equal (built by different front ends assembling the same fields in a
    different order, or replayed from a receipt) must not silently produce
    different digests (Codex review, PR #1027).

    Recurses through dataclasses and tuples/lists to reach every nested
    mapping (`ValueProvenance.shadowed_legacy` is itself a `ValueProvenance`,
    `provenance` maps to `ValueProvenance` instances, etc.); a tuple/list's
    own element order is preserved -- unlike a mapping's key order, it is
    part of the value being fingerprinted (e.g. `ContractConfig.overlays`,
    `PolicyFile.reclassify`-style first-match-wins ordering elsewhere in
    this codebase). Falls back to plain ``repr()`` for anything that is
    neither a dataclass, a Mapping, nor a tuple/list -- every leaf value
    here (`str`/`int`/`bool`/`None`/`Enum`/`Path`) already has a stable,
    deterministic `repr()`.
    """
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        fields_repr = ", ".join(
            f"{f.name}={_canonical_repr(getattr(obj, f.name))}"
            for f in dataclasses.fields(obj)
        )
        return f"{type(obj).__name__}({fields_repr})"
    if isinstance(obj, Mapping):
        items = sorted(obj.items(), key=lambda kv: repr(kv[0]))
        body = ", ".join(
            f"{_canonical_repr(k)}: {_canonical_repr(v)}" for k, v in items
        )
        return f"{{{body}}}"
    if isinstance(obj, (tuple, list)):
        body = ", ".join(_canonical_repr(item) for item in obj)
        kind = "[" + body + "]" if isinstance(obj, list) else "(" + body + ")"
        return kind
    return repr(obj)


def _evaluation_config_value_repr(cfg: CompatibilityEvaluationConfig | None) -> str:
    """:func:`_canonical_repr` of *cfg*'s resolved *values* only --
    ``contract``/``evidence``/``surface``/``assurance``/``policy``/``gate``/
    ``suppressions`` -- deliberately excluding ``provenance`` (Codex review,
    PR #1027, third round).

    ``provenance`` records *how* a value was selected (``ValueProvenance.
    layer``/``source_kind``/``field_location``), not the value itself --
    the CLI and the typed Python API resolving the identical effective
    input legitimately produce different provenance (``SelectorLayer.
    EXPLICIT_CLI`` vs. ``API_REQUEST``, a different ``--flag`` vs. field
    spelling in ``source_kind``), and
    :func:`abicheck.compatibility_evaluation_frontend.cross_front_end_differences`
    already treats that difference as no divergence at all. Hashing
    ``provenance`` into a fingerprint meant to answer "did the *resolved
    input* change" would make two front ends resolving the same values
    hash differently -- defeating the one cross-frontend comparison this
    digest exists to support, and disagreeing with
    :mod:`abicheck.effective_config_digest`'s own established practice
    (its own per-field encodings never read ``provenance`` either)."""
    if cfg is None:
        return _canonical_repr(None)
    return _canonical_repr(
        (
            cfg.contract,
            cfg.evidence,
            cfg.surface,
            cfg.assurance,
            cfg.policy,
            cfg.gate,
            cfg.suppressions,
        )
    )


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
    (:data:`abicheck.buildsource.scan_levels.USER_DEPTHS`) -- always
    populated, since it names what a request *could* have asked for, not
    what this run resolved. *effective_depth*/*depth_satisfied* are
    ``None`` until :meth:`from_assurance` copies them off a real
    :class:`abicheck.analysis_assurance.AnalysisAssurance` -- this class
    never computes them itself (see module docstring)."""

    requested_depth: str | None = None
    effective_depth: str | None = None
    depth_satisfied: bool | None = None
    available_depths: tuple[str, ...] = _AVAILABLE_DEPTHS

    @classmethod
    def for_request(cls, requested_depth: str | None) -> EvidenceView:
        """The pre-execution view: only *requested_depth* is knowable yet."""
        return cls(requested_depth=requested_depth)

    @classmethod
    def from_assurance(cls, assurance: object) -> EvidenceView:
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
        object any caller already has in hand."""
        return cls(
            requested_depth=getattr(assurance, "requested_depth", None),
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
        evaluation_config: CompatibilityEvaluationConfig | None = None,
        compile_contexts: Mapping[str, CompileContext] | None = None,
        assurance: object | None = None,
    ) -> ResolvedExecutionContext:
        """Compose a context from an already-resolved
        :class:`~abicheck.workflows.plan.AnalysisPlan` plus whatever
        evaluation config / compile contexts the caller separately resolved
        for the same run. Reads *plan.operation*/*plan.requested_depth*
        verbatim -- it does not re-run planning, and it does not require
        *plan* to be the source of the other two arguments (a caller that
        has not resolved a compile context for every side, or any
        evaluation config at all, simply omits them). *assurance*, when
        given (a real, already-computed
        :class:`abicheck.analysis_assurance.AnalysisAssurance`), builds the
        full post-execution :class:`EvidenceView` via
        :meth:`EvidenceView.from_assurance` instead of the pre-execution,
        requested-only view -- for a caller resolving this context *after*
        a run has already completed, rather than before."""
        evidence = (
            EvidenceView.from_assurance(assurance)
            if assurance is not None
            else EvidenceView.for_request(plan.requested_depth)
        )
        return cls(
            operation=plan.operation,
            evidence=evidence,
            evaluation_config=evaluation_config,
            compile_contexts=compile_contexts or {},
        )

    def with_assurance(self, assurance: object) -> ResolvedExecutionContext:
        """A new context (frozen dataclasses don't mutate) whose
        :attr:`evidence` is the full post-execution
        :class:`EvidenceView`, copied off *assurance* via
        :meth:`EvidenceView.from_assurance`. Every other field is carried
        over unchanged -- this exists for a caller that built a
        pre-execution context (via :meth:`from_plan` with no *assurance*)
        and only later, once a run completes, has a real
        :class:`abicheck.analysis_assurance.AnalysisAssurance` to attach."""
        return dataclasses.replace(
            self, evidence=EvidenceView.from_assurance(assurance)
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

        Built from each part's own :func:`_canonical_repr` -- every
        dataclass this context composes (`CompatibilityEvaluationConfig`
        and its namespaces, `CompileContext`) is frozen with plain
        value/enum/tuple/mapping fields, so that encoding is a stable,
        content-only, order-independent-for-mappings encoding with no
        memory addresses, the same property :mod:`abicheck.
        effective_config_digest` relies on for its own per-field string
        encodings -- just applied to the whole composed object at once
        rather than field-by-field, since this fingerprint (unlike that
        module's) is not trying to remain stable across a schema change to
        either composed type. Plain ``repr()`` alone is not enough here
        (Codex review, PR #1027): `CompatibilityEvaluationConfig.provenance`/
        `CompatibilityPolicyConfig.overrides` are `Mapping`-typed fields
        whose dataclass equality already ignores insertion order, so two
        configs a resolver treats as equal must not hash differently
        depending on which order their entries happened to be inserted in.

        *compile_contexts* is hashed as one mapping through
        :func:`_canonical_repr` too, not as a delimiter-joined string of
        ``label=value`` parts (Codex review, PR #1027, second round): a
        side *label* is caller-supplied and this class documents it as
        arbitrary (see the class docstring), so nothing rules out a label
        that itself contains ``=`` or the join delimiter -- a hand-rolled
        join over unescaped labels is exactly the non-injective encoding
        :func:`abicheck.effective_config_digest._json_list`'s own
        docstring already documents and avoids for the identical reason
        (an arbitrary namespace/selector string can legally contain a
        delimiter). ``repr()`` of a Python `str` escapes its own quote and
        backslash characters, which is what makes the mapping encoding
        injective over its *keys* the join was not.

        Reads *evaluation_config* through :func:`_evaluation_config_value_repr`,
        not directly through :func:`_canonical_repr`, so the digest never
        includes ``provenance`` (Codex review, PR #1027, third round) -- see
        that function's own docstring for why a resolved-input fingerprint
        must not vary with which front end happened to resolve it.
        """
        return _sha256_of(
            self.operation,
            self.requested_depth or "",
            _evaluation_config_value_repr(self.evaluation_config),
            _canonical_repr(dict(self.compile_contexts)),
        )
