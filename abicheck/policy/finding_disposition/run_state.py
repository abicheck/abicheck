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

"""The state one disposition run threads through its steps, and the rules
that seed it.

:class:`DispositionRules` is everything a caller resolves *before* the run
(the suppression list, namespace conventions, scope switches);
:class:`PipelineContext` is the per-run state the steps share: those rules,
both snapshots, and the side-output buckets each disposed-of finding lands in.
Callers build the rules; the pipeline builds the context
(:meth:`PipelineContext.from_rules`). Steps -- and tests of one step -- may
construct a context directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ...model import AbiSnapshot
    from ...model.change import Change
    from ...policy_file import PolicyFile
    from ...suppression import SuppressionList
    from ...surface import PublicSurface
    from ..disposition_ledger import DispositionLedger


@dataclass(frozen=True)
class DispositionRules:
    """What decides a finding's disposition, resolved before the run.

    Grouped into one value rather than a dozen trailing parameters: the
    pipeline's previous signature twice had a parameter inserted mid-list,
    silently rebinding a positional caller's ledger (bug class
    ``api.positional_slot_rebinding``).
    """

    suppression: SuppressionList | None = None
    #: Contractually frozen namespace globs (``PolicyFile.frozen_namespaces``).
    frozen_namespaces: tuple[str, ...] = ()
    #: ``None`` means "not configured": each consumer uses its own default.
    internal_namespaces: tuple[str, ...] | None = None
    #: ``None`` means "not configured": each consumer uses its own default.
    experimental_namespaces: tuple[str, ...] | None = None
    scope_to_public_surface: bool = False
    force_public_symbols: frozenset[str] = frozenset()
    collapse_versioned_symbols: bool = False
    public_surface_allowlist: frozenset[str] | None = None

    @classmethod
    def from_policy_file(
        cls,
        policy_file: PolicyFile | None,
        *,
        suppression: SuppressionList | None = None,
        scope_to_public_surface: bool = False,
        force_public_symbols: set[str] | frozenset[str] | None = None,
        collapse_versioned_symbols: bool = False,
        public_surface_allowlist: set[str] | frozenset[str] | None = None,
    ) -> DispositionRules:
        """The rules ``compare()`` applies: the policy file's namespace
        conventions plus the run's own suppression and scope switches."""
        from ..policy_file_namespaces import (
            experimental_namespaces,
            internal_namespaces,
        )

        return cls(
            suppression=suppression,
            frozen_namespaces=tuple(policy_file.frozen_namespaces)
            if policy_file is not None
            else (),
            internal_namespaces=internal_namespaces(policy_file) or None,
            experimental_namespaces=experimental_namespaces(policy_file) or None,
            scope_to_public_surface=scope_to_public_surface,
            force_public_symbols=frozenset(force_public_symbols or ()),
            collapse_versioned_symbols=collapse_versioned_symbols,
            public_surface_allowlist=frozenset(public_surface_allowlist)
            if public_surface_allowlist is not None
            else None,
        )


@dataclass
class PipelineContext:
    """Shared state passed through the pipeline.

    *old* is the **baseline**, and it is genuinely optional: a
    ``compare --no-baseline`` audit resolves exactly one snapshot and
    declares that no prior surface exists (ADR-065's ``declared_absent``),
    so there is no second side for an evolution step to read. ``None`` is
    that state, stated once here rather than re-encoded by every consumer
    -- and deliberately *not* spelled as "the candidate compared against
    itself", which is what this pipeline used to receive and which made a
    pre-existing, never-observed-to-change condition read as an observed
    ``persistent`` one.

    Two derived properties express the only two things a step ever needs to
    know about that: :attr:`baseline_present` (may an evolution question be
    asked at all?) and :attr:`baseline_or_empty` (what the OLD side
    contributes to a step that unions or looks up across both).
    """

    old: AbiSnapshot | None
    new: AbiSnapshot
    suppression: SuppressionList | None = None
    # Glob patterns identifying contractually frozen namespaces (e.g.
    # ``**::detail::r1``). Threaded in from PolicyFile.frozen_namespaces.
    # Consumed by EscalateFrozenNamespaceViolations to tag matching
    # findings with Change.frozen_namespace_violation.
    frozen_namespaces: list[str] = field(default_factory=list)
    # ADR-044 P1 item 5: project-configurable internal-implementation-namespace
    # convention, threaded in from PolicyFile.internal_namespaces. None means
    # "not configured" — MarkReachability/DetectInternalLeaks/
    # DemoteUnreachableInternalChurn each fall back to their own
    # DEFAULT_INTERNAL_NAMESPACES. Deliberately not consulted by
    # DetectNamespacePatterns's experimental_namespaces — a different, unrelated
    # convention (see PolicyFile.internal_namespaces's docstring).
    internal_namespaces: tuple[str, ...] | None = None
    # The `experimental::` graduation convention, threaded in from
    # PolicyFile.experimental_namespaces. None means "not configured" --
    # DetectNamespacePatterns falls back to DEFAULT_EXPERIMENTAL_NAMESPACES.
    # A separate field from `internal_namespaces` above on purpose: the two
    # name unrelated conventions (implementation detail vs. not-yet-stable).
    experimental_namespaces: tuple[str, ...] | None = None
    # ADR-024 §D4: when True, FilterNonPublicSurface moves findings that are
    # not on the public-header-scoped ABI surface to ``out_of_surface``.
    scope_to_public_surface: bool = False
    # `compare --post-manifest`: an explicit committed-ABI surface (the set of
    # `pp_*`/ufunc-loop symbols a POST manifest promises). When set,
    # FilterNonPublicSurface scopes against *this* set instead of the
    # header-derived surface — an export finding whose symbol is not committed is
    # demoted, while type-level and leak findings are kept (conservative). None
    # means "not manifest-scoped".
    public_surface_allowlist: set[str] | None = None
    # G15 (opt-in): when True, DetectVersionedSymbolScheme reclassifies the
    # version-rename pairs (ICU `u_*_NN`) as compatible so the verdict reflects
    # the real delta instead of the rename churn. Off by default (authority rule).
    collapse_versioned_symbols: bool = False
    # ADR-024 §D6 widening overlay: symbol names (mangled or demangled) the
    # user *guarantees* are public even when header provenance can't see them
    # (asm stubs, .def exports, extern "C" shims, MSVC-mangling gaps). Matching
    # findings are forced to stay in-surface under scoping. Widening only ever
    # *keeps* a finding, so it cannot hide a break.
    force_public_symbols: set[str] = field(default_factory=set)
    # Set True when scoping was requested but the public surface could not be
    # resolved, so the step fell back to the full export table (keeps every
    # finding). Consumers surface this as "manual review required" — scoping
    # must never silently read as confident compatibility (issue #235).
    scope_fell_back: bool = False
    # Public surfaces computed by FilterNonPublicSurface, cached here so the
    # caller can reuse them (e.g. surface_scope_confidence) instead of repeating
    # the type-closure walk. None when scoping was not run.
    surf_old: PublicSurface | None = None
    surf_new: PublicSurface | None = None
    # Accumulated side-outputs
    opaque_filtered: list[Change] = field(default_factory=list)
    suppressed: list[Change] = field(default_factory=list)
    redundant: list[Change] = field(default_factory=list)
    kept: list[Change] = field(default_factory=list)
    # ADR-024: findings filtered out as not-public (full audit trail).
    out_of_surface: list[Change] = field(default_factory=list)
    # Set when collapsed version-rename churn was paired with an observed
    # SONAME change. The late SONAME policy should not call that bump
    # unnecessary after this step has moved the matched removals out of kept.
    versioned_scheme_soname_relink_required: bool = False
    # ADR-067 C-S1: the run's conserved policy-disposition ledger. Every
    # suppression application point in this pipeline records into it (with the
    # matched rule's provenance) as it moves a change into ``suppressed``, so
    # the raw-versus-effective totals reconcile by construction rather than
    # being recovered after the fact. ``None`` only for a caller that built a
    # context of its own without one -- recording is then skipped, never
    # faked.
    disposition_ledger: DispositionLedger | None = None
    #: The stand-in for an absent baseline (``old is None``), built by the
    #: snapshot factory (``workflows.snapshot_factory.absent_baseline``) and
    #: handed in by the caller -- this policy-layer context never constructs
    #: a snapshot itself. See :attr:`baseline_or_empty`.
    absent_baseline: AbiSnapshot | None = None

    @property
    def scope_resolved(self) -> bool:
        """Whether a requested public-surface scope actually resolved.

        ``True`` unless scoping was requested and had to fall back to the
        full export table (issue #235: an unconfirmed scope must not read as
        a confidently-clean public surface).
        """
        return not (self.scope_to_public_surface and self.scope_fell_back)

    @property
    def baseline_or_empty(self) -> AbiSnapshot:
        """The OLD side for a step that unions or looks up across both.

        With a baseline that is :attr:`old`. Without one it is an **empty**
        snapshot -- a baseline that does not exist declares nothing,
        exports nothing and carries no graph -- so a union
        (``surface(old) | surface(new)``) reduces to the candidate's own
        and a membership test ("did this declaration exist before?")
        correctly answers no. Deliberately *not* :attr:`new`: substituting
        the candidate would answer that second question "yes, unchanged"
        about a history nobody observed, which is the whole class of claim
        the self-compare this pipeline used to receive was making.

        Only steps that union or look up use this; a step that would
        *compare* the two sides returns early on ``ctx.old is None``
        instead and does not run at all.
        """
        if self.old is not None:
            return self.old
        if self.absent_baseline is None:
            raise ValueError(
                "a run with no baseline must supply absent_baseline "
                "(workflows.snapshot_factory.absent_baseline)"
            )
        return self.absent_baseline

    @classmethod
    def from_rules(
        cls,
        old: AbiSnapshot | None,
        new: AbiSnapshot,
        rules: DispositionRules,
        *,
        ledger: DispositionLedger | None,
        absent_baseline: AbiSnapshot | None = None,
    ) -> PipelineContext:
        """A fresh context for one run of *rules* over *old* -> *new*."""
        return cls(
            old=old,
            new=new,
            suppression=rules.suppression,
            frozen_namespaces=list(rules.frozen_namespaces),
            internal_namespaces=rules.internal_namespaces,
            experimental_namespaces=rules.experimental_namespaces,
            scope_to_public_surface=rules.scope_to_public_surface,
            force_public_symbols=set(rules.force_public_symbols),
            collapse_versioned_symbols=rules.collapse_versioned_symbols,
            public_surface_allowlist=set(rules.public_surface_allowlist)
            if rules.public_surface_allowlist is not None
            else None,
            disposition_ledger=ledger,
            absent_baseline=absent_baseline,
        )
