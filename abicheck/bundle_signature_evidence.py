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

"""C-boundary signature-evidence gate for the bundle layer (G38 Phase 4,
amendment to ADR-023 — see ``docs/contribute/plans/g38-bundle-facts-model-
and-multibuild-comparability.md``).

``abicheck.bundle``'s own ``bundle_intra_dep_signature_changed`` already
fires correctly when a provider's DWARF/header evidence shows a real
signature change on a symbol a sibling library imports. What it cannot say
is the negative case: when *at least one* side lacks that evidence (a
stripped provider, or a provider only ever dumped at L0, on either the old
or the new snapshot — not necessarily both), the bundle layer has no way
to say "this consumer's import still resolves by name, but nothing
establishes the signature agrees" — it silently reports nothing, which
reads as "compatible" even though compatibility was never actually checked.

This module answers exactly that question, as a standalone companion to
:func:`abicheck.bundle.compare_bundle` rather than a change to it:
:func:`find_unverified_signature_findings` takes the same bundle snapshots
and per-library diff results ``compare_bundle`` already receives, plus each
library's own :class:`~abicheck.model.AbiSnapshot` (old and new — the one
input ``compare_bundle`` itself does not need, since its own detectors work
entirely from ``ElfMetadata``/``DiffResult``), and returns the additional
``BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED`` findings a caller can append to
``BundleDiffResult.bundle_findings``.

Deliberately NOT folded into ``compare_bundle`` itself: ``abicheck/bundle.py``
sits at the AI-readiness 2000-line hard cap (confirmed at the time this
module was written), so any addition there needs an equal-or-greater
removal in the same change -- a scope this phase has no reason to take on.
Keeping this as a pure, additive function elsewhere costs nothing: it reads
the identical ``BundleSnapshot``/``DiffResult`` shapes ``compare_bundle``
already produces/consumes, and a caller wanting both simply calls both and
concatenates the finding lists (see this module's own tests for the exact
pattern). This also matches G38 Phase 3's `bundle_multibuild.py` precedent
(`pair_variants`/`coverage_regression_findings` are equally standalone, not
wired into `compare_bundle`) — no CLI/config surface yet calls this
function either; see this phase's status note in the plan doc.

This is a leaf module with respect to :mod:`abicheck.bundle`: it does not
import that module (only :mod:`abicheck.bundle_models` for ``BundleFinding``/
``BundleSnapshot``, and :mod:`abicheck.bundle_resolution_reachability` for
the ``DT_NEEDED``-reachability BFS both this module and ``bundle.py`` need
-- extracted into its own tiny leaf module for exactly this reason, see
its own docstring), so there is no import-cycle risk either direction.

**Provider-edge filtering (Codex review, fresh evidence):** a consumer is
only counted for a given provider when (1) the provider is actually
reachable from that consumer via a real ``DT_NEEDED`` path
(:func:`~abicheck.bundle_resolution_reachability.reachable_intra_libraries`)
and (2) the provider actually satisfies that consumer's own version/
default-binding requirement (:func:`_consumer_matches_provider`) -- both
the same constraints ``bundle._detect_unresolved_intra_dependency``
already applies to its own, more elaborate provider matching. The version
check is evaluated per (consumer, provider_entry) pair rather than that
sibling function's "does *some* provider in the whole set resolve this"
question, since this module's main loop already iterates one concrete
``provider_entry`` at a time -- it did not, in the end, need that
sibling's per-consumer resolution shape, contrary to an earlier revision
of this docstring.

A third check (Codex review, fresh evidence) guards the *old* side the
same way: a bare-name "was this symbol exported in the old snapshot"
check (:func:`_symbol_was_exported`) cannot tell a genuinely fresh symbol
*version* apart from an unrelated old-side version sharing the same bare
name -- a provider that previously exported only ``foo@V1`` and now adds
``foo@V2`` would otherwise read as "retained, evidence uncertain" for
V2 purely because *some* ``foo`` existed before.
:func:`_provider_entry_retained_from_old` closes this by matching on
``ProviderEntry.version`` at the bundle-resolution layer, which is
version-aware, rather than the version-blind ``AbiSnapshot.function_map``/
``variable_map`` layer ``_symbol_was_exported`` reads.

A fourth check (Codex review, fresh evidence) guards the evidence-
sufficiency lookup itself against the same version-blindness one layer
deeper: even a *retained* symbol (the third check above) can have
multiple co-existing GNU versions on one or both sides (``foo@V1`` and
``foo@@V2`` both still live -- an ordinary shape for a provider that has
never broken ABI compatibility), and ``AbiSnapshot.function_map``/
``variable_map`` keep only one bare-name-keyed entry regardless. Asking
whether *that* entry is "sufficient evidence" for one specific
``ProviderEntry.version`` risks silently borrowing a different version's
signature. :func:`_bare_name_version_collapsed` detects the collapse via
the bundle-resolution layer's own per-version ``ProviderEntry`` list
(which the ``AbiSnapshot`` layer does not carry) and fails the
sufficiency check closed rather than trusting the ambiguous entry.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from .bundle_models import (
    CONFIRMED_C_BOUNDARY_SIGNATURE_BREAK_KINDS,
    BundleFinding,
    BundleSignatureEvidence,
    BundleSnapshot,
    ConsumerEntry,
    ProviderEntry,
    basename_to_bundle_key,
)
from .bundle_resolution_reachability import reachable_intra_libraries
from .checker_types import DiffResult
from .model import AbiSnapshot
from .model.change_catalog.kinds import ChangeKind

#: G38 stabilization: this used to be a locally-duplicated frozenset
#: (comment preserved below for the history of why each kind is or isn't
#: included), independently maintained from `bundle._detect_intra_dep_
#: signature_changed`'s own `relevant_kinds` set -- and the two drifted:
#: `bundle.py` promoted only three kinds
#: (`FUNC_PARAMS_CHANGED`/`FUNC_RETURN_CHANGED`/`VAR_TYPE_CHANGED`) to a
#: consumer-attributed `BUNDLE_INTRA_DEP_SIGNATURE_CHANGED` finding while
#: this module already suppressed its own "unverified" finding on nine
#: more. A confirmed `CALLING_CONVENTION_CHANGED` (say) correctly
#: suppressed this module's uncertainty finding but was silently never
#: promoted to a cross-library break -- losing exactly the causality the
#: bundle report exists to surface. Both consumers now import the same
#: :data:`abicheck.bundle_models.CONFIRMED_C_BOUNDARY_SIGNATURE_BREAK_KINDS`
#: rather than keeping two lists that can disagree again; see that
#: constant's own docstring for the per-kind inclusion/exclusion reasoning
#: (`FUNC_VARIADIC_ADDED`/`FUNC_VARIADIC_REMOVED`/`CALLING_CONVENTION_
#: CHANGED`, then `is_noexcept`/`is_virtual`/`ref_qualifier`/
#: `exception_spec`, all added across several Codex review rounds for the
#: identical "a confirmed change on one axis must not coexist with a
#: contradictory 'couldn't tell' finding on the whole symbol" reason;
#: `CTOR_EXPLICIT_ADDED`/`CTOR_EXPLICIT_REMOVED` deliberately excluded,
#: tried once and reverted, since an `explicit` transition never changes
#: the mangled name and proves nothing about the binary calling signature
#: this module verifies).
_CONFIRMED_SIGNATURE_CHANGE_KINDS = CONFIRMED_C_BOUNDARY_SIGNATURE_BREAK_KINDS


# The two per-symbol predicates and their compact projection moved to
# `workflows/bundle_symbol_status.py` -- a leaf module both this and
# `bundle_models` can depend on, since the projection needs them and a
# classmethod reaching back here was a real import cycle. Re-exported by
# value so every existing `from .bundle_signature_evidence import
# _symbol_was_exported` call site (and this module's own use below) keeps
# resolving.
from .workflows.bundle_symbol_status import (  # noqa: E402
    _UNKNOWN_TYPE_SENTINEL as _UNKNOWN_TYPE_SENTINEL,
    _symbol_evidence_sufficient as _symbol_evidence_sufficient,
    _symbol_was_exported as _symbol_was_exported,
    _type_spelling_is_unresolved as _type_spelling_is_unresolved,
    symbol_signature_statuses as symbol_signature_statuses,
)

#: G38 stabilization: this used to be a locally-duplicated function
#: (`_basename_to_bundle_key`, original docstring's own history preserved
#: in `bundle_models.basename_to_bundle_key` now) -- `bundle.py`'s own
#: `diff_by_library` construction had the identical bug independently
#: (Codex/CodeRabbit review on PR #845), so the fix moved to the one
#: shared leaf module both already import from.
_basename_to_bundle_key = basename_to_bundle_key


def _confirmed_provider_symbols(
    old: BundleSnapshot,
    per_library_results: Iterable[DiffResult],
) -> set[tuple[str, str]]:
    """`(provider_library, symbol)` pairs already carrying a real, diff-
    confirmed signature change -- these must never also produce an
    "unverified" finding (real evidence of a break outranks "couldn't tell
    either way").

    *provider_library* here is the bundle-canonical key (see
    :func:`_basename_to_bundle_key`), matching the key space
    `find_unverified_signature_findings`'s own main loop compares against
    (``new.resolution.provides``) -- not the raw ``DiffResult.library``
    basename this set was previously (incorrectly) keyed by.
    """
    basename_to_key = _basename_to_bundle_key(old)
    confirmed: set[tuple[str, str]] = set()
    for result in per_library_results:
        basename = Path(result.library).name
        provider_lib = basename_to_key.get(basename, basename)
        for change in result.changes:
            if change.kind in _CONFIRMED_SIGNATURE_CHANGE_KINDS:
                confirmed.add((provider_lib, change.symbol))
    return confirmed


def _consumer_matches_provider(
    consumer: ConsumerEntry, provider_entry: ProviderEntry, new: BundleSnapshot
) -> bool:
    """Does *provider_entry* actually satisfy *consumer*'s own version/
    default-binding requirement for the symbol they share?

    Mirrors ``bundle._detect_unresolved_intra_dependency``'s own
    version-aware provider matching:

    - A consumer requiring a specific version (``ConsumerEntry.version``)
      can only be satisfied by a provider definition carrying that exact
      version. When the precise ``version_soname`` is known, the match is
      further pinned to the provider library that soname actually
      resolves to (GNU version *labels* are not globally unique across
      providers).
    - An unversioned consumer reference can only be satisfied by an
      unversioned or default-version (``@@default``) provider definition
      (``ProviderEntry.is_default``) -- a provider whose only definition
      of this symbol is a non-default versioned one (``foo@V1``, not
      ``foo@@V1``) cannot satisfy it, even though the bare symbol name
      matches.
    """
    if consumer.version:
        if consumer.version_soname:
            target_lib = new.resolution.soname_to_name.get(consumer.version_soname)
            return (
                target_lib == provider_entry.library
                and provider_entry.version == consumer.version
            )
        return provider_entry.version == consumer.version
    return provider_entry.is_default


def _provider_entry_retained_from_old(
    provider_entry: ProviderEntry, old: BundleSnapshot, symbol: str
) -> bool:
    """Did *old*'s own bundle resolution graph already carry a provider
    definition of *symbol* from the same library, at the same GNU symbol
    version, as *provider_entry*?

    ``new.resolution.provides[symbol]`` can gain a fresh ``ProviderEntry``
    across a release the same way a symbol table can gain a fresh
    ``foo@V2`` definition alongside a pre-existing ``foo@V1`` one -- both
    entries share the bare *symbol* name, but only one of them is actually
    the *retained* export whose old-side signature evidence is worth
    asking about. A name-only check (``_symbol_was_exported``, which reads
    only ``AbiSnapshot.function_map``/``variable_map`` -- themselves keyed
    by bare name, with no per-version distinction) cannot tell these apart
    (Codex review, fresh evidence): it would treat a brand-new ``foo@V2``
    as "retained, evidence uncertain" purely because an unrelated
    ``foo@V1`` happened to exist on the old side, even though ``foo@V2``
    is a genuinely new export with no old-side counterpart to compare
    against. Matching on ``ProviderEntry.version`` (``""`` for an
    unversioned symbol, so two unversioned entries still match each other)
    is what actually answers "is this the same export, not just the same
    bare name" -- the same version-aware granularity
    ``_consumer_matches_provider`` already applies one layer up.
    """
    return any(
        old_pe.version == provider_entry.version
        for old_pe in old.resolution.provides.get(symbol, [])
        if old_pe.library == provider_entry.library
    )


def _consumer_retained_from_old(
    consumer: ConsumerEntry, old: BundleSnapshot, provider_lib: str, symbol: str
) -> bool:
    """Would *consumer* have resolved *symbol* from *provider_lib* against
    *old*'s own bundle resolution graph too -- i.e. is this genuinely a
    retained edge for *this specific consumer*, not one only newly made
    reachable by a default-binding change?

    Retention is not a uniform fact about the new provider entry alone
    (Codex review, fresh evidence): matching purely on
    ``ProviderEntry.version`` (:func:`_provider_entry_retained_from_old`)
    can hold while the *binding* that actually makes the new entry
    reachable to a given consumer did not exist on the old side. A
    concrete example: old exports only ``foo@V1`` (``is_default=False``);
    new exports the identical ``foo@@V1``, now marked default. An
    unversioned consumer binds only to a default definition
    (``_consumer_matches_provider``'s own rule) -- it could not have
    resolved ``foo`` from this provider in *old* at all, so for that
    consumer specifically the new binding is a genuinely new capability,
    not a retained one whose signature could have silently changed. A
    consumer requiring the specific version ``V1`` is unaffected either
    way: its own match rule never inspects ``is_default``, so it was
    already reachable to the identical old definition and stays counted.

    Deliberately does *not* replace :func:`_provider_entry_retained_from_
    old` -- an unversioned consumer's own match rule ignores symbol
    version entirely, so checking only per-consumer reachability would
    treat a provider entry with a genuinely new, never-before-existing
    version as "retained" merely because *some* old default entry (of a
    different version) satisfies an unversioned consumer. The two checks
    answer different questions and both must hold: does this exact
    version/entry have old-side evidence at all, and would this specific
    consumer have been able to reach *a* compatible old-side entry.
    """
    return any(
        _consumer_matches_provider(consumer, old_pe, old)
        for old_pe in old.resolution.provides.get(symbol, [])
        if old_pe.library == provider_lib
    )


def _bare_name_version_collapsed(
    snapshot: BundleSnapshot, provider_lib: str, symbol: str
) -> bool:
    """Does *snapshot*'s own bundle resolution graph record more than one
    distinct GNU symbol version of *symbol* exported by *provider_lib*?

    ``AbiSnapshot.function_map``/``variable_map`` carry exactly one
    ``Function``/``Variable`` entry per bare symbol name -- an ordinary
    provider that has never broken ABI compatibility across a versioned
    release routinely retains multiple live definitions of the same bare
    name (``foo@V1`` *and* ``foo@@V2``), and that single model entry
    cannot be attributed to any one specific version; it reflects
    whichever definition the header/DWARF parser happened to associate
    with the bare name (Codex review, fresh evidence -- the same
    last-entry-wins collapse this repo's own root ``AGENTS.md`` already
    documents for ``ElfMetadata.symbol_map``). Evaluating
    ``_symbol_evidence_sufficient`` against that single entry for a
    *specific* ``ProviderEntry.version`` would silently borrow whichever
    version's signature the model happened to keep, reading as "fully
    evidenced" for a version that was never actually captured. The bundle
    resolution graph, unlike ``AbiSnapshot``, keeps one ``ProviderEntry``
    per version -- exactly the granularity needed to detect the collapse,
    even though it cannot recover the lost per-version signature data
    itself. When collapsed, evidence sufficiency must fail closed rather
    than trust the ambiguous single entry.
    """
    versions = {
        pe.version
        for pe in snapshot.resolution.provides.get(symbol, [])
        if pe.library == provider_lib
    }
    return len(versions) > 1


def find_unverified_signature_findings(
    old: BundleSnapshot,
    new: BundleSnapshot,
    per_library_results: Iterable[DiffResult],
    old_snapshots: Mapping[str, AbiSnapshot | BundleSignatureEvidence],
    new_snapshots: Mapping[str, AbiSnapshot | BundleSignatureEvidence],
) -> list[BundleFinding]:
    """`BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED` findings: a sibling library's
    undefined import resolves by name to a provider's export in *new* (the
    same C-linkage match `compare_bundle`'s own `BUNDLE_INTRA_DEP_SIGNATURE_
    CHANGED` detector uses), but the provider's own type evidence for that
    exact symbol cannot confirm or deny that the signature actually agrees
    between *old* and *new* -- distinct from both "no change" (evidence
    agrees) and the confirmed, `BREAKING` `BUNDLE_INTRA_DEP_SIGNATURE_
    CHANGED` (evidence disagrees).

    *old* is used for two things: it resolves each `DiffResult.library` (a
    real on-disk basename) back to its bundle-canonical key via
    :func:`_basename_to_bundle_key`, for the "a confirmed change already
    exists" precedence check below (see that function's docstring for why
    `compare_bundle`'s own signature does not need this), and it supplies
    the version-aware retained-export check
    (:func:`_provider_entry_retained_from_old`) that skips a freshly-added
    symbol *version* sharing an old bare name. It plays no role in the
    per-side evidence-sufficiency check itself, which reads only
    *old_snapshots*/*new_snapshots*.

    *old_snapshots*/*new_snapshots* map bundle-relative library name (the
    same canonical key `BundleSnapshot.libraries` uses) to that library's
    own `AbiSnapshot` -- or, since G38 stabilization Phase 9, the
    cheaper `BundleSignatureEvidence` projection of one (see that type's
    own docstring); both are accepted since this function reads only the
    three fields the projection carries. Either way this is the one input
    `compare_bundle` itself never receives. A provider absent from either
    mapping, or whose symbol was
    not actually part of the *old* side's export surface (`_symbol_was_
    exported`), is skipped for that symbol -- this covers both a genuine
    addition (no declaration on the old side at all) and a symbol that
    existed on the old side only as a private/internal declaration
    (`Visibility.HIDDEN`): `AbiSnapshot` deliberately retains a library's
    private declarations alongside its public ones, so mere presence in
    `function_map`/`variable_map` does not by itself mean the old binary
    ever exported it -- a symbol newly exported in *new* must not inherit
    "retained, evidence uncertain" status from an unrelated old-side
    declaration the old export table never actually carried (Codex
    review). This function only ever compares a symbol that was a real
    export on both sides.

    One finding per (consumer, provider, symbol) triple, mirroring
    `compare_bundle`'s own `BUNDLE_INTRA_DEP_SIGNATURE_CHANGED` granularity
    exactly, so a caller merging both finding lists gets a consistent shape
    regardless of which of the two kinds a given triple produced.
    """
    results = list(per_library_results)
    confirmed = _confirmed_provider_symbols(old, results)
    findings: list[BundleFinding] = []
    seen: set[tuple[str, str, str]] = set()
    reachable_cache: dict[str, set[str]] = {}

    def _reachable(lib: str) -> set[str]:
        if lib not in reachable_cache:
            reachable_cache[lib] = reachable_intra_libraries(new, lib)
        return reachable_cache[lib]

    for symbol, providers in new.resolution.provides.items():
        for provider_entry in providers:
            provider_lib = provider_entry.library
            version_collapsed = _bare_name_version_collapsed(
                old, provider_lib, symbol
            ) or _bare_name_version_collapsed(new, provider_lib, symbol)
            if (provider_lib, symbol) in confirmed and not version_collapsed:
                # A version-blind confirmed change is only trustworthy
                # precedence when there is no version ambiguity to begin
                # with -- when the bare name has collapsed onto multiple
                # co-existing versions, `confirmed`'s own (provider_lib,
                # symbol) key can't tell which version the diff-confirmed
                # change actually describes, so it must not silently
                # suppress the unverified finding for a *different*
                # version pinned to the same bare name.
                continue

            # Restricted to consumers that can actually reach *provider_lib*
            # via a real DT_NEEDED path (Codex review, fresh evidence): a
            # bare `consumers_of(symbol)` is name-only and set-wide, the
            # same limitation `bundle._detect_unresolved_intra_dependency`'s
            # own docstring documents for its own naive alternative -- two
            # unrelated libraries can each export a same-named symbol
            # without either one being loadable together with a given
            # consumer, and this loop's "which consumers does this provider
            # affect" question is exactly the one reachability answers.
            #
            # Also restricted to consumers whose own version/default-binding
            # requirement *this exact provider_entry* actually satisfies
            # (Codex review, fresh evidence): `consumers_of(symbol)` matches
            # by bare name only, so a consumer requiring `foo@V2` previously
            # still paired with a `provider_entry` whose only definition is
            # `foo@V1` -- a provider that cannot actually satisfy that
            # consumer at all (a real resolution failure, already covered
            # by `BUNDLE_UNRESOLVED_INTRA_DEPENDENCY`/
            # `BUNDLE_INTRA_DEP_REMOVED`, not a signature-mismatch risk
            # this module exists to flag). Mirrors `_detect_unresolved_
            # intra_dependency`'s own version/`version_soname`/`is_default`
            # compatibility rules, evaluated per (consumer, provider_entry)
            # pair rather than that function's "does *some* provider in the
            # whole set resolve this" question, since this loop already
            # iterates one concrete provider_entry at a time.
            consumer_libs = sorted(
                {
                    c.library
                    for c in new.resolution.consumers_of(symbol)
                    if c.library != provider_lib
                    and provider_lib in _reachable(c.library)
                    and _consumer_matches_provider(c, provider_entry, new)
                    # A consumer that could not have resolved this exact
                    # symbol from this provider under *old*'s own bindings
                    # (e.g. a default-binding change just made it newly
                    # reachable) has no old-side signature to be
                    # "unverified" against for this edge specifically
                    # (Codex review, fresh evidence) -- see
                    # _consumer_retained_from_old's own docstring for why
                    # this is a per-consumer question, not subsumed by the
                    # per-provider-entry _provider_entry_retained_from_old
                    # check below.
                    and _consumer_retained_from_old(c, old, provider_lib, symbol)
                }
            )
            if not consumer_libs:
                continue

            old_snap = old_snapshots.get(provider_lib)
            new_snap = new_snapshots.get(provider_lib)
            if old_snap is None or new_snap is None:
                continue
            if not _symbol_was_exported(symbol, old_snap):
                # Not a retained symbol whose evidence is in doubt -- either
                # entirely new (never declared in the old snapshot at all),
                # or was only ever a private/internal declaration there
                # (Visibility.HIDDEN) that this bundle's export table never
                # actually carried. Either way, the old side has nothing to
                # be "unverified" about: a genuinely new export doesn't
                # inherit uncertainty from an unrelated old-side declaration
                # that was never part of the export surface.
                continue
            if not _provider_entry_retained_from_old(provider_entry, old, symbol):
                # Not the same export retained across the release -- a
                # different, freshly-added version of this bare-named
                # symbol, so there is no old-side signature for it to be
                # "unverified" against.
                continue

            if version_collapsed:
                # The single bare-name-keyed AbiSnapshot entry can't be
                # attributed to this specific version -- fail closed
                # rather than trust evidence that may belong to a
                # different co-existing version of this symbol.
                old_sufficient = new_sufficient = False
            else:
                old_sufficient = _symbol_evidence_sufficient(symbol, old_snap)
                new_sufficient = _symbol_evidence_sufficient(symbol, new_snap)
            if old_sufficient and new_sufficient:
                continue

            if not old_sufficient and not new_sufficient:
                evidence_gap = "neither side has"
            elif not old_sufficient:
                evidence_gap = "the old side lacks"
            else:
                evidence_gap = "the new side lacks"

            for consumer_lib in consumer_libs:
                key = (consumer_lib, provider_lib, symbol)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    BundleFinding(
                        kind=ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED,
                        symbol=symbol,
                        description=(
                            f"{consumer_lib} calls {symbol} (mangled name "
                            f"unchanged), which {provider_lib} still exports "
                            f"by that name -- but {evidence_gap} real "
                            f"DWARF/header type evidence for this symbol, so "
                            f"whether ABI compatibility actually still holds "
                            f"cannot be confirmed or denied."
                        ),
                        consumer_library=consumer_lib,
                        provider_library=provider_lib,
                        affected_libraries=[consumer_lib],
                    )
                )
    return findings
