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

"""Selector-scoped reclassification — the third policy-file primitive.

Two rule forms already exist for steering a verdict, each half-right for a
project that needs the other half:

- ``suppression.py``'s ``Suppression`` — a rich per-symbol/pattern/namespace
  selector grammar, but its only action is *deleting* the finding.
- ``policy_file.py``'s ``overrides:`` block — keeps the finding and changes
  its verdict, but is keyed by ``ChangeKind`` alone, with no selector: the
  override applies to every symbol of that kind, project-wide.

Neither combination lets a project say "every ``func_visibility_changed`` on
*this* symbol family is a known, accepted risk — not a suppression, still
worth seeing, just not BREAKING." A COMDAT-inline-heavy library (oneDAL is
the motivating case) can have dozens of such symbols; downgrading the whole
kind globally via ``overrides:`` would also downgrade a genuine visibility
regression on an unrelated symbol, and suppressing the finding outright
throws away evidence a reviewer may still want to see.

``ReclassifyRule`` is that third form: the same selector grammar
:class:`~abicheck.suppression.Suppression` already implements (``symbol``/
``symbol_pattern``/``type_pattern``/``member_name``/``namespace``/
``entity_namespace``/``cause_namespace``/``source_location``/
``change_kind``/``expires``), with ``to:`` instead of deletion. Reuses
:class:`~abicheck.suppression.Suppression` itself for selector matching
(via the public :meth:`~abicheck.suppression.Suppression.selector_matches`)
rather than re-implementing the glob/regex machinery a second time --
deliberately bypassing that class's reachability / ``allow_public_break``
gates, since those exist to guard against a rule *hiding* evidence, which
does not apply here: a reclassified finding stays in the report, just at a
different verdict.

Loaded as an optional ``reclassify:`` block in a ``--policy-file`` document,
parsed by :mod:`abicheck.policy_file` (which owns the ``to:`` severity
vocabulary via ``parse_severity_value``, the same ``break``/``warn``/
``risk``/``ignore`` spellings ``overrides:`` already uses) and consulted by
:meth:`abicheck.policy_file.PolicyFile.compute_verdict` ahead of the
kind-global ``overrides:`` entry for the same kind, since a rule scoped to a
selector is strictly more specific than one scoped to a bare kind. Format
example::

    reclassify:
      - kind: func_visibility_changed
        symbol_pattern: "_ZN6oneapi3dal.*"
        to: risk
        reason: "COMDAT-inline demotions; consumers already embed their own copy"

Deliberately never imports :mod:`abicheck.suppression`/
:mod:`abicheck.checker_types` at module (or ``TYPE_CHECKING``) scope, even
though it uses :class:`~abicheck.suppression.Suppression` at runtime and
type-annotates against ``Change``. ``checker_types.py`` imports ``PolicyFile``
from ``policy_file.py``, and ``policy_file.py`` is this module's own caller
-- a static edge to either module here would close that loop into a real
import cycle (``policy_file -> reclassify -> suppression -> checker_types ->
policy_file``), which ``scripts/check_ai_readiness.py``'s ``import-cycle-
growth`` gate treats as SCC growth regardless of whether the importing
statement is function-local (its cycle detector walks the whole AST, not
just module scope). Resolving ``Suppression`` via ``importlib.import_module``
at call time (mirroring the lazy ``__getattr__`` shim in
``cli_buildsource.py``, a runtime call rather than a static import edge) is
the sanctioned way around that per CLAUDE.md "What NOT to do" -- extending
``IMPORT_CYCLE_ALLOWLIST`` instead would paper over a real, growing SCC.
``change``/``Change`` parameters are typed ``Any`` for the same reason.

**Known gap, deliberately not closed here (Codex review, P2):**
``contract_pipeline.ContractEvaluationStage.build_context()`` -- the ADR-049
Phase 4 persisted-context assembly under ``compare --contract`` --
folds ``policy_file.overrides`` into the receipt's
``CompatibilityPolicyConfig`` but does not yet do the same for
``policy_file.reclassify``. A finding's ``compatibility_decision`` is
computed correctly either way (``ContractEvaluationStage.classify()`` calls
the same ``severity.effective_verdict_for_change`` this module is wired
into), but the *audit receipt* -- the JSON/replay record of what actually
scored the run -- doesn't yet record which reclassify rule, reason, or
expiry decided it. Closing this needs the resolved reclassify list threaded
through ``CompatibilityPolicyConfig``, its JSON serialization (a real
schema-version concern -- see ``contract_context_io.py``), and
``contract_replay.py``'s policy-independent replay evaluator -- a scoped,
independently-verified follow-up, not a same-PR extension of this change.

**This module also owns the per-change effective-verdict resolver**
(:func:`effective_verdict_for_change`) and its disclosure sibling
(:func:`reclassify_rule_for_change`), which moved here from ``severity.py``
during ADR-061 Phase 2. Two reasons, one architectural and one local:

- Architectural: ``checker_types.DiffResult``'s verdict buckets
  (``breaking``/``source_breaks``/``compatible``/``risk``) and
  ``severity.py``'s severity/gating layer both need that resolver, and
  ADR-061 classifies those two callers into *different* responsibility
  layers (``compare`` and ``policy``) whose dependency contract forbids the
  first importing the second. This module is the leaf both may depend on --
  the "pull the shared logic out to a leaf both sides can depend on" pattern
  ADR-061 names for exactly this class of blocker. It stays deliberately
  unclassified until ``checker_policy.py``'s own model/policy split lands,
  which is what will decide the leaf's final owner.
- Local: the resolver's precedence chain is built *around* the selector
  rules defined here, and :func:`reclassify_rule_for_change` has to mirror
  that chain step for step. Three separate review rounds have already
  corrected a disagreement between the two; co-locating them puts both
  implementations of one precedence order in one file.

``severity.effective_verdict_for_change`` / ``severity.reclassify_rule_for_change``
/ ``severity.KindSets`` remain importable and unchanged -- ``severity.py``
re-exports all three, so no caller (in this repo or out of it) moved.
"""

from __future__ import annotations

import importlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, cast

from .checker_policy import (
    API_BREAK_KINDS,
    BREAKING_KINDS,
    COMPATIBLE_KINDS,
    RISK_KINDS,
    ChangeKind,
    HasKind,
    Verdict,
    effective_category,
    policy_kind_sets,
)
from .model.policy_file_protocol import ReclassifyRuleProtocol

#: The four verdicts a `to:` value is allowed to resolve to -- the exact set
#: `policy_file.parse_severity_value`'s `break`/`warn`/`risk`/`ignore`
#: vocabulary maps onto. `NO_CHANGE` is excluded: a `ReclassifyRule`
#: constructed directly in Python (bypassing that parser -- this class is
#: public API) with `to_verdict=Verdict.NO_CHANGE` would make a matching
#: real change disappear from every one of `DiffResult.breaking`/
#: `source_breaks`/`risk`/`compatible` (Codex review) -- a silently
#: passing result that conceals a real change, not a lenient reclassification.
_VALID_RECLASSIFY_VERDICTS: frozenset[Verdict] = frozenset({
    Verdict.BREAKING,
    Verdict.API_BREAK,
    Verdict.COMPATIBLE_WITH_RISK,
    Verdict.COMPATIBLE,
})

#: The canonical ``to:`` spelling for each valid reclassify verdict --
#: policy_file.py's `_SEVERITY_MAP` (``break``/``warn``/``risk``/``ignore``)
#: read in reverse. Duplicated here rather than imported (a static import of
#: policy_file.py would close the exact import cycle the module docstring
#: describes) -- this is the whole vocabulary, four entries, and stable by
#: construction (`_VALID_RECLASSIFY_VERDICTS` names the same four verdicts).
#: Used by `ReclassifyRule.__post_init__` to canonicalize `self.to` so it can
#: never disagree with `self.to_verdict` (Codex review).
_CANONICAL_TO_SPELLING: dict[Verdict, str] = {
    Verdict.BREAKING: "break",
    Verdict.API_BREAK: "warn",
    Verdict.COMPATIBLE_WITH_RISK: "risk",
    Verdict.COMPATIBLE: "ignore",
}


def _suppression_cls() -> Any:
    """Resolve :class:`abicheck.suppression.Suppression` at call time --
    see the module docstring for why this can't be a static import."""
    return importlib.import_module("abicheck.suppression").Suppression


#: YAML keys accepted in one ``reclassify:`` entry. ``kind`` is this rule
#: form's own spelling of ``Suppression.change_kind`` (matching the shape of
#: the user-facing example this module's docstring documents), not a second,
#: independent selector.
RECLASSIFY_KNOWN_KEYS: frozenset[str] = frozenset(
    {
        "kind",
        "symbol",
        "symbol_pattern",
        "type_pattern",
        "member_name",
        "namespace",
        "entity_namespace",
        "cause_namespace",
        "source_location",
        "binding",
        "to",
        "reason",
        "label",
        "expires",
    }
)


@dataclass
class ReclassifyRule:
    """One selector-scoped reclassification rule.

    Attributes:
        to_verdict: The verdict this rule forces when its selectors match —
            already resolved from the raw ``to:`` spelling by the loader
            (:func:`abicheck.policy_file.parse_severity_value`), so this
            class carries no severity vocabulary of its own.
        to: The ``to:`` spelling, for :meth:`describe`/:meth:`to_report_dict`
            audit output. Canonicalized in ``__post_init__`` to always match
            ``to_verdict`` (see there) -- for a policy-file-loaded rule this
            is simply the value the loader already passed in (the loader
            derives ``to_verdict`` from this same spelling, so they already
            agree); a directly-constructed rule (a public API surface) that
            passes an inconsistent or omitted ``to`` gets it overwritten with
            the canonical spelling for its ``to_verdict`` instead.
        symbol, symbol_pattern, type_pattern, member_name, namespace,
        entity_namespace, cause_namespace, source_location, binding,
        change_kind, expires: Same selector grammar and semantics as the
            identically named :class:`~abicheck.suppression.Suppression`
            fields — see that class's docstrings for each. At least one
            selector is required, enforced by the same validation
            :class:`Suppression` already performs. ``binding`` is
            conjunctive-only there and stays that way here.
        reason: Optional human-readable justification, for audit output.
        label: Optional grouping tag, mirroring
            :attr:`Suppression.label`.
    """

    to_verdict: Verdict
    to: str = ""
    symbol: str | None = None
    symbol_pattern: str | None = None
    type_pattern: str | None = None
    member_name: str | None = None
    namespace: str | None = None
    entity_namespace: str | None = None
    cause_namespace: str | None = None
    source_location: str | None = None
    change_kind: str | None = None
    reason: str | None = None
    label: str | None = None
    expires: date | None = None
    #: ELF symbol linkage selector -- see ``Suppression.binding`` for the
    #: full grammar/caveat (provider-side evidence only, conjunctive-only:
    #: can narrow another selector here but never stand alone). Added after
    #: `expires` (Codex review, fresh evidence: this rule form otherwise
    #: fell out of sync with `Suppression`'s selector grammar).
    binding: str | None = None
    #: Built at construction time and reused for every :meth:`matches` call
    #: rather than re-validated/re-compiled per call — mirrors how
    #: :class:`~abicheck.suppression.Suppression` itself eagerly compiles its
    #: own patterns. Typed ``Any`` (really a ``Suppression`` instance) --
    #: see the module docstring.
    _selector: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.to_verdict not in _VALID_RECLASSIFY_VERDICTS:
            raise ValueError(
                f"Invalid to_verdict {self.to_verdict!r}. Valid values: "
                f"{sorted(v.value for v in _VALID_RECLASSIFY_VERDICTS)}"
            )
        # Canonicalize `to` from `to_verdict` rather than trusting a caller-
        # supplied spelling (Codex review, second round on the same audit-
        # trail concern -- the earlier fix only patched reporter.py's own
        # reclassified_by fallback; describe() still reads self.to directly,
        # so Markdown/HTML disclosure could still misdescribe a directly-
        # constructed rule whose `to` disagreed with, or omitted,
        # `to_verdict`). This is a no-op for every policy-file-loaded rule:
        # the loader always derives to_verdict FROM this same spelling, so
        # they already agree and this simply reassigns the same string.
        # `to_verdict` is the single source of truth; `to` is always its
        # derived display spelling.
        self.to = _CANONICAL_TO_SPELLING[self.to_verdict]
        # A datetime is itself a date subclass, so a Python caller
        # constructing this rule directly with expires=datetime(...) would
        # otherwise pass isinstance(expires, date) unnormalized and later
        # crash Suppression.is_expired()'s `date.today() > self.expires`
        # comparison (TypeError: can't compare date and datetime) -- the
        # same YAML-unquoted-timestamp case policy_file.py's
        # _parse_reclassify_expires already normalizes for the loader path,
        # applied here too so direct construction (a public API surface,
        # not just the policy-file loader) doesn't crash on the identical
        # input shape (Codex review).
        if isinstance(self.expires, datetime):
            self.expires = self.expires.date()
        # Delegates all selector validation (mutual exclusivity, unknown
        # change_kind, malformed glob/regex, "at least one selector") to
        # Suppression's own __post_init__ -- a ValueError raised there
        # propagates unchanged to this rule's own construction, so a
        # ReclassifyRule can never exist with an invalid selector any more
        # than a Suppression can.
        self._selector = _suppression_cls()(
            symbol=self.symbol,
            symbol_pattern=self.symbol_pattern,
            type_pattern=self.type_pattern,
            member_name=self.member_name,
            namespace=self.namespace,
            entity_namespace=self.entity_namespace,
            cause_namespace=self.cause_namespace,
            source_location=self.source_location,
            change_kind=self.change_kind,
            expires=self.expires,
            binding=self.binding,
        )

    def matches(self, change: Any, today: date | None = None) -> bool:
        """Return True if this rule's selectors match *change* (an
        :class:`abicheck.checker_types.Change`; typed ``Any`` -- see the
        module docstring).

        Expired rules (past ``expires``) never match, same as
        :class:`Suppression`. Deliberately consults only the selector
        grammar -- see the module docstring for why the reachability /
        ``allow_public_break`` gates :class:`Suppression` layers on top
        don't apply to reclassification.
        """
        return bool(self._selector.selector_matches(change, today))

    def is_expired(self, today: date | None = None) -> bool:
        """Return True if this rule has passed its ``expires`` date.

        ``None`` when unset, same as :meth:`Suppression.is_expired`. Used by
        the report renderers (``reporter.py``'s ``policy_reclassify``,
        ``reporter_markdown.py``, ``html_report.py``, ``sarif.py``) to
        exclude an expired rule from the *active* rule set they disclose --
        listing an expired rule there would claim a downgrade is in effect
        when :meth:`matches` would actually already refuse to apply it.
        """
        return bool(self._selector.is_expired(today))

    def describe(self) -> str:
        """One-line human-readable summary, for policy audit output."""
        bits = [f"to={self.to or self.to_verdict.value}"]
        for field_name in (
            "change_kind",
            "symbol",
            "symbol_pattern",
            "type_pattern",
            "member_name",
            "namespace",
            "entity_namespace",
            "cause_namespace",
            "source_location",
            "binding",
        ):
            val = getattr(self, field_name)
            if val is not None:
                bits.append(f"{field_name}={val!r}")
        if self.reason:
            bits.append(f"reason={self.reason!r}")
        # Codex review: the Markdown/HTML report renderers call describe()
        # to disclose an active rule, but a rule with no other reason to
        # mention it (or with a label alongside a reason) was silently
        # missing its own expiry -- a reader of those two formats had no
        # way to tell when a temporary waiver stops applying. to_report_dict()
        # already included both; describe() should too.
        if self.label:
            bits.append(f"label={self.label!r}")
        if self.expires is not None:
            bits.append(f"expires={self.expires.isoformat()!r}")
        return "reclassify(" + ", ".join(bits) + ")"

    def to_report_dict(self) -> dict[str, str]:
        """JSON-serializable audit dict for this rule, shared by every report
        renderer (``reporter.py``'s ``policy_reclassify``, ``sarif.py``'s
        ``policyReclassify``) so the field set/spelling can't silently drift
        between them the way two hand-rolled dict-builders eventually would.

        Lists this rule's own configuration -- the *active rule set*, not a
        per-finding "did this rule fire" attribution (see the module
        docstring's known-gap note). Keys present depend on which fields the
        rule actually set; ``to`` is always present.
        """
        out: dict[str, str] = {"to": self.to_verdict.value}
        if self.change_kind is not None:
            out["kind"] = self.change_kind
        for field_name in (
            "symbol", "symbol_pattern", "type_pattern", "member_name",
            "namespace", "entity_namespace", "cause_namespace",
            "source_location", "binding",
        ):
            val = getattr(self, field_name)
            if val is not None:
                out[field_name] = val
        if self.reason:
            out["reason"] = self.reason
        if self.label:
            out["label"] = self.label
        if self.expires is not None:
            out["expires"] = self.expires.isoformat()
        return out


def first_matching_reclassify_verdict(
    rules: list[ReclassifyRule], change: Any, today: date | None = None
) -> Verdict | None:
    """Return the ``to_verdict`` of the first rule in *rules* matching
    *change*, or ``None`` if none match.

    First-match-wins, in file order — unlike suppression (where every
    matching rule has the identical effect, "delete"), two reclassify rules
    can disagree about *what* verdict to apply to the same change, so rule
    order is meaningful. Documented in ``policy_file.py``'s ``reclassify:``
    format and enforced here as the one place this resolution happens.
    """
    for rule in rules:
        if rule.matches(change, today):
            return rule.to_verdict
    return None


def active_reclassify_rules(
    rules: Sequence[ReclassifyRuleProtocol], today: date | None = None
) -> list[ReclassifyRuleProtocol]:
    """Return the subset of *rules* not yet past their ``expires`` date.

    Every report renderer disclosing the *active* rule set (``reporter.py``'s
    ``policy_reclassify``, ``reporter_markdown.py``, ``html_report.py``,
    ``sarif.py``) filters through this rather than listing every configured
    rule verbatim (Codex review) -- an expired rule can never actually match
    (:meth:`ReclassifyRule.matches`), so disclosing it as active claims a
    downgrade is in effect when it no longer is.

    Typed against :class:`~abicheck.model.policy_file_protocol.
    ReclassifyRuleProtocol`/``Sequence`` rather than the concrete
    ``ReclassifyRule``/``list`` (ADR-061 Phase 4's ``PolicyFile``
    investigation): a real ``list[ReclassifyRule]`` still satisfies this
    signature structurally (``Sequence`` is covariant), but so does the
    ``Sequence[ReclassifyRuleProtocol]`` a ``DiffResult.policy_file.
    reclassify`` read now yields once that field is typed against
    ``PolicyFileProtocol``.
    """
    return [r for r in rules if not r.is_expired(today)]


# ---------------------------------------------------------------------------
# Per-change effective-verdict resolution (moved from severity.py, ADR-061
# Phase 2 -- see this module's docstring for why it lives here)
# ---------------------------------------------------------------------------

#: Pre-computed (breaking, api_break, compatible, risk) kind sets.
KindSets = tuple[
    frozenset[ChangeKind],
    frozenset[ChangeKind],
    frozenset[ChangeKind],
    frozenset[ChangeKind],
]

_VERDICT_ORDER = [
    Verdict.NO_CHANGE,
    Verdict.COMPATIBLE,
    Verdict.COMPATIBLE_WITH_RISK,
    Verdict.API_BREAK,
    Verdict.BREAKING,
]


def resolve_kind_sets(
    policy: str | None = None,
    kind_sets: KindSets | None = None,
) -> KindSets:
    """Return (breaking, api_break, compatible, risk) kind sets.

    *kind_sets* takes precedence when provided (e.g. from
    ``DiffResult._effective_kind_sets()`` which includes PolicyFile overrides).
    Falls back to ``policy_kind_sets(policy)`` or canonical sets.
    """
    if kind_sets is not None:
        return kind_sets
    if policy is None or policy == "strict_abi":
        return (
            frozenset(BREAKING_KINDS),
            frozenset(API_BREAK_KINDS),
            frozenset(COMPATIBLE_KINDS),
            RISK_KINDS,
        )
    return policy_kind_sets(policy)


def _has_frozen_namespace_violation(change: HasKind) -> bool:
    """Return True only for a real frozen-namespace tag string."""
    fnv = getattr(change, "frozen_namespace_violation", None)
    return isinstance(fnv, str) and bool(fnv)


def _raw_verdict_for_kind(kind: ChangeKind, kind_sets: KindSets) -> Verdict:
    """Return the verdict for *kind* without per-finding overrides."""
    breaking, api_break, compatible, risk = kind_sets
    if kind in breaking:
        return Verdict.BREAKING
    if kind in api_break:
        return Verdict.API_BREAK
    if kind in risk:
        return Verdict.COMPATIBLE_WITH_RISK
    if kind in compatible:
        return Verdict.COMPATIBLE
    return Verdict.BREAKING


def effective_verdict_for_change(
    change: HasKind,
    *,
    policy: str | None = None,
    kind_sets: KindSets | None = None,
    policy_file: object | None = None,
    today: date | None = None,
) -> Verdict:
    """Return the effective verdict for one change.

    Policy-file overrides usually move an entire ``ChangeKind`` into another
    verdict bucket. Frozen-namespace violations are deliberately per-change: if
    an override would downgrade a tagged finding below the base-policy verdict,
    the override is ignored for that one finding.

    *today* is forwarded to a matching `reclassify:` rule's own expiry check
    (:func:`first_matching_reclassify_verdict`) -- pass a fixed date for a
    deterministic/testable caller (e.g. ``SuppressionList.audit()``'s own
    *today* parameter); ``None`` uses the real current date, same as every
    other caller already relies on.
    """
    kind = change.kind
    base_policy = getattr(policy_file, "base_policy", policy)
    base_sets = (
        resolve_kind_sets(base_policy, None)
        if policy_file is not None
        else resolve_kind_sets(base_policy, kind_sets)
    )

    eff = getattr(change, "effective_verdict", None)
    if isinstance(eff, Verdict):
        raw_v = _raw_verdict_for_kind(kind, base_sets)
        if (
            _has_frozen_namespace_violation(change)
            and _VERDICT_ORDER.index(eff) < _VERDICT_ORDER.index(raw_v)
        ):
            return raw_v
        return eff

    # A: selector-scoped reclassification (the rules above) -- consulted
    # ahead of the kind-global `overrides` below, mirroring
    # PolicyFile._resolve_change_verdict's own priority order exactly, so
    # this per-finding resolver (severity/category buckets, JSON/HTML/SARIF
    # labels, severity-based gating) agrees with the legacy verdict
    # PolicyFile.compute_verdict already computes instead of silently
    # re-deriving a different answer for the same change.
    reclassify_rules = (
        getattr(policy_file, "reclassify", None) if policy_file is not None else None
    )
    if reclassify_rules:
        reclass_v = first_matching_reclassify_verdict(reclassify_rules, change, today)
        if reclass_v is not None:
            base_v = effective_category(change, *base_sets)
            if (
                _has_frozen_namespace_violation(change)
                and _VERDICT_ORDER.index(reclass_v) < _VERDICT_ORDER.index(base_v)
            ):
                return base_v
            return reclass_v

    overrides = (
        getattr(policy_file, "overrides", None)
        if policy_file is not None
        else None
    )
    if overrides and kind in overrides:
        base_v = effective_category(change, *base_sets)
        override_v = cast(Verdict, overrides[kind])
        if (
            _has_frozen_namespace_violation(change)
            and _VERDICT_ORDER.index(override_v) < _VERDICT_ORDER.index(base_v)
        ):
            return base_v
        return override_v
    # Reuses `base_sets` (already computed above from `policy_file.
    # base_policy` when a policy_file is given) rather than recomputing from
    # the outer `policy`/`kind_sets` parameters directly (Codex review,
    # pre-existing bug surfaced by suppression.py's audit() calling this
    # with only `policy_file=` set, no `policy=`/`kind_sets=`): for a
    # policy_file whose base_policy isn't strict_abi (e.g. plugin_abi), a
    # finding with no effective_verdict/reclassify/override match fell all
    # the way back to strict_abi's own kind sets, silently ignoring the
    # policy file's own base policy. For the no-policy_file case this is a
    # pure simplification, not a behavior change: base_sets there is already
    # computed as `resolve_kind_sets(policy, kind_sets)` -- identical to
    # what this line used to recompute.
    return effective_category(change, *base_sets)


def reclassify_rule_for_change(
    change: HasKind, policy_file: object | None, today: date | None = None
) -> Any | None:
    """Return the ``ReclassifyRule`` that actually decided *change*'s
    effective verdict, or ``None`` if no rule did.

    Mirrors :func:`effective_verdict_for_change`'s own precedence exactly: a
    rule that *matches* but is shadowed by a higher-priority
    ``effective_verdict`` (an ADR-027 pipeline modulation) or blocked by the
    frozen-namespace verdict floor did not actually decide the change's
    verdict, so it is not "the reclassifying rule" for disclosure purposes
    even though :meth:`ReclassifyRule.matches` would say yes.

    Used by ``reporter.py`` to stamp a per-change ``reclassified_by`` field
    on the JSON report (Codex review: ``cli_pr_comment``'s
    ``pr_comment._reclassified_count()`` only recognized the kind-global
    ``policy_overrides`` map, so a PR comment silently omitted the
    "reclassified by --policy-file" notice for a finding downgraded by a
    selector-scoped ``reclassify:`` rule instead). Computing this from the
    real ``Change`` object here -- rather than having ``pr_comment.py``
    reimplement selector matching against the JSON report alone -- is
    deliberate: a JSON-serialized change doesn't carry every selector field a
    rule can match on (``type_pattern``/``member_name``/``namespace``/
    ``entity_namespace``/``cause_namespace`` have no JSON counterpart), so a
    JSON-only reimplementation could not be sound.

    A matching rule whose ``to_verdict`` merely *restates* the verdict the
    next-priority path (a same-kind ``overrides:`` entry, or the base policy)
    would already have produced is a no-op, not a reclassification -- e.g.
    ``func_removed: to: break`` under ``strict_abi``, where ``func_removed``
    is already BREAKING (Codex review: a matching-but-no-op rule was still
    stamping ``reclassified_by``, making the PR comment falsely report a
    downgrade that never happened). Only a rule that actually *changes* the
    verdict from what would apply in its absence counts as deciding it. That
    comparison verdict is computed through the identical frozen-namespace
    floor the ``overrides:`` branch below applies -- not the override's raw
    value (Codex review, second round: a frozen-namespace finding with e.g.
    ``overrides: func_removed: ignore`` plus ``reclassify: ... to: break``
    would, absent the rule, already clamp back to BREAKING via the floor;
    comparing against the raw COMPATIBLE override instead made the rule read
    as deciding a verdict that was already going to be BREAKING anyway).
    """
    if isinstance(getattr(change, "effective_verdict", None), Verdict):
        return None
    rules = (
        getattr(policy_file, "reclassify", None) if policy_file is not None else None
    )
    if not rules:
        return None
    base_policy = getattr(policy_file, "base_policy", None)
    base_sets = resolve_kind_sets(base_policy, None)
    base_v = effective_category(change, *base_sets)
    overrides = (
        getattr(policy_file, "overrides", None) if policy_file is not None else None
    )
    kind = change.kind
    # The verdict that would apply if *this* reclassify rule didn't exist --
    # the next step down the same precedence chain effective_verdict_for_change
    # walks (a same-kind overrides: entry, else the base policy's own
    # verdict), with the identical frozen-namespace floor clamp the overrides:
    # branch below applies -- so a rule that merely restates it is recognized
    # as a no-op regardless of which of the two it happens to restate, and
    # regardless of whether the floor would already have clamped it.
    if overrides and kind in overrides:
        override_v = cast(Verdict, overrides[kind])
        if (
            _has_frozen_namespace_violation(change)
            and _VERDICT_ORDER.index(override_v) < _VERDICT_ORDER.index(base_v)
        ):
            next_priority_v = base_v
        else:
            next_priority_v = override_v
    else:
        next_priority_v = base_v
    for rule in rules:
        if rule.matches(change, today):
            reclass_v = rule.to_verdict
            # The verdict this matching rule *actually* produces, applying
            # the identical frozen-namespace floor
            # effective_verdict_for_change's own reclassify branch applies:
            # when reclass_v is blocked, that branch returns base_v directly
            # -- it never falls through to consult overrides:, even though
            # overrides: would have applied had no reclassify rule matched
            # at all (Codex review, third round: a blocked rule was
            # previously always read as a no-op, but the real effective
            # verdict it produces -- base_v -- can still differ from
            # next_priority_v, e.g. a global `overrides: ... to: break` with
            # a frozen-namespace `reclassify: ... to: ignore` blocked back
            # to a weaker base_v than the override would have given: the
            # rule genuinely changed the outcome from what overrides: alone
            # would have produced, just not to the verdict it asked for).
            effective_v = (
                base_v
                if (
                    _has_frozen_namespace_violation(change)
                    and _VERDICT_ORDER.index(reclass_v) < _VERDICT_ORDER.index(base_v)
                )
                else reclass_v
            )
            if effective_v == next_priority_v:
                return None
            return rule
    return None
