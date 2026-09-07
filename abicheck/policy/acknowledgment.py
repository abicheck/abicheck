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

"""ADR-067 D5 (plan workstream C-S3): change-intent acknowledgment records.

An acknowledgment is a **distinct disposition** from a suppression (AGENTS.md
"Record before disposing": an acknowledgment is intentional acceptance of a
known change; a suppression is a claimed false positive or a deliberate scope
exclusion). A suppressed finding disappears from the visible change set; an
acknowledged one keeps its verdict class, stays in the report, and still
contributes to the gate according to policy (D5) — acknowledging a break
never pretends it is compatible.

**Same grammar, never a second one.** Like :class:`~abicheck.suppression.
Suppression`, an :class:`Acknowledgment` constructs and matches through
:class:`~abicheck.policy.selectors.SelectorSet` (ADR-063 D10) rather than a
parallel matching engine, and :class:`AcknowledgmentList` loads from the
identical YAML shape (``version: 1`` plus a top-level list key) that
:class:`~abicheck.suppression.SuppressionList` already uses.

**Deliberately bounded — the one place this record type diverges from
suppression's grammar.** ``vision.md``'s "Change governance and evolution"
section states the normative requirement this module exists to enforce
verbatim: *"Changes can be acknowledged with explicit, reviewable context
bounded to specific findings, components, and release ranges... A baseline
refresh or a broad ignore rule is not an acknowledgment of everything it
happens to cover."* D5 restates the same bound from the suppression side: *"a
broad regex is a suppression, not an acknowledgment."* Concretely: an
acknowledgment's selector is restricted to :attr:`Acknowledgment.finding_id`
(the producer-agnostic canonical identity, preferred) and/or an exact
:attr:`Acknowledgment.symbol` plus optional :attr:`Acknowledgment.change_kind`
— never a pattern, a namespace glob, a source-location glob, or a member-name
regex. :class:`AcknowledgmentList.load` rejects any of those broader
suppression-only keys outright (see ``_FORBIDDEN_BROAD_KEYS``), so a project
cannot write what is really a suppression rule into an acknowledgment
document and have it silently accepted as bounded.

**Ambiguity is a hard error, never a silent nearest-match.** D5: *"an
ambiguous or unknown identity requires review and is never resolved to the
nearest old acknowledgment."* :meth:`AcknowledgmentList.evaluate` raises
:class:`AmbiguousAcknowledgmentError` the moment more than one loaded record
matches the same change, rather than picking the first (or "most similar")
one.

**Shared record-id scheme with workstream B (ADR-066 longitudinal
history).** :meth:`Acknowledgment.record_id` is built from exactly the
fields :mod:`abicheck.workflows.history` already keys a release by --
``component`` (History's own per-library key) plus ``baseline``/``candidate``
(the same free-form version-label strings ``HistoryEntry.version`` carries) --
so an acknowledgment naming a ``candidate`` release label can be joined
against a later history run's own ``version`` field without a second id
scheme. B's own versioning-policy resolution (ADR-066 S2) is not required for
this: the labels are read and compared as opaque strings, exactly how S1's
history chain already treats them.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from .selectors import SelectorSet

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..checker_types import Change

#: Keys an acknowledgment entry may carry. Deliberately excludes every
#: *broad*-shaped suppression selector (``symbol_pattern``, ``type_pattern``,
#: ``member_name``, ``namespace``, ``entity_namespace``, ``cause_namespace``,
#: ``source_location``, ``binding``) -- see the module docstring's "bounded"
#: section. A document that names one of those gets a specific error telling
#: the author it belongs in a suppression file instead of a generic
#: "unknown key".
_KNOWN_ENTRY_KEYS: frozenset[str] = frozenset({
    "finding_id", "symbol", "change_kind",
    "component", "baseline", "candidate",
    "reason", "reference", "expires",
})

#: Suppression-only broad selectors that would make an acknowledgment an
#: unbounded rule rather than a record about specific findings.
_FORBIDDEN_BROAD_KEYS: frozenset[str] = frozenset({
    "symbol_pattern", "type_pattern", "member_name", "namespace",
    "entity_namespace", "cause_namespace", "source_location", "binding",
    "reachability", "allow_public_break", "allow_unknown_reachability",
    "label",
})


class AmbiguousAcknowledgmentError(ValueError):
    """More than one acknowledgment record matched the same change (D5).

    D5 is explicit that this case "requires review" rather than being
    silently resolved to the record that happens to appear first (or looks
    "closest") in the document -- the two (or more) records disagree about
    something (component, release range, reason) that a human has to settle.
    """


@dataclass
class Acknowledgment:
    """One ADR-067 D5 acknowledgment record.

    Matched by :attr:`finding_id` (the stable, backend-independent identity
    :func:`~abicheck.finding_identity.report_canonical_finding_id` computes)
    or, failing that, an exact :attr:`symbol` (optionally narrowed by
    :attr:`change_kind`) -- never a pattern (see the module docstring). At
    least one of :attr:`finding_id`/:attr:`symbol` is required, and
    :attr:`reason` is required and non-empty: D5's "a reason" is not optional
    the way :class:`~abicheck.suppression.Suppression`'s own ``reason`` is,
    because an acknowledgment with no stated reason is exactly the kind of
    accidental-broad-acceptance vision.md's invariant warns against.
    """

    finding_id: str | None = None
    """Preferred selector: the change's canonical finding identity
    (``report_canonical_finding_id``), stable across an ``--ast-frontend``
    switch and any anonymous-type renaming — see
    :attr:`abicheck.suppression.Suppression.finding_id` for the identical
    contract."""
    symbol: str | None = None
    """Exact-match fallback selector when no ``finding_id`` is recorded yet.
    Never a pattern — see the module docstring's "bounded" section."""
    change_kind: str | None = None
    """Optional narrowing of :attr:`symbol` to one specific ``ChangeKind``."""
    component: str | None = None
    """The library/package/variant this record applies to (ADR-066's
    per-entity component key) — ``None`` matches regardless of component,
    the same "unset field never scopes" convention every other selector
    grammar in this codebase uses."""
    baseline: str | None = None
    """The release label (``AbiSnapshot.version``/``git_tag`` spelling, or
    workflows.history's own ``HistoryEntry.version``) this acknowledgment's
    change transition started from. Documentary — matching itself only reads
    :attr:`candidate` (see :meth:`matches`)."""
    candidate: str | None = None
    """The release label this acknowledgment applies as of. ``None`` matches
    any release (an acknowledgment good for every future release of this
    finding, e.g. one that will never be un-broken); a stated value scopes
    the record to that one release the same way :attr:`component` scopes to
    one library — a caller not supplying a ``release_label`` at all cannot
    satisfy a stated ``candidate``, per D5's fail-closed-on-unknown-scope
    rule (see :meth:`matches`)."""
    reason: str = ""
    """Required, non-empty justification — see this class's own docstring."""
    reference: str | None = None
    """Optional external reference (an issue/PR URL, a design doc)."""
    expires: date | None = None
    """Optional expiry — an expired acknowledgment never matches (mirrors
    :attr:`abicheck.suppression.Suppression.expires`)."""
    _selector: SelectorSet = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.expires, datetime):
            self.expires = self.expires.date()
        if not self.reason or not self.reason.strip():
            raise ValueError(
                "Acknowledgment must have a non-empty 'reason' — vision.md's "
                "governance model requires an acknowledgment to carry "
                "explicit, reviewable context, not a bare rule"
            )
        if self.finding_id is None and self.symbol is None:
            raise ValueError(
                "Acknowledgment must have 'finding_id' or 'symbol' — a bounded, "
                "specific match. A broad selector (pattern/namespace/"
                "source_location) is a suppression, not an acknowledgment "
                "(ADR-067 D5)."
            )
        if self.change_kind is not None and self.symbol is None and self.finding_id is None:
            raise ValueError(
                "'change_kind' narrows 'symbol' or 'finding_id' and cannot "
                "stand alone"
            )
        # `finding_id`/`symbol`/`change_kind` are exactly the bounded subset
        # of SelectorSet's grammar this record type permits — see the module
        # docstring. SelectorSet's own "at least one selector" validation is
        # satisfied by whichever of finding_id/symbol is present.
        self._selector = SelectorSet(
            symbol=self.symbol,
            change_kind=self.change_kind,
            finding_id=self.finding_id,
            expires=self.expires,
        )

    def is_expired(self, today: date | None = None) -> bool:
        return self._selector.is_expired(today)

    def matches(
        self,
        change: Change,
        *,
        component: str | None = None,
        release_label: str | None = None,
        today: date | None = None,
    ) -> bool:
        """True if this record acknowledges *change* in the given scope.

        *component*/*release_label* are the caller's own declared scope for
        this run (e.g. the library name being compared, and the candidate
        snapshot's version label). A record that names a component/candidate
        the caller did not supply, or that disagrees with what was supplied,
        never matches — D5's bound is enforced by refusing an unverifiable
        match, never by "resolving to the nearest" one (see the module
        docstring).
        """
        canonical_finding_id: str | None = None
        if self.finding_id is not None:
            from ..finding_identity import report_canonical_finding_id

            canonical_finding_id = report_canonical_finding_id(change)
        if not self._selector.matches_selectors(
            change, today=today, canonical_finding_id=canonical_finding_id
        ):
            return False
        if self.component is not None and self.component != component:
            return False
        if self.candidate is not None and self.candidate != release_label:
            return False
        return True

    def record_id(self) -> str:
        """The shared identifier scheme (see the module docstring).

        ``component::baseline::candidate::finding_id-or-symbol`` — every
        segment falls back to ``*`` when unset, so two records that differ
        only in an unset field never collide, and the id stays stable across
        a re-load of the same document (it derives from the record's own
        content, not its position in the file).
        """
        subject = self.finding_id or self.symbol or ""
        return "::".join(
            [self.component or "*", self.baseline or "*", self.candidate or "*", subject]
        )

    def to_dict(self) -> dict[str, object]:
        entry: dict[str, object] = {"reason": self.reason}
        if self.finding_id is not None:
            entry["finding_id"] = self.finding_id
        if self.symbol is not None:
            entry["symbol"] = self.symbol
        if self.change_kind is not None:
            entry["change_kind"] = self.change_kind
        if self.component is not None:
            entry["component"] = self.component
        if self.baseline is not None:
            entry["baseline"] = self.baseline
        if self.candidate is not None:
            entry["candidate"] = self.candidate
        if self.reference is not None:
            entry["reference"] = self.reference
        if self.expires is not None:
            entry["expires"] = self.expires.isoformat()
        return entry


class AcknowledgmentList:
    """A loaded set of :class:`Acknowledgment` records — the D5 counterpart
    of :class:`~abicheck.suppression.SuppressionList`."""

    def __init__(
        self,
        acknowledgments: list[Acknowledgment],
        *,
        source_sha256: str | None = None,
        source_path: str | None = None,
    ) -> None:
        self._acknowledgments = acknowledgments
        self.source_sha256 = source_sha256
        self.source_path = source_path

    def __len__(self) -> int:
        return len(self._acknowledgments)

    def __iter__(self) -> Iterator[Acknowledgment]:
        return iter(self._acknowledgments)

    @classmethod
    def load(cls, path: Path) -> AcknowledgmentList:
        """Load acknowledgment records from a YAML file.

        Same envelope as :meth:`abicheck.suppression.SuppressionList.load`
        (``version: 1``, raw-byte digest before decoding) — see that
        method's docstring for why the digest is computed over undecoded
        bytes.
        """
        try:
            raw_bytes = path.read_bytes()
        except OSError as e:
            raise OSError(f"Cannot read acknowledgment file {path}: {e}") from e
        digest = hashlib.sha256(raw_bytes).hexdigest()
        text = raw_bytes.decode("utf-8")

        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in acknowledgment file: {e}") from e

        if data is None:
            return cls([], source_sha256=digest, source_path=str(path))
        if not isinstance(data, dict):
            raise ValueError("Acknowledgment file must be a YAML mapping")

        version = data.get("version")
        if version != 1:
            raise ValueError(
                f"Unsupported acknowledgment file version: {version!r} (expected 1)"
            )

        raw_entries = data.get("acknowledgments")
        if raw_entries is None:
            return cls([], source_sha256=digest, source_path=str(path))
        if not isinstance(raw_entries, list):
            raise ValueError("'acknowledgments' must be a list")

        records: list[Acknowledgment] = []
        for i, item in enumerate(raw_entries):
            if not isinstance(item, dict):
                raise ValueError(f"Acknowledgment entry {i} must be a mapping")
            forbidden = set(item.keys()) & _FORBIDDEN_BROAD_KEYS
            if forbidden:
                raise ValueError(
                    f"Acknowledgment entry {i} uses suppression-only broad "
                    f"selector(s) {sorted(forbidden)}, which an acknowledgment "
                    "may never use (ADR-067 D5 / vision.md: "
                    "\"a broad regex is a suppression, not an acknowledgment\"). "
                    "Use 'finding_id' or an exact 'symbol' instead, or write "
                    "this rule as a suppression."
                )
            unknown = set(item.keys()) - _KNOWN_ENTRY_KEYS
            if unknown:
                raise ValueError(
                    f"Acknowledgment entry {i} has unknown key(s): "
                    f"{sorted(unknown)}. Allowed keys: "
                    f"{sorted(_KNOWN_ENTRY_KEYS)}"
                )
            expires = _parse_expires(item.get("expires"), i)
            try:
                record = Acknowledgment(
                    finding_id=item.get("finding_id"),
                    symbol=item.get("symbol"),
                    change_kind=item.get("change_kind"),
                    component=item.get("component"),
                    baseline=item.get("baseline"),
                    candidate=item.get("candidate"),
                    reason=item.get("reason", ""),
                    reference=item.get("reference"),
                    expires=expires,
                )
            except ValueError as e:
                raise ValueError(f"Acknowledgment entry {i}: {e}") from e
            records.append(record)

        return cls(records, source_sha256=digest, source_path=str(path))

    def evaluate(
        self,
        change: Change,
        *,
        component: str | None = None,
        release_label: str | None = None,
        today: date | None = None,
    ) -> Acknowledgment | None:
        """The record acknowledging *change* in this scope, or ``None``.

        Raises :class:`AmbiguousAcknowledgmentError` when more than one
        record matches — D5's "ambiguous... requires review" (see the
        module docstring).
        """
        matches = [
            record
            for record in self._acknowledgments
            if record.matches(
                change, component=component, release_label=release_label, today=today
            )
        ]
        if not matches:
            return None
        if len(matches) > 1:
            raise AmbiguousAcknowledgmentError(
                "More than one acknowledgment record matches "
                f"{getattr(change, 'symbol', '<unknown>')!r} "
                f"({getattr(getattr(change, 'kind', None), 'value', None)}): "
                f"{[m.record_id() for m in matches]}. ADR-067 D5: an ambiguous "
                "match requires review and is never resolved automatically."
            )
        return matches[0]


def _parse_expires(expires_raw: object, entry_index: int) -> date | None:
    if expires_raw is None:
        return None
    if isinstance(expires_raw, date):
        if isinstance(expires_raw, datetime):
            return expires_raw.date()
        return expires_raw
    try:
        return date.fromisoformat(str(expires_raw))
    except ValueError as e:
        raise ValueError(
            f"Acknowledgment entry {entry_index}: invalid 'expires' date "
            f"{expires_raw!r} (expected ISO 8601 format, e.g. 2026-06-01)"
        ) from e
