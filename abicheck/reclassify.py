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
Phase 4 persisted-context assembly under ``compare --contract-evaluation`` --
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
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from .checker_policy import Verdict

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
        entity_namespace, cause_namespace, source_location, change_kind,
        expires: Same selector grammar and semantics as the identically
            named :class:`~abicheck.suppression.Suppression` fields —
            see that class's docstrings for each. At least one selector
            is required, enforced by the same validation
            :class:`Suppression` already performs.
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
            "source_location",
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
    rules: list[ReclassifyRule], today: date | None = None
) -> list[ReclassifyRule]:
    """Return the subset of *rules* not yet past their ``expires`` date.

    Every report renderer disclosing the *active* rule set (``reporter.py``'s
    ``policy_reclassify``, ``reporter_markdown.py``, ``html_report.py``,
    ``sarif.py``) filters through this rather than listing every configured
    rule verbatim (Codex review) -- an expired rule can never actually match
    (:meth:`ReclassifyRule.matches`), so disclosing it as active claims a
    downgrade is in effect when it no longer is.
    """
    return [r for r in rules if not r.is_expired(today)]
