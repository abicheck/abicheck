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

"""Bundle cross-library change detectors (split from :mod:`abicheck.bundle`).

Implements the individual ``_detect_*`` finding-producers
:func:`abicheck.bundle.compare_bundle`/:func:`abicheck.bundle.audit_bundle`
orchestrate that don't belong to the manifest-drift/SONAME-skew cluster:
structural (library added/removed), intra-dependency-removed,
intra-dependency-unresolved (the audit-mode sibling of intra-dependency-
removed), intra-dependency-signature-changed, intra-type-changed,
provider-changed, and version-drift -- plus the one heuristic
(:func:`_is_public_surface_symbol`) only these detectors need.

The manifest-drift/SONAME-skew detectors and the system-provider/system-
symbol/system-version/ELF-magic/namespace-stripping primitives several of
*these* detectors depend on live in the sibling
:mod:`abicheck.bundle_detector_heuristics` instead -- this module imports
what it needs from there. Splitting the two purely mechanical detector
groups apart (rather than keeping every ``_detect_*`` function in one
file) is what keeps *both* new files under the AI-readiness 800-line
production cap; a single combined file would have landed at ~1200 lines
with no existing debt-ledger entry to place it under (G38 Phase 15's
file-split prerequisite -- see
``docs/contribute/plans/g38-bundle-facts-model-and-multibuild-comparability.md``).
A mechanical extraction (unchanged function bodies) from ``bundle.py``,
the same reason ``bundle_manifest.py``/``bundle_models.py``/
``bundle_resolution_reachability.py``/``bundle_soname.py`` were already
split out of that module before it. ``bundle.py`` re-exports every name
here that an existing test or caller imports directly (``from
abicheck.bundle import ...``) for back-compat -- new code should import
from here directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .bundle_detector_heuristics import (
    DEFAULT_SYSTEM_SYMBOLS,
    _import_is_external,
    _looks_system,
    _looks_system_symbol,
    _strip_namespace_prefix,
)
from .bundle_models import (
    PROMOTABLE_C_BOUNDARY_SIGNATURE_BREAK_KINDS,
    BundleFinding,
    BundleSnapshot,
    consumer_resolves_via_provider as _consumer_resolves_via_provider,
    diff_change_is_breaking as _diff_change_is_breaking,
)
from .bundle_resolution_reachability import (
    cached_reachable_intra_libraries as _cached_reachable_intra_libraries,
    reachable_intra_libraries as _reachable_intra_libraries,
)
from .bundle_soname import soname_matches_providers
from .checker_policy import ChangeKind, Verdict, policy_kind_sets
from .checker_types import DiffResult
from .elf_metadata import ElfSymbol, SymbolBinding

if TYPE_CHECKING:
    from .bundle_manifest import InstantiationManifest


def _detect_library_structural_changes(
    old: BundleSnapshot,
    new: BundleSnapshot,
) -> list[BundleFinding]:
    """Detect libraries that appeared or disappeared.

    Only emits :class:`ChangeKind.BUNDLE_LIBRARY_REMOVED` when the missing
    library exported at least one symbol consumed by a surviving sibling
    in the old bundle — that is, the removal actually breaks the bundle's
    internal contract. A removal that broke nothing internally is handled
    by the existing ``--fail-on-removed-library`` flow.
    """
    findings: list[BundleFinding] = []
    old_names = set(old.libraries)
    new_names = set(new.libraries)

    for added in sorted(new_names - old_names):
        findings.append(
            BundleFinding(
                kind=ChangeKind.BUNDLE_LIBRARY_ADDED,
                symbol=added,
                description=f"New library {added} appears in the bundle.",
                provider_library=added,
            ),
        )

    for removed in sorted(old_names - new_names):
        # Was the removed lib actually depended on by a surviving sibling?
        # Only emit a bundle finding when the removal actually breaks the
        # internal contract. Stand-alone library removal is handled by
        # the existing --fail-on-removed-library flow.
        old_meta = old.metadata.get(removed)
        consumers: list[str] = []
        if old_meta is not None:
            exports = {s.name for s in old_meta.symbols}
            for sib_name, sib_meta in old.metadata.items():
                if sib_name == removed or sib_name not in new.metadata:
                    continue
                if any(imp.name in exports for imp in sib_meta.imports):
                    consumers.append(sib_name)
        if not consumers:
            continue
        findings.append(
            BundleFinding(
                kind=ChangeKind.BUNDLE_LIBRARY_REMOVED,
                symbol=removed,
                description=(
                    f"Library {removed} removed from the bundle; "
                    f"depended on by: {', '.join(sorted(consumers))}"
                ),
                provider_library=removed,
                affected_libraries=consumers,
            ),
        )

    return findings


def _detect_intra_dep_removed(
    old: BundleSnapshot,
    new: BundleSnapshot,
    system_providers: set[str],
    explicit_providers: set[str],
) -> list[BundleFinding]:
    """Find imports in the new bundle that no sibling provides.

    Excludes imports satisfied by system libraries (out of bundle scope by
    design). Classification uses provider/version evidence first
    (:func:`_import_is_external`) and the symbol-name allow-list
    (``DEFAULT_SYSTEM_SYMBOLS`` / ``_looks_system_*``) as a fallback.
    Excludes weak imports (linker treats unresolved weak as 0/NULL).

    A consumer's import is treated as system-provided when every DT_NEEDED
    edge it carries that resolves *outside* the bundle is in the
    ``system_providers`` allow-list -- but only when either (a) *this
    consumer* never reached a *version-compatible* in-bundle sibling
    providing this symbol in ``old`` (checked via reachability, not merely
    a same-named provider existing somewhere in the old bundle -- an
    unrelated consumer's own old provider must not veto a different
    consumer's always-external dependency, nor may a provider whose
    version could never have satisfied this consumer's own reference), or
    (b) the user explicitly named at least one of this consumer's
    remaining sonames via ``explicit_providers``. Without (a)/(b), a
    sibling provider and its DT_NEEDED edge could have been dropped by the
    same refactor that left this consumer needing libc -- so absent
    either, the symbol-name check below still has to agree.
    """
    findings: list[BundleFinding] = []
    old_reachable_cache: dict[str, set[str]] = {}

    def _old_reachable(lib: str) -> set[str]:
        if lib not in old_reachable_cache:
            old_reachable_cache[lib] = _reachable_intra_libraries(old, lib)
        return old_reachable_cache[lib]

    for symbol, consumers in new.resolution.consumers.items():
        providers = new.resolution.providers_for(symbol)
        if providers:
            continue  # someone in the bundle provides it
        old_all_providers = old.resolution.providers_for(symbol)
        for consumer in consumers:
            if consumer.weak:
                continue
            consumer_meta = new.metadata.get(consumer.library)
            if consumer_meta is None:
                continue
            # Did *this* consumer -- not merely some other consumer of the
            # same symbol name -- previously reach a *version-compatible*
            # old provider (docstring above; mirrors the audit-mode
            # sibling's identical compatibility rule)?
            if consumer.version:
                compatible_old = {
                    p.library
                    for p in old_all_providers
                    if p.version == consumer.version
                }
            else:
                compatible_old = {p.library for p in old_all_providers if p.is_default}
            ever_provided_in_bundle = bool(
                compatible_old
                and consumer.library in old.libraries
                and compatible_old & _old_reachable(consumer.library)
            )
            if _import_is_external(consumer, consumer_meta, new):
                continue
            # Every non-intra DT_NEEDED on the allow-list AND (no sibling
            # ever provided this symbol, OR the user explicitly asserted a
            # remaining soname) -> trust it unconditionally. Otherwise fall
            # through to the symbol-name check (docstring above).
            # Known limitations (Codex review, shared by the audit-mode
            # sibling below): (1) absence of a *bundle* regression is not
            # proof of a system export (no export-table parse); (2) `all()`
            # below is over every extra edge, not just whichever provides
            # `symbol`. DEFAULT_SYSTEM_PROVIDERS broadened to reduce (2).
            extra_needed = new.resolution.extra_needed.get(consumer.library, [])
            if (
                extra_needed
                and all(
                    soname_matches_providers(e, system_providers) or _looks_system(e)
                    for e in extra_needed
                )
                and (
                    not ever_provided_in_bundle
                    or any(
                        soname_matches_providers(e, explicit_providers)
                        for e in extra_needed
                    )
                )
            ):
                continue
            if symbol in DEFAULT_SYSTEM_SYMBOLS or _looks_system_symbol(symbol):
                continue
            findings.append(
                BundleFinding(
                    kind=ChangeKind.BUNDLE_INTRA_DEP_REMOVED,
                    symbol=symbol,
                    description=(
                        f"{consumer.library} imports {symbol}, but no library in "
                        f"the new bundle exports it. Runtime load of "
                        f"{consumer.library} will fail with undefined symbol."
                    ),
                    consumer_library=consumer.library,
                    affected_libraries=[consumer.library],
                ),
            )
    return findings


def _detect_unresolved_intra_dependency(
    new: BundleSnapshot,
    system_providers: set[str],
) -> list[BundleFinding]:
    """Audit-mode (no old side) sibling of :func:`_detect_intra_dep_removed`.

    ADR-056 D2: ``scan --artifact-set`` has no per-library diff to read, so
    this operates purely off the new-side resolution graph. Deliberately
    **not** a call into :func:`_detect_intra_dep_removed`; differs in three
    ways that matter for soundness here:

    1. **Version-aware, reachability-constrained provider matching.**
       ``providers_for(symbol)`` is name-only and set-wide.
       ``ProviderEntry.version``/``ConsumerEntry.version`` are consulted so a
       version mismatch (consumer needs ``foo@V2``, set only provides
       ``foo@V1``) is not mistaken for a resolved import; when the precise
       ``ConsumerEntry.version_soname`` is known, the match is pinned to
       that exact provider library (GNU version *labels* are not globally
       unique). Every candidate provider must additionally be reachable
       from the consumer through :func:`_reachable_intra_libraries` — a
       match on a library the consumer has no ``DT_NEEDED`` path to would
       never actually be loaded together with the consumer.
    2. **A narrower, explicitly-approximate suppression path for unversioned
       imports.** Mirrors ``_detect_intra_dep_removed``'s allow-list union
       and its non-empty guard (``extra_edges and all(...)``, never a bare
       ``all([])``), but adds a requirement one-sided audit needs and the
       diff-driven detector does not: the consumer must have **zero**
       intra-bundle ``DT_NEEDED`` edges — a consumer still depending on an
       intra-set library that simply stopped exporting the symbol has a
       real, in-set candidate provider this coarse check cannot rule out.
       Deliberately has **no** symbol-name-shape fallback
       (``_looks_system_symbol``): ``--bundle-system-providers`` exists
       specifically to cover a legitimate, non-system-shaped custom export
       (e.g. ``vendor_init``) that a shape heuristic would never match.
    3. Emits ``ChangeKind.BUNDLE_UNRESOLVED_INTRA_DEPENDENCY`` (not
       ``BUNDLE_INTRA_DEP_REMOVED`` — that kind implies a diff-confirmed
       removal, which this finding cannot claim) at
       ``COMPATIBLE_WITH_RISK``, not ``BREAKING``: an audit has no old side
       to confirm the symbol ever resolved.
    """
    findings: list[BundleFinding] = []
    reachable_cache: dict[str, set[str]] = {}

    def _reachable(lib: str) -> set[str]:
        if lib not in reachable_cache:
            reachable_cache[lib] = _reachable_intra_libraries(new, lib)
        return reachable_cache[lib]

    for symbol, consumers in new.resolution.consumers.items():
        providers = new.resolution.providers_for(symbol)
        for consumer in consumers:
            if consumer.weak:
                continue
            consumer_meta = new.metadata.get(consumer.library)
            if consumer_meta is None:
                continue
            reachable = _reachable(consumer.library)

            if consumer.version:
                if consumer.version_soname:
                    # Same soname_to_name map _reachable_intra_libraries()
                    # uses (Codex review) -- provider_library_for_soname()'s
                    # independent heuristic has the identical
                    # resolved-through-symlink gap, so a version_soname
                    # naming a provider's real on-disk filename could fail
                    # to resolve here even when that provider is genuinely
                    # reachable.
                    target_lib = new.resolution.soname_to_name.get(
                        consumer.version_soname
                    )
                    resolved = target_lib is not None and target_lib in reachable
                    resolved = resolved and any(
                        p.library == target_lib and p.version == consumer.version
                        for p in providers
                    )
                else:
                    resolved = any(
                        p.version == consumer.version and p.library in reachable
                        for p in providers
                    )
            else:
                # P2 regression (Codex review): an unversioned consumer
                # reference can only be satisfied by an unversioned or
                # default-version ("@@default") provider definition -- a
                # provider that exports this symbol *only* as a non-default
                # versioned definition ("foo@V1", not "foo@@V1") cannot
                # satisfy it, even though the bare symbol name is reachable.
                resolved = any(
                    p.library in reachable and p.is_default for p in providers
                )

            if resolved:
                continue
            if _import_is_external(consumer, consumer_meta, new):
                continue

            if not consumer.version:
                intra_edges = new.resolution.intra_needed.get(consumer.library, [])
                extra_edges = new.resolution.extra_needed.get(consumer.library, [])
                if (
                    not intra_edges
                    and extra_edges
                    and all(
                        soname_matches_providers(e, system_providers)
                        or _looks_system(e)
                        for e in extra_edges
                    )
                ):
                    continue

            findings.append(
                BundleFinding(
                    kind=ChangeKind.BUNDLE_UNRESOLVED_INTRA_DEPENDENCY,
                    symbol=symbol,
                    description=(
                        f"{consumer.library} imports {symbol}, but no provider "
                        "was found for it in this artifact set (audit mode — "
                        "no old side to confirm this ever resolved)."
                    ),
                    consumer_library=consumer.library,
                    affected_libraries=[consumer.library],
                ),
            )
    return findings


# Linker/runtime-synthesized per-object symbols present, under their own
# independent definition, in virtually every ELF shared object (crt/glibc
# start files, the toolchain's implicit array markers). Each library gets
# its own copy from its own crt object -- this is normal ELF plumbing, not
# an ownership collision -- so _detect_duplicate_providers excludes them
# rather than flagging one on every multi-library artifact set.
_LINKER_SYNTHESIZED_SYMBOLS: frozenset[str] = frozenset(
    {
        "_edata",
        "_end",
        "__bss_start",
        "__bss_start__",
        "_end__",
        "_init",
        "_fini",
        "_start",
        "_DYNAMIC",
        "_GLOBAL_OFFSET_TABLE_",
        "__dso_handle",
        "_IO_stdin_used",
        "__data_start",
        "data_start",
        "__environ",
        "environ",
        "__gmon_start__",
        "__TMC_END__",
        "__preinit_array_start",
        "__preinit_array_end",
        "__init_array_start",
        "__init_array_end",
        "__fini_array_start",
        "__fini_array_end",
        "__JCR_LIST__",
        "__JCR_END__",
    }
)


def _detect_duplicate_providers(new: BundleSnapshot) -> list[BundleFinding]:
    """Audit-mode: flag a symbol exported as a *strong, default* definition
    by 2+ members of one ``--artifact-set``.

    ADR-056 D2 (PR H): a single-side sibling of :func:`_detect_provider_changed`
    -- that needs an old side (removed-here/added-there) an audit doesn't
    have. What an audit *can* see: two libraries both defining the same
    symbol as their own strong (``STB_GLOBAL``), default-bound export means
    an unversioned reference resolves to whichever the dynamic linker's
    load-order rules pick first, not a declared contract. Only
    ``is_default`` providers count (mirrors
    :class:`~abicheck.bundle_models.ProviderEntry.is_default`).

    **Only strong providers count** (Codex review, fresh evidence): a
    weak/``STB_GNU_UNIQUE`` copy is ordinary C++ vague linkage (an inline
    function/template instantiation COMDAT every DSO that uses it emits
    identically), deduplicated by the dynamic linker at load time -- not
    two libraries disputing ownership.

    Deliberately conservative on false positives: linker/runtime-
    synthesized per-object symbols (:data:`_LINKER_SYNTHESIZED_SYMBOLS`)
    and libstdc++-shaped names (:func:`~abicheck.bundle_detector_heuristics.
    _looks_system_symbol`) are excluded -- either would fire on nearly
    every real multi-library set for no ownership reason at all.

    **Known, deliberately-deferred gap:** no L4 symbol reconciliation
    (same gap as :func:`~abicheck.bundle.artifact_set_member_exports`, see
    ``docs/contribute/known-gaps.md``) -- two raw exports spelling one
    declaration under different mangling variants could misread as two
    single-provider symbols rather than one duplicate, or vice versa.
    Closing it needs each member's own L4 mapping threaded into the bundle
    layer, a materially larger change left for a follow-up.
    """
    findings: list[BundleFinding] = []
    for symbol, providers in sorted(new.resolution.provides.items()):
        if symbol in _LINKER_SYNTHESIZED_SYMBOLS or _looks_system_symbol(symbol):
            continue
        default_libs = sorted(
            {
                p.library
                for p in providers
                if p.is_default and p.binding == SymbolBinding.GLOBAL
            }
        )
        if len(default_libs) < 2:
            continue
        findings.append(
            BundleFinding(
                kind=ChangeKind.BUNDLE_DUPLICATE_PROVIDER,
                symbol=symbol,
                description=(
                    f"{symbol} is exported as a strong (non-weak) default "
                    f"definition by {len(default_libs)} libraries in this "
                    f"artifact set ({', '.join(default_libs)}); which one a "
                    "consumer resolves against depends on load order / "
                    "symbol interposition, not a declared contract."
                ),
                affected_libraries=default_libs,
            ),
        )
    return findings


def _detect_manifest_ownership(
    new: BundleSnapshot,
    manifest: InstantiationManifest,
) -> list[BundleFinding]:
    """Audit-mode (no old side) sibling of ``compare --manifest``'s
    ownership check (:func:`~abicheck.bundle_detector_heuristics.
    _detect_manifest_drift`).

    ADR-056 D2 (PR H): no old side, so no "newly promised" half (that pass
    is inherently a diff) -- only whether the promise holds *right now*.
    Delegates to :func:`~abicheck.bundle_detector_heuristics.
    _manifest_ownership_findings`, the identical "missing"/"wrong provider"
    logic ``compare --manifest`` uses, so the two entry points can't
    diverge on what "wrong provider" means. Emits
    ``BUNDLE_MANIFEST_ENTRY_UNSATISFIED`` -- distinct from
    ``BUNDLE_MANIFEST_INSTANTIATION_REMOVED``, which implies a
    diff-confirmed regression this audit cannot claim.
    """
    from .bundle_detector_heuristics import _manifest_ownership_findings

    return _manifest_ownership_findings(
        new,
        manifest,
        kind=ChangeKind.BUNDLE_MANIFEST_ENTRY_UNSATISFIED,
        scope_desc="this artifact set",
    )


def _detect_intra_dep_signature_changed(
    new: BundleSnapshot,
    diff_by_library: dict[str, DiffResult],
    policy: str = "strict_abi",
) -> list[BundleFinding]:
    """Promote provider-side signature changes to consumer-side findings.

    For each confirmed *promotable* C-boundary signature break
    (:data:`~abicheck.bundle_models.PROMOTABLE_C_BOUNDARY_SIGNATURE_BREAK_
    KINDS`, a strict subset of ``CONFIRMED_C_BOUNDARY_SIGNATURE_BREAK_
    KINDS``), find siblings importing the symbol that *actually resolve it
    against this provider* (:func:`_consumer_resolves_via_provider`),
    emitting one finding per (consumer, symbol) pair. *policy*: a
    named-policy demotion and a ``--policy-file`` override on the diff are
    both honored, via :func:`_diff_change_is_breaking`. Scanned including
    ``diff.out_of_surface_changes`` (G38 Phase 14): a headerless break
    demoted by ``--scope-public-headers`` still breaks the bundle's own
    linkage contract (``docs/use/multi-binary.md``).
    """
    findings: list[BundleFinding] = []
    seen: set[tuple[str, str, str]] = set()
    relevant_kinds = PROMOTABLE_C_BOUNDARY_SIGNATURE_BREAK_KINDS
    policy_sets = policy_kind_sets(policy)
    reachable_cache: dict[str, set[str]] = {}

    def _reachable(lib: str) -> set[str]:
        return _cached_reachable_intra_libraries(new, reachable_cache, lib)

    for provider_lib, diff in diff_by_library.items():
        for change in diff.changes + diff.out_of_surface_changes:
            if change.kind not in relevant_kinds:
                continue
            if not _diff_change_is_breaking(diff, change, policy_sets):
                continue
            consumers = new.resolution.consumers_of(change.symbol)
            consumer_libs = sorted(
                {
                    c.library
                    for c in consumers
                    if c.library != provider_lib
                    and _consumer_resolves_via_provider(
                        new, c, provider_lib, change.symbol, _reachable(c.library)
                    )
                }
            )
            if not consumer_libs:
                continue
            for consumer_lib in consumer_libs:
                key = (consumer_lib, provider_lib, change.symbol)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    BundleFinding(
                        kind=ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED,
                        symbol=change.symbol,
                        description=(
                            f"{consumer_lib} calls {change.symbol} (mangled name "
                            f"unchanged) but {provider_lib} altered its DWARF "
                            f"signature. Calling convention is now mismatched."
                        ),
                        consumer_library=consumer_lib,
                        provider_library=provider_lib,
                        old_value=change.old_value,
                        new_value=change.new_value,
                        affected_libraries=[consumer_lib],
                    ),
                )
    return findings


def _detect_intra_type_changed(
    old: BundleSnapshot,
    new: BundleSnapshot,
    diff_by_library: dict[str, DiffResult],
) -> list[BundleFinding]:
    """Detect a type layout change that crosses a DSO boundary.

    Conservative heuristic: a ``type_*_changed`` against type ``T`` in
    library A counts as cross-DSO iff *some other library B* in the bundle
    exports a symbol whose name contains ``T`` (template instantiation,
    mangled signature reference). Catches the ``detail::``-style
    pattern where a type defined in core leaks into algo's mangled
    symbols. Misses extern-C function pointers that pass struct
    references (would require type-graph propagation from DWARF, future
    work — out of scope for ADR-023 first cut). Scanned including
    ``diff.out_of_surface_changes`` (G38 Phase 14, rationale in
    ``_detect_intra_dep_signature_changed``'s own docstring).

    Reachability scope (ADR-027 A3 / D3.2 limitation). The public-vs-internal
    split below is computed from ``ElfMetadata.symbols``, which is parsed from
    ``.dynsym`` and therefore holds only **exported** (GLOBAL/WEAK,
    non-hidden) definitions — LOCAL and hidden symbols are not retained
    (``elf_metadata._parse_dynsym``). Consequently a consumer that references
    the changed type **only** from its own internal/hidden functions leaves no
    trace here: no exported symbol carries the type name, ``consumer_reach``
    stays empty, and no finding is emitted for that consumer. The
    ``internal_hit`` (demote-to-risk) branch therefore fires only for inputs
    whose snapshots happen to carry local symbols (e.g. relocatable objects or
    DWARF-derived graphs); detecting purely internal cross-DSO references from
    a stripped shared object would require retaining non-exported symbols or a
    full type graph, deliberately out of scope (documented per the ADR-027
    review — the conservative miss is preferred over a parser/schema change
    with broad blast radius).
    """
    findings: list[BundleFinding] = []
    # Dedup: one finding per (consumer, provider, type) triple — multiple
    # low-level changes (size + alignment + field-removed) against the
    # same type would otherwise emit N copies of the same cross-DSO break.
    seen: set[tuple[str, str, str]] = set()
    type_kinds = {
        ChangeKind.TYPE_SIZE_CHANGED,
        ChangeKind.TYPE_ALIGNMENT_CHANGED,
        ChangeKind.TYPE_FIELD_REMOVED,
        ChangeKind.TYPE_FIELD_OFFSET_CHANGED,
        ChangeKind.TYPE_FIELD_TYPE_CHANGED,
        ChangeKind.TYPE_BASE_CHANGED,
        ChangeKind.TYPE_VTABLE_CHANGED,
        ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API,
    }
    for provider_lib, diff in diff_by_library.items():
        for change in diff.changes + diff.out_of_surface_changes:
            if change.kind not in type_kinds:
                continue
            type_name = change.symbol
            # Look for the type name embedded in another library's symbols.
            # ADR-027 A3/D3.2 reachability filter: classify each consumer match
            # as reaching its *public* surface (the type leaks into a symbol the
            # consumer itself exports — full-confidence cross-DSO break) vs only
            # its *internal* surface (the type appears only in the consumer's
            # local/hidden symbols — demoted to risk, not dropped, because the
            # consumer does not re-expose the type across the boundary).
            stripped = _strip_namespace_prefix(type_name)
            consumer_reach: dict[str, bool] = {}  # consumer -> reaches public surface
            for sib_name, sib_meta in new.metadata.items():
                if sib_name == provider_lib:
                    continue
                public_hit = False
                internal_hit = False
                for sym in sib_meta.symbols:
                    if stripped and stripped in sym.name:
                        if _is_public_surface_symbol(sym):
                            public_hit = True
                            break
                        internal_hit = True
                if public_hit or internal_hit:
                    consumer_reach[sib_name] = public_hit
            for consumer_lib in sorted(consumer_reach):
                key = (consumer_lib, provider_lib, type_name)
                if key in seen:
                    continue
                seen.add(key)
                on_public = consumer_reach[consumer_lib]
                finding = BundleFinding(
                    kind=ChangeKind.BUNDLE_INTRA_TYPE_CHANGED,
                    symbol=type_name,
                    description=(
                        f"{provider_lib} changed type {type_name}; the type "
                        f"is reachable from {consumer_lib}'s exported symbols. "
                        f"{consumer_lib}'s ABI looks unchanged in isolation, "
                        f"but every cross-DSO use of {type_name} is affected."
                    ),
                    consumer_library=consumer_lib,
                    provider_library=provider_lib,
                    affected_libraries=[consumer_lib],
                )
                if not on_public:
                    # Consumer uses the type only internally → demote (disclosed,
                    # never dropped): the cross-DSO change cannot reach the
                    # consumer's own public ABI surface.
                    finding.effective_verdict = Verdict.COMPATIBLE_WITH_RISK
                    finding.modulation_reason = "consumer-internal-use"
                    finding.modulation_rule = "bundle-reachability"
                    finding.description = (
                        f"{provider_lib} changed type {type_name}; {consumer_lib} "
                        f"references it only via internal (non-exported) symbols, "
                        f"so the change does not reach {consumer_lib}'s public ABI "
                        f"surface — review, but not a confirmed cross-DSO break."
                    )
                findings.append(finding)
    return findings


def _is_public_surface_symbol(sym: ElfSymbol) -> bool:
    """True when *sym* is part of a library's exported public ABI surface.

    A defined GLOBAL/WEAK symbol with default/protected visibility is reachable
    by external consumers; LOCAL binding or hidden/internal visibility is not
    (it is the DSO's private implementation). Used by the A3 reachability filter
    to tell a real cross-DSO break from an internal-only type reference.
    """
    if sym.binding not in (SymbolBinding.GLOBAL, SymbolBinding.WEAK):
        return False
    return sym.visibility in ("default", "protected")


def _detect_provider_changed(
    new: BundleSnapshot,
    diff_by_library: dict[str, DiffResult],
) -> list[BundleFinding]:
    """Detect symbol provider migration within the bundle.

    A symbol that was removed from library A in this release and added
    (with the same mangled name) to library B in the same release is most
    likely a provider move, not an ABI change. Promote both per-library
    findings into one ``BUNDLE_PROVIDER_CHANGED`` event. Scanned including
    ``diff.out_of_surface_changes`` (G38 Phase 14) -- deliberately still
    with no reachability requirement, unlike its two siblings: a provider
    move breaks an external consumer exactly as much as a sibling (ADR-023).
    """
    findings: list[BundleFinding] = []

    removed_by: dict[str, str] = {}  # symbol -> library that removed it
    added_by: dict[str, str] = {}  # symbol -> library that added it
    for lib_name, diff in diff_by_library.items():
        for change in diff.changes + diff.out_of_surface_changes:
            if change.kind in (ChangeKind.FUNC_REMOVED, ChangeKind.VAR_REMOVED):
                removed_by.setdefault(change.symbol, lib_name)
            elif change.kind in (ChangeKind.FUNC_ADDED, ChangeKind.VAR_ADDED):
                added_by.setdefault(change.symbol, lib_name)

    for symbol, old_lib in removed_by.items():
        new_lib = added_by.get(symbol)
        if new_lib is None or new_lib == old_lib:
            continue
        # Confirm the symbol exists in the new bundle at the new provider.
        providers = new.resolution.providers_for(symbol)
        if not any(p.library == new_lib for p in providers):
            continue
        findings.append(
            BundleFinding(
                kind=ChangeKind.BUNDLE_PROVIDER_CHANGED,
                symbol=symbol,
                description=(
                    f"Symbol {symbol} moved from {old_lib} to {new_lib} within "
                    f"the bundle. Downstream consumers with DT_NEEDED on "
                    f"{old_lib} only resolve transitively if their dependency "
                    f"chain reaches {new_lib}."
                ),
                provider_library=new_lib,
                old_value=old_lib,
                new_value=new_lib,
                affected_libraries=[old_lib, new_lib],
            ),
        )

    return findings


def _detect_version_drift(
    old: BundleSnapshot,
    new: BundleSnapshot,
) -> list[BundleFinding]:
    """Detect gnu.version_d drift on intra-bundle imports.

    Compares each new-side consumer import's required version against the
    old-side provider's defined version for the same symbol. Emits one
    finding per symbol whose version moved.
    """
    findings: list[BundleFinding] = []

    # Build (symbol -> old default version) from old bundle.
    old_default_version: dict[str, str] = {}
    for sym_name, providers in old.resolution.provides.items():
        for prov in providers:
            if prov.version:
                old_default_version.setdefault(sym_name, prov.version)
                break

    new_default_version: dict[str, str] = {}
    for sym_name, providers in new.resolution.provides.items():
        for prov in providers:
            if prov.version:
                new_default_version.setdefault(sym_name, prov.version)
                break

    common = set(old_default_version) & set(new_default_version)
    for sym in sorted(common):
        if old_default_version[sym] == new_default_version[sym]:
            continue
        consumers = new.resolution.consumers_of(sym)
        consumer_libs = sorted({c.library for c in consumers})
        if not consumer_libs:
            continue
        findings.append(
            BundleFinding(
                kind=ChangeKind.BUNDLE_INTRA_DEP_VERSION_DRIFT,
                symbol=sym,
                description=(
                    f"Symbol {sym} now exported at version "
                    f"{new_default_version[sym]} (was {old_default_version[sym]}); "
                    f"siblings {', '.join(consumer_libs)} pick up the new version."
                ),
                old_value=old_default_version[sym],
                new_value=new_default_version[sym],
                affected_libraries=consumer_libs,
            ),
        )

    return findings
