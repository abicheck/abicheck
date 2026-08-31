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

"""Per-finding cross-profile reconciliation for ``aggregate`` (G34 Phase D).

:mod:`abicheck.workflows.aggregate`'s ``profile_matrix`` answers "which toolchain
profiles are affected"; this module answers the question underneath it —
*which finding* is the one that differs, and is it the same finding on every
profile or one profile's alone. Without it, a reviewer looking at a
GCC/Clang/MSVC matrix has to cross-reference three reports by hand to tell
"the same removal shows up everywhere" from "each profile broke differently",
which is exactly the call a compiler-matrix gate exists to make.

Split out of ``aggregate.py`` (which sits above the 1500-line soft limit) as
a **leaf**: it imports nothing from ``aggregate`` and knows nothing about
targets, gates, coverage, or exit codes. ``aggregate`` projects its own
``TargetReport`` grouping down to the plain
``base_target -> profile -> [per-check finding set]`` shape
:func:`build_finding_matrix` takes, so the reconciliation rules below are
independently testable without constructing a whole aggregate result.

**The one invariant this module exists to preserve.** ``aggregate``'s
governing rule is that an expected target with no report is *unknown*, never
folded in as compatible. The per-finding form of it is the reason
:attr:`FindingMatrixEntry.undetermined_profiles` exists as a third list
rather than findings being split into just affected/unaffected: a profile
whose findings were never read must never be reported as *proven clean* of a
finding. :class:`ReportFindings` keeps the two facts structurally apart at
the point of reading — *what a report listed* and *whether that list was all
of it* are separate fields — and every rule below propagates the distinction
rather than collapsing it. Only completeness may clear a profile; the
findings themselves may always convict one, since seeing a finding proves it
is there while not seeing one proves nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from abicheck.diff_cxx_rules import itanium_qualified_name, msvc_qualified_name
from abicheck.finding_identity import FindingIdentity, resolve_change_identity

if TYPE_CHECKING:
    from abicheck.checker_types import Change
    from abicheck.model.identity import EntityId

#: :attr:`FindingMatrixEntry.scope` values — how one logical finding is
#: distributed across the profiles that checked its target.
FINDING_SCOPE_ALL_PROFILES = "all_profiles"
FINDING_SCOPE_PROFILE_SPECIFIC = "profile_specific"
FINDING_SCOPE_PARTIAL = "partial"
FINDING_SCOPE_UNDETERMINED = "undetermined"


@dataclass(frozen=True)
class _ReportChangeView:
    """Exactly the :class:`~abicheck.checker_types.Change` attributes
    :func:`resolve_change_identity` reads, rebuilt from a report's own JSON.

    A read-back adapter, deliberately *not* a partial ``Change``: a real
    ``Change`` carries ~30 further fields (verdict modulation, reachability,
    impact assessment) that identity resolution never touches, and
    constructing one here would invite a future reader to assume those
    fields mean something on the round-tripped side. Only the nine
    attributes below are consulted, all by plain attribute access
    (``resolve_change_identity`` reads ``kind`` through ``getattr(..., "value",
    kind)``, so a bare kind *slug* string works unchanged and an unknown
    kind from a newer abicheck never has to parse as a ``ChangeKind``).
    """

    kind: str
    symbol: str
    description: str
    #: ``Change`` annotates these ``str | None``, but the annotation is not
    #: runtime-enforced and ``diff_python.py`` really does pass a list (e.g.
    #: ``new_value=sorted(group)`` for ``python_stable_abi_violation``), which
    #: ``_change_to_dict`` preserves verbatim into JSON.
    #: ``finding_identity._stringify_change_value`` was hardened for exactly
    #: that shape, so it is forwarded rather than dropped (Codex review).
    old_value: str | list[Any] | tuple[Any, ...] | None
    new_value: str | list[Any] | tuple[Any, ...] | None
    source_location: str | None
    affected_symbols: list[str] | None
    qualified_name: str | None
    #: Never serialized by ``_change_to_dict`` (same as ``qualified_name``
    #: above), so a report-derived view always reads this as absent rather
    #: than lossy — ``resolve_change_identity`` only folds a set
    #: ``change.entity_id`` in as an additional alias, never into
    #: ``primary_id``/tier, so a permanently-``None`` value here degrades
    #: identity precision but never raises or fabricates one (ADR-063
    #: Phase 2's first real reader of ``Change.entity_id``).
    entity_id: EntityId | None = None


def resolve_report_change_identity(entry: Mapping[str, Any]) -> FindingIdentity:
    """Tiered identity for a finding read back from a **report's** ``changes[]``.

    The same :func:`resolve_change_identity` resolution, over a JSON entry
    (``reporter._change_to_dict``'s output) instead of a live ``Change`` —
    what a cross-report consumer needs, since a report is the only artifact
    that survives a CI matrix leg. Used by
    :attr:`abicheck.workflows.aggregate.AggregateResult.finding_matrix` to decide
    whether two profiles' reports describe the *same* logical finding
    (G34 Phase D).

    **Identity is comparable across reports, not with a live ``Change``.**
    ``_change_to_dict`` does not serialize ``qualified_name``, so it reads
    back as ``None`` here and a finding whose live identity was promoted via
    that field resolves one tier lower on the round trip. That is a
    consistent loss — every report on every profile loses the same field, so
    two reports still agree with each other, which is the only comparison
    this function's callers make. Don't mix an id from here with one from
    :func:`resolve_change_identity` on a live diff and expect them to match.

    A non-mapping entry, or one with no ``kind``, yields a REDUCED-tier
    identity over whatever it does carry rather than raising: a report is
    an external artifact, and one malformed finding must not abort a whole
    CI-matrix aggregation.
    """

    def _opt_str(key: str) -> str | None:
        value = entry.get(key)
        return value if isinstance(value, str) and value else None

    def _opt_value(key: str) -> str | list[Any] | tuple[Any, ...] | None:
        """``old_value``/``new_value``, keeping a structured value intact.

        Routing these through ``_opt_str`` would read a producer's list as
        *absent* and quietly compute the identity without a discriminator it
        really has — see :class:`_ReportChangeView`.
        """
        value = entry.get(key)
        if isinstance(value, (list, tuple)):
            return value
        return value if isinstance(value, str) and value else None

    affected_raw = entry.get("affected_symbols")
    # Strings only, never coerced. `resolve_change_identity` uses the whole
    # set as `header_binary_context_mismatch`'s discriminator, so `str()`-ing
    # a `[123]` would mint the spelling `"123"` and let it collide with an
    # unrelated profile's genuine `"123"` — inventing identity evidence out
    # of a malformed report (Codex review). A non-conformant array is read as
    # absent here and separately withholds completeness, so it can still
    # convict its own profile and never clears another's.
    affected = (
        list(affected_raw)
        if isinstance(affected_raw, list)
        and all(isinstance(s, str) for s in affected_raw)
        else None
    )
    view = _ReportChangeView(
        kind=str(entry.get("kind") or ""),
        symbol=str(entry.get("symbol") or ""),
        description=str(entry.get("description") or ""),
        old_value=_opt_value("old_value"),
        new_value=_opt_value("new_value"),
        source_location=_opt_str("source_location"),
        affected_symbols=affected,
        # Never serialized by `_change_to_dict` — see this function's own
        # docstring for why an absent value here is consistent rather than
        # lossy for the cross-report comparison it exists to serve.
        qualified_name=None,
        entity_id=None,
    )
    return resolve_change_identity(cast("Change", view))


def cross_abi_declaration(symbol: str) -> str | None:
    """The declaration *symbol* names, spelled independently of its mangling
    scheme, or ``None`` when it cannot be recovered.

    An Itanium toolchain spells one declaration ``_ZN3lib3addEii`` and MSVC
    spells the same one ``?add@lib@@YAHHH@Z``; both reduce to ``lib::add``
    here, via ``diff_cxx_rules``' pure structural parsers — no demangler
    subprocess, so this behaves identically on every host.

    ``None`` for a type-level finding whose ``symbol`` is a type name rather
    than a mangling, for an ``extern "C"`` symbol that is already
    scheme-independent, and for a mangling the deliberately conservative
    parsers decline to model (templates, operators, MSVC special members).
    Never a guess.
    """
    if not symbol:
        return None
    qualified = itanium_qualified_name(symbol) or msvc_qualified_name(symbol)
    if not qualified or qualified == symbol:
        return None
    return qualified


#: Mangling schemes this module can tell apart. Two symbols in the *same*
#: scheme are directly comparable — their inequality is proof they name
#: different entities — while an Itanium/MSVC pair is not comparable at all
#: without a type-encoding translator this module deliberately does not have.
MANGLING_ITANIUM = "itanium"
MANGLING_MSVC = "msvc"


def mangling_scheme(symbol: str) -> str | None:
    """Which C++ mangling scheme *symbol* is encoded in, or ``None``.

    ``None`` for anything the structural parsers decline to model — a type
    name, an ``extern "C"`` symbol, a template or operator mangling. Decided
    by which parser actually accepts the symbol rather than by re-deriving
    prefix rules, so this can never disagree with
    :func:`cross_abi_declaration` about what a symbol is.
    """
    if not symbol:
        return None
    if itanium_qualified_name(symbol):
        return MANGLING_ITANIUM
    if msvc_qualified_name(symbol):
        return MANGLING_MSVC
    return None


def comparable_mangled_symbol(symbol: str) -> str:
    """*symbol* with platform spelling differences normalized away.

    A Mach-O toolchain prefixes its Itanium manglings with an extra leading
    underscore (``__ZN3lib3addEii`` for the same declaration Linux spells
    ``_ZN3lib3addEii``), so two profiles can carry byte-different symbols for
    one entity. Comparing raw strings would read that as two distinct
    overloads; comparing these normalized forms does not.
    """
    if symbol.startswith("__Z"):
        return symbol[1:]
    return symbol


def resolve_cross_abi_identity(entry: Mapping[str, Any]) -> FindingIdentity | None:
    """A mangling-scheme-independent identity for *entry*, or ``None``.

    :func:`resolve_report_change_identity` keys on the raw exported symbol,
    which is the most specific answer available *within* one mangling scheme
    but cannot see across two. This re-resolves the identity from
    :func:`cross_abi_declaration`'s scheme-independent name instead, keeping
    the same discriminator so a removal and an addition on one declaration
    still stay apart.

    **This is a same-*declaration* key, never a same-*finding* proof, and
    every caller must treat it as such.** Neither parser recovers parameter
    types, so ``add(int, int)`` and ``add(double)`` reduce to one key —
    meaning two profiles matching on it may be reporting one shared removal
    or two unrelated overload removals, and nothing in a report distinguishes
    those. :func:`build_finding_matrix` therefore never merges on it; it uses
    it only to *withhold* a clean verdict, which needs no such proof (Codex
    review: an earlier revision merged when each profile contributed exactly
    one identity, which asserted a pairing the evidence does not establish).
    """
    declaration = cross_abi_declaration(str(entry.get("symbol") or ""))
    if declaration is None:
        return None
    return resolve_report_change_identity({**entry, "symbol": declaration})


@dataclass(frozen=True)
class ReportFinding:
    """One entry of a report's ``changes[]``, reduced to what cross-profile
    reconciliation needs.

    :attr:`identity` is
    :func:`resolve_report_change_identity`'s
    ``primary_id`` — the key that decides whether two *different profiles'*
    reports describe the same logical finding. It is deliberately the same
    tiered identity model ``diff_filtering.py`` already uses as its
    cross-detector dedup key (ADR-049 Phase 2), not a second scheme: a
    finding one profile reports as ``func_removed`` (rich DWARF evidence)
    and another as ``func_removed_elf_only`` (symbols only) collapses to one
    identity there and must here too, or a differently-provisioned matrix
    leg would look like it found a different problem.

    Not the report's own ``finding_id``, which additionally hashes
    ``description`` and ``source_location`` *unconditionally* — right for
    "is this the same finding across two runs of the same comparison", too
    strict across profiles, where a header path or a rendered size can
    legitimately differ for one logical event.
    """

    identity: str
    identity_tier: str
    kind: str
    symbol: str
    description: str
    severity: str | None = None
    #: :func:`resolve_cross_abi_identity`'s answer — a candidate key for
    #: matching this finding against one reported under a *different*
    #: mangling scheme, or ``None`` when no scheme-independent name could be
    #: recovered. Never used on its own; see that function's docstring for
    #: why it is a candidate rather than a proof.
    cross_identity: str | None = None
    #: The scheme-independent declaration name behind
    #: :attr:`cross_identity` (``lib::add``), for display and grouping.
    cross_declaration: str | None = None
    #: :func:`mangling_scheme` and :func:`comparable_mangled_symbol` for this
    #: finding's own ``symbol`` — together they decide whether another
    #: profile's finding on the same declaration is *provably* a different
    #: one or merely indistinguishable.
    mangling: str | None = None
    comparable_symbol: str = ""
    #: ADR-049's per-finding contract-decision quartet, read verbatim off
    #: the report entry (see ``reporter._add_contract_evaluation_fields``
    #: for what stamps them) -- ``None`` for every field when the profile's
    #: run never opted into ``--contract``, which is what keeps
    #: a matrix built from pre-ADR-049 reports unaffected. Kept here rather
    #: than only surfaced in :class:`FindingMatrixEntry` so a caller working
    #: with raw per-profile findings (not yet reconciled into the matrix)
    #: still has them.
    contract_relevance: str | None = None
    compatibility_evaluation_status: str | None = None
    compatibility_decision: str | None = None
    gate_contribution: int | None = None

    @classmethod
    def from_report_entry(cls, entry: Mapping[str, Any]) -> ReportFinding:
        identity = resolve_report_change_identity(entry)
        cross = resolve_cross_abi_identity(entry)
        severity = entry.get("severity")
        contract_relevance = entry.get("contract_relevance")
        compatibility_evaluation_status = entry.get("compatibility_evaluation_status")
        compatibility_decision = entry.get("compatibility_decision")
        gate_contribution = entry.get("gate_contribution")
        return cls(
            identity=identity.primary_id,
            identity_tier=identity.tier,
            kind=str(entry.get("kind") or ""),
            symbol=str(entry.get("symbol") or ""),
            description=str(entry.get("description") or ""),
            severity=severity if isinstance(severity, str) and severity else None,
            cross_identity=cross.primary_id if cross is not None else None,
            cross_declaration=cross_abi_declaration(str(entry.get("symbol") or "")),
            mangling=mangling_scheme(str(entry.get("symbol") or "")),
            comparable_symbol=comparable_mangled_symbol(str(entry.get("symbol") or "")),
            contract_relevance=(
                contract_relevance if isinstance(contract_relevance, str) else None
            ),
            compatibility_evaluation_status=(
                compatibility_evaluation_status
                if isinstance(compatibility_evaluation_status, str)
                else None
            ),
            compatibility_decision=(
                compatibility_decision
                if isinstance(compatibility_decision, str)
                else None
            ),
            gate_contribution=(
                # bool is an int subclass in Python -- isinstance() alone
                # would accept a JSON true/false as a gate_contribution and
                # round-trip it back out as a JSON boolean, which neither
                # this schema nor compare_report.schema.json's own
                # gate_contribution allow (Codex/CodeRabbit review).
                # pylint: disable-next=unidiomatic-typecheck
                gate_contribution if type(gate_contribution) is int else None
            ),
        )


@dataclass(frozen=True)
class ReportFindings:
    """One report's findings, plus whether that list is known to be *all* of
    them.

    The two fields are independent on purpose, and conflating them is the
    mistake this class exists to make unrepresentable. ``findings`` is what
    was successfully read; :attr:`complete` is whether reading it accounted
    for everything the comparison found. A report can carry real findings
    and still be incomplete — a ``compare-release`` report enumerates its
    bundle- and matrix-level findings but only *counts* its per-library
    ones, and a report with one unparseable array element yields the rest
    plus a known hole.

    Only :attr:`complete` may clear a profile of a finding. The findings
    themselves can always *convict* a profile, complete or not: seeing a
    finding is proof it is there, whereas not seeing one is proof of
    nothing unless the list was exhaustive.
    """

    findings: tuple[ReportFinding, ...] = ()
    complete: bool = False


#: Finding arrays a ``compare-release`` report carries instead of ``changes``
#: (``cli_compare_release_helpers._format_release_json``) — the shape a
#: ``kind: bundle`` check produces, since a bundle comparison routes through
#: the per-library release fan-out. Both carry the same
#: ``kind``/``symbol``/``description``/``old_value``/``new_value`` fields
#: :class:`ReportFinding` reads, so they need no separate identity path.
_RELEASE_FINDING_KEYS = ("bundle_findings", "matrix_findings")

#: Where a ``scan --against`` report keeps its findings
#: (``scan_engine.ScanOutcome.to_dict``'s ``diff`` block, filled by
#: ``cli_scan_baseline``). A third report shape after ``compare`` and
#: ``compare-release``, and like the release shape it can never be
#: *complete*: only the gating buckets are itemized (compatible findings are
#: deliberately left out) and the list is capped at 20 with a
#: ``findings_truncated`` flag. Parsing it is what keeps a scan-checked
#: profile from vanishing out of the matrix entirely (Codex review).
_SCAN_DIFF_KEY = "diff"
_SCAN_FINDINGS_KEY = "findings"


def _with_bundle_attribution(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    """Fold a bundle finding's library attribution into its ``description``.

    A ``bundle_findings`` entry keeps ``consumer_library``/``provider_library``
    as separate fields, so two findings that differ *only* in which library
    pair they are about are otherwise identical on every field
    :func:`resolve_report_change_identity` reads — they would reconcile into
    one entry that is really two. Prefixing the description restores the
    distinction using ``bundle_models.BundleFinding.to_change``'s own
    established flattening (``"[consumer ← provider] "``) rather than
    inventing a second attribution format for the same fact.
    """
    consumer = entry.get("consumer_library")
    provider = entry.get("provider_library")
    consumer = consumer if isinstance(consumer, str) and consumer else None
    provider = provider if isinstance(provider, str) and provider else None
    if consumer and provider:
        prefix = f"[{consumer} ← {provider}] "
    elif provider:
        prefix = f"[{provider}] "
    elif consumer:
        prefix = f"[{consumer}] "
    else:
        return entry
    return {**entry, "description": prefix + str(entry.get("description") or "")}


#: Every field ``compare_report.schema.json``'s ``$defs/change`` marks
#: ``required``, mapped to whether the schema also permits ``null`` there.
#: All three ``compare`` report modes emit all six; an entry missing one —
#: or carrying ``null`` where the schema says ``"string"`` — was not written
#: by a conformant producer, so it is no evidence that the array was
#: enumerated successfully. It resolves to a *differently* discriminated
#: identity (``old_value``/``new_value`` are part of the discriminator, and
#: a ``null`` ``symbol`` reads as an empty one) while leaving the report
#: ``complete``, which is how a malformed array could clear another
#: profile's finding (Codex review).
#:
#: Checking presence alone was not enough, and the nullability split is the
#: schema's own: ``old_value``/``new_value`` are declared ``["string",
#: "null"]`` because a finding with no before/after value really does emit
#: them as ``null``, while ``symbol``/``description``/``kind``/``severity``
#: are plain ``"string"``.
#:
#: The schema is the fact owner; this mirror exists so the check is a cheap
#: per-key test rather than a full schema validation on every entry, and
#: ``TestSchemaRequiredFieldsAgree`` asserts both the field set *and* the
#: nullability agree with it, so neither half can drift.
_CONFORMANT_CHANGE_FIELDS = {
    "kind": False,
    "symbol": False,
    "description": False,
    "old_value": True,
    "new_value": True,
    "severity": False,
}

#: Identity fields that must be a plain string when present. A non-string
#: here does not merely degrade the identity, it silently changes it:
#: ``symbol`` and ``description`` would be ``str()``-coerced into a spelling
#: no producer ever wrote, and ``source_location`` would be read as absent.
_IDENTITY_STRING_FIELDS = ("symbol", "description", "source_location")

#: Identity fields that may *also* carry a structured value. ``Change``
#: annotates both ``str | None``, but the annotation is not runtime-enforced
#: and ``diff_python.py``'s ``python_stable_abi_violation`` emissions really
#: do pass a list -- ``finding_identity._stringify_change_value`` exists
#: precisely to fold that shape deterministically. Rejecting it here would
#: drop a genuine finding *and* mark its report incomplete, turning a
#: profile that is demonstrably affected into an undetermined one (Codex
#: review). The rule this file follows: accept exactly what the identity
#: resolver handles, no less.
_IDENTITY_VALUE_FIELDS = ("old_value", "new_value")


def _is_usable_finding_entry(entry: object) -> bool:
    """Whether *entry* is a finding this module can identify at all.

    Being a ``Mapping`` is not enough (Codex review): an object with no
    ``kind`` still parses, yielding a REDUCED-tier identity over entirely
    empty fields -- a finding that describes nothing, which would then be
    counted as evidence that the report enumerated its findings successfully.
    A profile whose array holds only such objects would be marked *complete*
    and could clear another profile's finding, which is the same false clean
    claim the non-mapping case already guards against, one level down.

    ``kind`` is the one required field: it is what every real finding has and
    what identity resolution is built around. The rest are checked for *type*
    only -- a present-but-wrong-typed value is rejected rather than coerced,
    since coercion would mint an identity from a spelling no producer emitted.
    An absent optional field is fine; that is ordinary, and every report on
    every profile omits the same ones.

    **Readability, not conformance.** Whether a producer wrote every field
    its schema requires is :func:`_is_conformant_change_entry`'s separate
    question, because the two answers are used for opposite purposes: this
    one decides whether a finding is *kept* (and a kept finding can only ever
    convict a profile), that one decides whether the array counts as
    *exhaustive* (which is the only thing that can clear one). Collapsing
    them into one predicate is wrong in both directions -- it would drop
    every ``scan --against`` finding, since that producer emits no
    ``old_value``/``new_value``/``severity`` at all.
    """
    if not isinstance(entry, Mapping):
        return False
    kind = entry.get("kind")
    if not isinstance(kind, str) or not kind:
        return False
    if not all(
        entry.get(field) is None or isinstance(entry.get(field), str)
        for field in _IDENTITY_STRING_FIELDS
    ):
        return False
    return all(
        entry.get(field) is None or isinstance(entry.get(field), (str, list, tuple))
        for field in _IDENTITY_VALUE_FIELDS
    )


def _is_string_sequence(value: object) -> bool:
    """Whether *value* is a list/tuple whose every element is a string.

    The shape every real producer of a structured `old_value`/`new_value`
    or an `affected_symbols` array emits. Checking only the container and
    not its elements let `[{"bad": 1}]` through, which
    ``_stringify_change_value`` then folds to the spelling ``"{'bad': 1}"``
    — an identity no producer wrote (Codex review).
    """
    return isinstance(value, (list, tuple)) and all(
        isinstance(item, str) for item in value
    )


def _is_conformant_change_entry(entry: Mapping[str, Any]) -> bool:
    """Whether a readable ``changes[]`` *entry* carries every field the
    compare-report schema requires of one, at the type it requires.

    Only asked of ``changes``, and only to decide completeness. A readable
    entry that fails here is still a real finding this module keeps -- it
    just stops the array from being treated as the whole story, since an
    entry no conformant producer would have written is not evidence that the
    rest were enumerated successfully either.

    ``null`` is accepted exactly where the schema accepts it (Codex review):
    a present-but-``null`` ``old_value`` is an ordinary finding with no
    before-value, while a ``null`` ``symbol`` is schema-invalid *and* reads
    as an empty spelling, which is precisely the differently-identified
    entry that must not clear another profile.
    """
    for field, nullable in _CONFORMANT_CHANGE_FIELDS.items():
        if field not in entry:
            return False
        value = entry[field]
        if value is None:
            if not nullable:
                return False
        elif not isinstance(value, str):
            # The schema declares every one of these a string. The single
            # carve-out is the structured `old_value`/`new_value` a real
            # producer emits (``diff_python.py`` passes a list), which
            # `_is_usable_finding_entry` already accepts and the identity
            # resolver folds deterministically -- rejecting *those* would
            # mark a genuine report incomplete. Extending the carve-out to
            # every field instead, as a first cut did, let `severity: []`
            # read as conformant (Codex review).
            if field not in _IDENTITY_VALUE_FIELDS or not _is_string_sequence(value):
                return False
    # Optional, but identity-bearing when present: `resolve_change_identity`
    # folds the whole `affected_symbols` set into one kind's discriminator,
    # so a non-string element there is producer output no schema describes
    # (Codex review). Absent is ordinary and stays fine.
    if "affected_symbols" in entry and not _is_string_sequence(
        entry["affected_symbols"]
    ):
        return False
    return True


#: What a report says about its own ``changes`` array being *displayed*
#: rather than *enumerated*. ``compare --show-only`` narrows the array
#: (``reporter.to_json``) while the verdict, the gate, and the ``summary``
#: block all keep describing the whole diff — so a well-formed array is not
#: by itself proof that nothing else was found (Codex review). Two signals,
#: because neither covers every report mode: ``show_only_filter`` is emitted
#: by full and root-cause mode but not by ``--report-mode leaf``, which
#: instead leaves ``summary.total_changes`` counting the unfiltered diff
#: against a filtered array.
_SHOW_ONLY_KEY = "show_only_filter"
_SUMMARY_KEY = "summary"
_SUMMARY_TOTAL_KEY = "total_changes"


def _changes_array_is_exhaustive(data: Mapping[str, Any], listed: int) -> bool:
    """Whether *data*'s ``changes`` array holds every finding the run made.

    A count *mismatch* is the general form of the question and a filter flag
    is one specific answer to it, so both are checked: any future display
    filter that narrows the array while leaving the summary whole is caught
    by the count alone. The comparison is deliberately one-directional —
    only a summary claiming *more* than the array lists withholds
    completeness, since an array longer than the count would mean something
    else is wrong and is not this function's question to answer.
    """
    if data.get(_SHOW_ONLY_KEY) is not None:
        return False
    summary = data.get(_SUMMARY_KEY)
    if isinstance(summary, Mapping):
        total = summary.get(_SUMMARY_TOTAL_KEY)
        if isinstance(total, int) and not isinstance(total, bool) and total > listed:
            return False
    return True


def parse_report_findings(data: Mapping[str, Any]) -> ReportFindings:
    """Read every finding array a report carries into a :class:`ReportFindings`.

    Three report shapes are understood. A ``compare`` report puts its whole
    finding set in ``changes``; a ``compare-release`` report has no
    ``changes`` at all and instead carries ``bundle_findings`` and
    ``matrix_findings`` (:data:`_RELEASE_FINDING_KEYS`) alongside per-library
    entries that are only *counts*; a ``scan --against`` report itemizes its
    gating buckets under ``diff.findings`` (:data:`_SCAN_DIFF_KEY`). All
    three are parsed, so a ``kind: bundle`` or scan-baseline check — neither
    of which produces the ``compare`` shape — participates in the matrix
    instead of being written off as unknown.

    :attr:`~ReportFindings.complete` is granted **only** for a well-formed,
    unfiltered ``changes`` array, because that is the one field that carries
    a comparison's entire finding set. A release report is never complete no
    matter how many bundle/matrix findings it lists: its per-library
    findings are not in the document, so it cannot clear a profile of one.
    Neither is a report with any unparseable *or* non-conformant
    (:func:`_is_conformant_change_entry`) array element, nor one whose
    array was narrowed for display rather than enumerated
    (:func:`_changes_array_is_exhaustive`) — the valid entries stay usable
    (they can still convict a profile), but the hole is recorded rather than
    silently rounded down to "found nothing", which is the false clean claim
    :attr:`FindingMatrixEntry.undetermined_profiles` exists to prevent.

    A malformed entry is skipped rather than raising: reports are external
    artifacts, and one bad finding must not take a whole CI-matrix
    aggregation down with it.
    """
    findings: list[ReportFinding] = []
    malformed = False

    raw_changes = data.get("changes")
    enumerated_changes = isinstance(raw_changes, list) and _changes_array_is_exhaustive(
        data, len(raw_changes)
    )
    if isinstance(raw_changes, list):
        for entry in raw_changes:
            if _is_usable_finding_entry(entry):
                findings.append(ReportFinding.from_report_entry(entry))
                if not _is_conformant_change_entry(entry):
                    malformed = True
            else:
                malformed = True

    scan_diff = data.get(_SCAN_DIFF_KEY)
    release_arrays = [data.get(key) for key in _RELEASE_FINDING_KEYS]
    if isinstance(scan_diff, Mapping):
        release_arrays.append(scan_diff.get(_SCAN_FINDINGS_KEY))

    for raw in release_arrays:
        if not isinstance(raw, list):
            continue
        for entry in raw:
            if _is_usable_finding_entry(entry):
                findings.append(
                    ReportFinding.from_report_entry(_with_bundle_attribution(entry))
                )
            else:
                malformed = True

    return ReportFindings(
        findings=tuple(findings),
        complete=enumerated_changes and not malformed,
    )
