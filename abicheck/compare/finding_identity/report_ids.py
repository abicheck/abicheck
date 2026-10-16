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

"""Report-facing finding fingerprints and the missing-contract finding.

Dependency-light on purpose: every report producer reaches these ids
without an import cycle."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from .change_identity import resolve_change_identity


def report_finding_id(c: object) -> str:
    """Stable per-finding fingerprint (schema 2.3, additive).

    Deterministic across repeated runs of the same comparison, so a
    consumer can tell "is this the same finding" across two report runs
    (e.g. to correlate a suppression/waiver, or diff two CI runs' findings)
    without relying on array order or index — neither of which abicheck
    guarantees stays stable release to release.

    Derived only from fields that identify the finding's *identity* (kind,
    symbol, old/new value, source location, description) — deliberately
    excluding ``severity``/``evidence_status``, which are policy-derived and
    would make the same underlying finding hash differently under a
    different ``--policy``.

    ``description`` is included as a discriminator: two findings of the same
    kind on the same symbol with the same old/new value and no distinct
    source location (e.g. ``param_pointer_level_changed`` on two different
    parameters of one function, both going from pointer-depth 1 to 2) would
    otherwise collide on an identical id even though they are different
    findings — ``description`` embeds the per-finding detail (parameter
    name/index, member name, …) that disambiguates them.

    Lives in this dependency-free leaf module so every producer of the id can
    reach it without an import cycle: ``reporter_markdown`` keys
    ``--report-mode root-cause`` groups with it (and re-exports it as
    ``_finding_id``, which ``reporter`` re-exports in turn, so existing
    import paths are unchanged), and ``checker`` keys ADR-049's persisted
    decision receipt with it -- a receipt keyed any other way could not be
    correlated with the report's own findings, and importing ``reporter_markdown``
    from ``checker`` would close a cycle the ``import-cycle-growth``
    AI-readiness gate rejects.

    ``disambiguator`` (``Change``'s ODR/multi-TU occurrence discriminator,
    Codex review, PR #1078, eighteenth round) is appended **only when set**:
    unconditionally joining it would rehash every id this function has ever
    produced (always ``None`` before this field existed) -- the same
    universal-rehash cost a prior round declined to pay for a *narrower*
    risk (``docs/contribute/plans/public-contract-default.md``'s
    "delimiter" finding). Conditional append costs nothing for a pre-
    existing finding while resolving the real, newly-reachable collision:
    two ODR-duplicate occurrences now legitimately both survive
    ``finding_dedup._dedup_exact`` with otherwise-identical fields.

    **Deliberately does NOT also fold in ``entity_id``** (tried and
    reverted, Codex review, PR #1078, twentieth round): unlike
    ``disambiguator``, ``entity_id`` predates this PR -- function/variable/
    layout detectors already populate it -- so appending it here would
    rehash this id for that entire pre-existing, already-shipped
    population, not just the new typedef/constant occurrences this round
    targeted. Fixed at the source instead: ``compare.typedefs``/
    ``compare.constants``'s own ``_collision_safe_disambiguator`` now gives
    a genuinely distinguishable occurrence a real ``disambiguator`` even
    when its producer supplied none, so the existing conditional append
    above already closes the gap without touching any other kind's id.
    """
    parts = [
        str(getattr(getattr(c, "kind", None), "value", getattr(c, "kind", ""))),
        str(getattr(c, "symbol", None) or ""),
        str(getattr(c, "old_value", None) or ""),
        str(getattr(c, "new_value", None) or ""),
        str(getattr(c, "source_location", None) or ""),
        str(getattr(c, "description", None) or ""),
    ]
    disambiguator = getattr(c, "disambiguator", None)
    if disambiguator:
        parts.append(str(disambiguator))
    key = "\x1f".join(parts)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def report_canonical_finding_id(c: object) -> str:
    """Backend-independent finding identity for cross-producer suppression
    (schema 2.36, additive; ``policy/suppression/rules.py``'s ``finding_id:`` selector).

    Unlike :func:`report_finding_id` -- which folds in ``source_location``
    and ``description`` specifically to disambiguate two same-kind,
    same-symbol findings from each other -- this id is deliberately
    **not** guaranteed unique per finding. It is :func:`resolve_change_identity`'s
    ``primary_id`` (mangled-symbol CANONICAL tier when available, a
    normalized qualified-name+kind+parameter-signature NORMALIZED tier
    otherwise), called with ``canonicalize_values=True`` -- the one thing
    that call has that ``diff_filtering.py``'s cross-detector dedup and
    ``diff_symbols.py``'s old/new symbol matching's own (default,
    uncanonicalized) calls don't need. Without it, a kind outside
    ``_EQUIVALENT_CHANGE_CATEGORIES`` (most kinds -- e.g.
    ``FUNC_RETURN_CHANGED``) folds ``change.old_value``/``new_value``
    *verbatim* into every identity tier including CANONICAL, so CastXML's
    ``"char const*"`` and Clang's ``"char const *"`` for the identical
    return-type change would hash to two different canonical ids -- exactly
    the discrepancy :func:`canonicalize_type_name` exists to normalize
    (Codex review, fresh evidence: an earlier revision of this function
    called :func:`resolve_change_identity` with the default, and the claim
    in this docstring that the NORMALIZED tier was already canonicalized
    was simply wrong for any non-equivalent-category kind).
    ``canonicalize_values=True`` normalizes (never drops) ``description``
    for the same reason it normalizes ``old_value``/``new_value``: for
    :data:`_DESCRIPTION_EMBEDS_VALUES_KINDS`, it routinely embeds the same
    raw type spelling those fields do, so the known raw substrings are
    swapped for their already-canonicalized forms; for every other kind
    ``description`` still folds in as-is. ``source_location`` is the one
    field this identity never uses at all (Codex review, fresh evidence:
    an earlier revision of this docstring claimed ``description`` was
    dropped entirely, which stopped being true once description
    normalization was added — this identity is not source-location/
    description-*blind*, only *normalized* against them). Either way,
    ``report_finding_id``'s raw ``source_location``/``description`` are
    exactly the fields two header backends are *not* guaranteed to spell
    identically (a raw file:line, or a description embedding a raw type
    spelling) — which is why it is not itself usable as a suppression key
    meant to survive a `--ast-frontend castxml` vs. `--ast-frontend clang`
    switch, and why this sibling id exists instead of widening that one's
    contract.

    A batch-shaped finding (e.g. an allocator-replacement summary covering
    many symbols) resolves through the same REDUCED-tier fallback
    :func:`resolve_change_identity` already documents for that shape — this
    function adds no special-casing of its own.

    Unlike :func:`report_finding_id`, :func:`resolve_change_identity` reads
    ``change.qualified_name``/``change.kind`` via plain attribute access
    (not ``getattr`` with a default) -- a real :class:`~abicheck.checker_types.Change`
    always has both, but a lightweight test double standing in for one (see
    the retired ``cli_scan_baseline._baseline_finding_dicts``'s "safe to call against
    fakes/stubs" contract, which this id is emitted alongside) may not. That
    case degrades to :func:`report_finding_id` itself -- self-consistent and
    still deterministic, just not guaranteed cross-backend-stable for the
    one input this function couldn't resolve a real identity for.

    **Never returns ``primary_id`` verbatim.** ``primary_id``'s own aliases
    embed a literal ``\\x1f`` (ASCII unit separator) field delimiter (see
    ``resolve_change_identity``'s ``sig``/``mangled:``/``qualified:``
    construction) -- valid inside a Python string and inside a JSON string
    (``json.dumps`` escapes control characters), but YAML's spec forbids an
    unescaped C0 control character in a plain scalar, so a value copied
    verbatim out of a JSON report into a ``--suppress`` YAML file's
    ``finding_id:`` selector would fail to parse (confirmed empirically:
    PyYAML's ``safe_load`` raises ``ReaderError: unacceptable character
    #x001f``). Hashed into the same fixed-length hex-digest shape
    :func:`report_finding_id` already uses instead, which is unconditionally
    printable/YAML-safe/copy-pasteable, and preserves everything this
    function's contract actually promises: two changes with the same
    ``primary_id`` still hash identically, and different ``primary_id``s
    practically never collide (SHA-256, birthday-bound truncated to 16 hex
    chars -- the same collision risk this function's ``finding_id`` sibling
    already accepts for the identical reason).
    """
    try:
        primary_id = resolve_change_identity(
            c,  # type: ignore[arg-type]
            canonicalize_values=True,
        ).primary_id
    except AttributeError:
        return report_finding_id(c)
    return hashlib.sha256(primary_id.encode("utf-8")).hexdigest()[:16]


def missing_contract_kind(gate_scope: object) -> str:
    """The synthetic finding kind a missing contract member is reported under.

    ``--used-by`` and ``--required-symbol`` report the same event -- "this
    label is required and the new library does not have it" -- under two
    different kind slugs, and every report format derives the slug from
    ``DiffResult.gate_scope`` the same way. Centralized here, alongside
    :func:`missing_contract_finding`, because the slug is part of that
    synthetic finding's *identity*: two producers disagreeing on it would
    hash to two different ids for one event.
    """
    return (
        "used_by_missing_symbol"
        if gate_scope == "used_by"
        else "required_symbol_missing"
    )


@dataclass(frozen=True)
class MissingContractFinding:
    """A missing contract member's identity, in ``Change``-compatible shape.

    A missing ``--used-by``/``--required-symbol`` label has no backing
    ``Change`` at all -- each report format synthesizes its own entry for it
    -- so there was nothing for :func:`report_finding_id` to read, and the
    emitted entry carried no ``finding_id`` a consumer could join to
    ADR-049's ``decision_receipt`` (Codex review, fresh evidence). This
    carries exactly the six fields that function hashes, so the synthesized
    entry and the receipt agree by construction rather than by two
    hand-copied literals happening to match.
    """

    kind: str
    symbol: str
    description: str
    old_value: None = None
    new_value: None = None
    source_location: None = None


def missing_contract_finding(kind: str, label: str) -> MissingContractFinding:
    """The identity of the missing-contract finding for *label* under *kind*.

    The description is built here, not by each caller, for the reason
    :class:`MissingContractFinding` gives: it is one of
    :func:`report_finding_id`'s own inputs, so a producer spelling it
    differently would silently mint a different id for the same event.
    """
    return MissingContractFinding(
        kind=kind,
        symbol=label,
        description=(
            f"Required symbol/version '{label}' is missing from the new library."
        ),
    )
