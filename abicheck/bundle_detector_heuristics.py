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

"""Bundle manifest/SONAME-skew matching heuristics (split from
:mod:`abicheck.bundle_detectors`).

The manifest-drift detector (:func:`_detect_manifest_drift`) and its
demangled-name-index matching helpers (:func:`_entry_targets`,
:func:`_build_demangled_index`, :func:`_match_target_against_index`,
:func:`_match_entry`), the opt-in SONAME-skew detector
(:func:`_detect_soname_skew`/:func:`_soname_skew_findings`), and the
system-provider/system-symbol/system-version/ELF-magic/namespace-
stripping primitives several of :mod:`abicheck.bundle_detectors`'s own
detectors depend on (:func:`_looks_system`, :func:`_looks_system_symbol`,
:func:`_looks_system_version`, :func:`_import_is_external`,
:func:`_path_looks_like_elf`, :func:`_strip_namespace_prefix`).

Extracted purely to keep :mod:`abicheck.bundle_detectors` itself under the
AI-readiness 800-line production cap for a *new* file (G38 Phase 15's
file-split prerequisite -- see
``docs/contribute/plans/g38-bundle-facts-model-and-multibuild-comparability.md``);
:mod:`abicheck.bundle_detectors` imports the primitives its own detectors
need from here, one-directionally (nothing here depends back on
:mod:`abicheck.bundle_detectors`). :mod:`abicheck.bundle` re-exports every
name here that an existing test or caller imports directly (``from
abicheck.bundle import ...``) for back-compat -- new code should import
from here directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .bundle_manifest import (
    InstantiationManifest,
    ManifestEntry,
    _expand_instantiations,
)
from .bundle_models import BundleFinding, BundleSnapshot, ConsumerEntry, ProviderEntry
from .checker_policy import ChangeKind
from .elf_metadata import ElfMetadata

if TYPE_CHECKING:
    from .diff_cpp_patterns import BundleMember

# Manifest-matching cooperative checkpoint granularity (Codex review, PR H,
# second round): frequent enough that a real --budget overrun is caught
# well before it compounds, coarse enough that deadline.check()'s own
# time.monotonic() read doesn't dominate a large index scan.
_DEADLINE_CHECK_INTERVAL = 2000


def _soname_skew_findings(
    old_members: list[BundleMember],
    new_members: list[BundleMember],
    cohorts: list[str],
) -> list[BundleFinding]:
    """Pure cohort-skew logic over already-read bundle members.

    SONAME skew is **only** evaluated within explicitly declared cohorts —
    each entry of *cohorts* is a cohort-key prefix (e.g. ``"libfoo_"``)
    naming a set of libraries the release engineer asserts are co-versioned.
    Libraries that match no declared cohort are never compared, so a normal
    release that bumps an independent ``libfoo.so.1 → libfoo.so.2`` while an
    unrelated ``libbar.so.1`` stays put is never reported. With an empty
    *cohorts* list this returns nothing: there is no implicit lockstep
    invariant to infer from filenames alone.
    """
    # An empty prefix (e.g. --bundle-cohort "" from an unset shell var) would
    # be treated as "no filter" by the detector and compare every DSO —
    # reintroducing the global false positive the opt-in exists to prevent.
    # Strip and drop blanks so only genuine cohort prefixes are honoured.
    prefixes = [p.strip() for p in cohorts if p and p.strip()]
    if not prefixes:
        return []
    from .diff_cpp_patterns import detect_bundle_soname_skew

    findings: list[BundleFinding] = []
    for prefix in prefixes:
        for change in detect_bundle_soname_skew(
            old_members,
            new_members,
            cohort_prefix=prefix,
        ):
            findings.append(
                BundleFinding(
                    kind=change.kind,
                    symbol=change.symbol,
                    description=change.description,
                    old_value=change.old_value,
                    new_value=change.new_value,
                    affected_libraries=list(change.affected_symbols or []),
                )
            )
    return findings


def _detect_soname_skew(
    old: BundleSnapshot,
    new: BundleSnapshot,
    cohorts: list[str] | None,
) -> list[BundleFinding]:
    """Detect inconsistent SONAME major bumps within declared cohorts.

    *cohorts* is the explicit opt-in: a list of cohort-key prefixes naming
    co-versioned library sets (from ``compare-release --bundle-cohort``).
    When it is empty/None nothing is emitted — there is no auto-grouping of
    independent libraries by filename, which avoids false positives on
    normal multi-library releases.

    Members are derived from the *matched* release libraries
    (``BundleSnapshot.libraries`` / ``.metadata``) rather than by rescanning
    a single directory — release discovery is recursive, so a cohort member
    living in another subdirectory must still participate. The authoritative
    major comes from each library's DT_SONAME, falling back to the on-disk
    filename; libraries with no derivable major (unversioned ``libfoo.so``)
    are dropped.
    """
    cohorts = [c.strip() for c in (cohorts or []) if c and c.strip()]
    if not cohorts:
        return []
    from .binary_utils import strip_vendor_hash
    from .diff_cpp_patterns import BundleMember, _extract_soname_major

    def _members(snap: BundleSnapshot) -> list[BundleMember]:
        from .bundle_soname import resolved_basename

        members: list[BundleMember] = []
        for name, path in snap.libraries.items():
            meta = snap.metadata.get(name)
            # Resolved target's basename (matches capture_bundle_facts()'s
            # identical use) -- but only when filesystem_backed: a facts-
            # reconstructed snapshot's paths are synthetic (bare
            # `Path("libfoo.so.1")`), and Path.resolve() would still walk
            # CWD for those, letting an unrelated same-named CWD entry
            # override the persisted basename (Codex review). Use it as-is.
            real_name = resolved_basename(path) if snap.filesystem_backed else path.name
            soname = (meta.soname if meta and meta.soname else "") or real_name
            # G9 (remaining half): DT_SONAME is read directly off the ELF
            # here, unlike `.library` (the on-disk filename), which the
            # cohort key normalizes via strip_vendor_hash downstream. An
            # auditwheel/delocate-vendored library's SONAME carries the same
            # content-hash suffix as its filename (e.g.
            # `libfoo_core-a1b2c3d4.so.2`) — strip it first so both the major
            # extraction below and `BundleMember.soname` see the canonical
            # logical SONAME, matching `.library`'s normalization. A hashed
            # *versioned* dylib (`libfoo.2-a1b2c3.dylib`) has the hash
            # between the major and the extension, so extracting the major
            # from the raw string misses it entirely (Codex review).
            stripped_soname = strip_vendor_hash(soname)
            major = _extract_soname_major(stripped_soname)
            if major is None:
                major = _extract_soname_major(strip_vendor_hash(real_name))
            if major is None:
                continue
            members.append(
                BundleMember(
                    library=real_name,
                    soname=stripped_soname,
                    soname_major=major,
                )
            )
        return members

    old_members = _members(old)
    new_members = _members(new)
    if not old_members or not new_members:
        return []
    return _soname_skew_findings(old_members, new_members, cohorts)


def _entry_targets(entry: ManifestEntry) -> list[tuple[str, str]]:
    """Decompose a manifest entry into ``[(display_name, match_kind)]``.

    Where ``match_kind`` is one of:
        - ``"symbol"`` — literal equality against ``.dynsym``.
        - ``"pattern"`` — fnmatch glob against the *demangled* form.
        - ``"template"`` — substring match against the demangled form.

    A symbol or pattern entry yields one target; a template entry
    yields **one target per instantiation**, so each instantiation is
    matched (and reported) independently. The reviewer's regression:
    a single template entry with four instantiations where only two
    are exported previously short-circuited at "any match found" and
    declared the entry satisfied. Per-instantiation decomposition
    makes the contract explicit and gives users one finding per
    missing instantiation.
    """
    if entry.symbol is not None:
        return [(entry.symbol, "symbol")]
    if entry.pattern is not None:
        return [(entry.pattern, "pattern")]
    expanded = _expand_instantiations(entry.template or "", entry.instantiations)
    return [(t, "template") for t in expanded]


def _build_demangled_index(snapshot: BundleSnapshot) -> list[tuple[str, str]]:
    """Return ``[(demangled_name, library_name)]`` for every public export.

    Performed once per :func:`_match_entry` call so manifest checking is
    O(symbols + targets × index) rather than O(symbols × targets) — for
    a large bundle (~50k exported symbols) with a manifest
    containing hundreds of template instantiations, the naïve
    re-scan-per-target path would dominate ``compare-release`` runtime
    now that bundle analysis is default-on.

    Demangling uses :func:`abicheck.demangle.demangle`; when the
    demangler is unavailable, the mangled name is recorded so
    ``extern "C"`` symbols still match.

    Only ``is_default`` symbols are indexed (Codex review, security P1,
    PR H): a manifest entry always matches by bare/unversioned name (see
    :func:`_match_target_against_index`'s ``symbol`` branch, which keys
    :attr:`~abicheck.bundle_models.ResolutionGraph.provides` the identical
    way), and the dynamic linker can only satisfy an unversioned reference
    against a default (``@@version`` or unversioned) definition -- a DSO
    exporting only a non-default ``@version`` definition of the name
    cannot actually be linked against by an unversioned consumer, even
    though the bare name is technically present in ``.dynsym``.

    Checkpoints ``deadline.check()`` every :data:`_DEADLINE_CHECK_INTERVAL`
    symbols (Codex review, PR H, second round): a ~50k-symbol bundle
    demangled in one call has no other cooperative checkpoint of its own,
    so a small ``--budget`` could otherwise be overrun well before any
    per-target checkpoint in :func:`_match_entry` is ever reached.
    """
    from . import deadline
    from .demangle import demangle as _demangle

    index: list[tuple[str, str]] = []
    seen = 0
    for lib_name, meta in snapshot.metadata.items():
        for sym in meta.symbols:
            if sym.visibility not in ("default", "protected"):
                continue
            if not sym.is_default:
                continue
            seen += 1
            if seen % _DEADLINE_CHECK_INTERVAL == 0:
                deadline.check()
            index.append((_demangle(sym.name) or sym.name, lib_name))
    return index


def _match_target_against_index(
    target: str,
    kind: str,
    snapshot: BundleSnapshot,
    index: list[tuple[str, str]] | None = None,
) -> tuple[list[str], list[ProviderEntry]]:
    """Find every export in *snapshot* that satisfies *target* of *kind*.

    Returns ``(matched_demangled_names, providers)``.  The provider list
    has one :class:`ProviderEntry` per library that exports a matching
    symbol (de-duplicated; one entry per library, not per symbol).

    When *index* is supplied (a pre-built demangled-name → library
    mapping), the scan operates against the cached list. Callers
    iterating many targets against the same snapshot should pass a
    shared index to amortise the demangle pass.
    """
    import fnmatch

    if kind == "symbol":
        # Only a *default* definition satisfies a manifest's own
        # unversioned/bare-name promise (Codex review, security P1, PR H)
        # -- see _build_demangled_index()'s identical guard for the
        # pattern/template branch below and its docstring for why.
        providers = [p for p in snapshot.resolution.providers_for(target) if p.is_default]
        return ([target] if providers else []), providers

    from . import deadline

    if index is None:
        index = _build_demangled_index(snapshot)

    matched: list[str] = []
    provider_set: set[str] = set()
    # Checkpointed every _DEADLINE_CHECK_INTERVAL entries (Codex review,
    # PR H, second round): a single pattern/template target scanning a
    # ~50k-entry index has no other cooperative checkpoint of its own --
    # _match_entry's own per-target check only bounds the time *between*
    # targets, not a single large scan.
    for i, (demangled, lib_name) in enumerate(index):
        if i % _DEADLINE_CHECK_INTERVAL == 0:
            deadline.check()
        if lib_name in provider_set:
            # We already recorded this library as a provider — one
            # match per library is enough; skip the rest of its exports.
            # (Avoids quadratic work when a library exports thousands
            # of symbols matching a coarse pattern.)
            continue
        hit = False
        if kind == "pattern":
            hit = fnmatch.fnmatchcase(demangled, target)
        else:  # template
            hit = target in demangled
        if hit:
            matched.append(demangled)
            provider_set.add(lib_name)
    providers = [
        ProviderEntry(library=name, version="") for name in sorted(provider_set)
    ]
    return matched, providers


# Backward-compatibility alias for the original name — some tests and
# external integrations imported _match_target directly. The new code
# path is :func:`_match_target_against_index`.
_match_target = _match_target_against_index


def _match_entry(
    entry: ManifestEntry,
    snapshot: BundleSnapshot,
    index: list[tuple[str, str]] | None = None,
) -> list[tuple[str, str, list[str], list[ProviderEntry]]]:
    """Return per-target match results for *entry*.

    ``[(target_display_name, kind, matched_demangled, providers), ...]``

    For ``symbol`` and ``pattern`` entries the list has one element.
    For ``template`` entries it has one element per instantiation, so
    a partially-satisfied template fires one ``MANIFEST_INSTANTIATION_REMOVED``
    per missing instantiation rather than silently passing because some
    sibling instantiation happened to match.

    When the caller has many manifest entries to evaluate against the
    same snapshot, build a shared index once via
    :func:`_build_demangled_index` and pass it in to amortise the
    O(symbols) demangle pass across all targets.
    """
    from . import deadline

    needs_index = any(kind != "symbol" for _, kind in _entry_targets(entry))
    if index is None and needs_index:
        index = _build_demangled_index(snapshot)
    out: list[tuple[str, str, list[str], list[ProviderEntry]]] = []
    for target, kind in _entry_targets(entry):
        # Cooperative checkpoint (Codex review, PR H): a large pattern/
        # template manifest can spend arbitrarily long here scanning the
        # full demangled index per target -- deadline_scope() alone
        # doesn't interrupt pure Python work, so without this a small
        # --budget could be exceeded well before run_scan_set's own
        # elapsed-time check (after audit_bundle returns) ever sees it.
        deadline.check()
        matched, providers = _match_target_against_index(target, kind, snapshot, index)
        out.append((target, kind, matched, providers))
    return out


def _manifest_ownership_findings(
    snapshot: BundleSnapshot,
    manifest: InstantiationManifest,
    *,
    kind: ChangeKind,
    scope_desc: str,
    index: list[tuple[str, str]] | None = None,
) -> list[BundleFinding]:
    """Check whether *manifest*'s ownership promises hold against *snapshot*.

    The one-sided "does this contract hold right now" half shared by
    :func:`_detect_manifest_drift` (``compare --manifest``, two-sided: this
    is its "missing in new"/"wrong provider" pass) and
    :func:`abicheck.bundle_detectors._detect_manifest_ownership`
    (``scan --artifact-set --manifest``, audit-mode: no old side, so this
    *is* the whole check). *kind* and *scope_desc* let each caller emit its
    own :class:`~abicheck.checker_policy.ChangeKind` and wording
    (``"the new bundle"`` vs. ``"this artifact set"``) over the identical
    matching logic, so the two callers cannot silently diverge on what
    "wrong provider" means.

    Decomposes template entries into one virtual target per instantiation
    so each instantiation is checked independently. For each target:
      - If no exported symbol matches → *kind*.
      - If matched but at the wrong provider (when ``optional_provider=False``)
        → *kind* (contract names the expected library).

    Symbols in *snapshot* but not in the manifest are not flagged here
    (out-of-manifest exports are not necessarily promised).
    """
    findings: list[BundleFinding] = []
    if index is None:
        index = _build_demangled_index(snapshot)

    for entry in manifest.entries:
        for target, kind_word, matched, providers in _match_entry(
            entry, snapshot, index
        ):
            if not matched:
                findings.append(
                    BundleFinding(
                        kind=kind,
                        symbol=target,
                        description=(
                            f"Manifest promises {kind_word} {target!r} but no "
                            f"exported symbol in {scope_desc} matches it."
                        ),
                        provider_library=entry.library,
                    ),
                )
                continue
            if not entry.optional_provider and entry.library is not None:

                def _matches(
                    prov: ProviderEntry, _entry: ManifestEntry = entry
                ) -> bool:
                    if prov.library == _entry.library:
                        return True
                    meta = snapshot.metadata.get(prov.library)
                    return meta is not None and meta.soname == _entry.library

                if not any(_matches(p) for p in providers):
                    got = ", ".join(sorted(p.library for p in providers))
                    findings.append(
                        BundleFinding(
                            kind=kind,
                            symbol=target,
                            description=(
                                f"Manifest requires {kind_word} {target!r} to "
                                f"be provided by {entry.library}, but "
                                f"{scope_desc} provides it via {got} instead."
                            ),
                            provider_library=entry.library,
                            new_value=got,
                        ),
                    )
    return findings


def _detect_manifest_drift(
    old: BundleSnapshot,
    new: BundleSnapshot,
    manifest: InstantiationManifest,
) -> list[BundleFinding]:
    """Enforce a release manifest against the new bundle.

    Per-snapshot demangle indexes are built once and reused across every
    manifest entry — manifest enforcement scales O(symbols + Σtargets)
    rather than O(symbols × Σtargets). The "missing in new"/"wrong
    provider" half is :func:`_manifest_ownership_findings`; this function
    adds the two-sided "newly promised" pass that only makes sense with an
    old side to diff against.
    """
    # Build the per-snapshot demangle indexes once; both the
    # "missing in new" and "newly promised" passes reuse them.
    new_index = _build_demangled_index(new)
    old_index = _build_demangled_index(old)

    findings = _manifest_ownership_findings(
        new,
        manifest,
        kind=ChangeKind.BUNDLE_MANIFEST_INSTANTIATION_REMOVED,
        scope_desc="the new bundle",
        index=new_index,
    )

    # Newly-promised targets — matched in new bundle but not in old.
    for entry in manifest.entries:
        new_targets = _match_entry(entry, new, new_index)
        old_targets = {t: m for t, _, m, _ in _match_entry(entry, old, old_index)}
        for target, kind, matched_new, _ in new_targets:
            if not matched_new:
                continue
            if old_targets.get(target):
                continue
            findings.append(
                BundleFinding(
                    kind=ChangeKind.BUNDLE_MANIFEST_INSTANTIATION_ADDED,
                    symbol=target,
                    description=(
                        f"Manifest now promises {kind} {target!r}; "
                        f"not exported by the old bundle. New public surface."
                    ),
                    provider_library=entry.library,
                ),
            )

    return findings


# ---------------------------------------------------------------------------
# Internal heuristics
# ---------------------------------------------------------------------------

# Common system-provided symbols imported by almost every C/C++ DSO.
# Avoids false-positive bundle findings for libc/libstdc++ symbols.
DEFAULT_SYSTEM_SYMBOLS: frozenset[str] = frozenset(
    {
        "__libc_start_main",
        "__cxa_atexit",
        "__cxa_finalize",
        "__cxa_throw",
        "__gxx_personality_v0",
        "__stack_chk_fail",
        "__stack_chk_guard",
        "__tls_get_addr",
        "__errno_location",
        "_ITM_registerTMCloneTable",
        "_ITM_deregisterTMCloneTable",
        "abort",
        "exit",
        "malloc",
        "free",
        "calloc",
        "realloc",
        "memcpy",
        "memmove",
        "memset",
        "memcmp",
        "strlen",
        "strcmp",
        "strncmp",
        "strcpy",
        "strncpy",
        "strdup",
        "fprintf",
        "printf",
        "puts",
        "pthread_once",
        "pthread_self",
        "pthread_create",
        "pthread_join",
    }
)


def _looks_system(soname: str) -> bool:
    """Heuristic: looks like a system-provided library by name."""
    return (
        soname.startswith("libc.so")
        or soname.startswith("libm.so")
        or soname.startswith("libdl.so")
        or soname.startswith("libpthread.so")
        or soname.startswith("librt.so")
        or soname.startswith("libstdc++.so")
        or soname.startswith("libc++.so")
        or soname.startswith("libgcc")
        or soname.startswith("ld-linux")
    )


def _looks_system_symbol(name: str) -> bool:
    """Heuristic: imported symbol that is almost certainly system-provided."""
    if name.startswith("_ZNSt") or name.startswith("_ZSt"):
        return True  # std:: mangled
    if name.startswith("_ZNK") and "St" in name[:8]:
        return True
    return False


# Symbol-version namespaces owned by the C/C++ runtime and toolchain. A symbol
# imported with one of these required versions is satisfied by libc /
# libstdc++ / libgcc / libgomp — never by a sibling inside the analysed
# bundle — so it must not be reported as a dropped intra-bundle dependency.
# This is the version-evidence half of the field-derived oneDAL fix
# (``syscall@GLIBC_*``, ``stdout@GLIBC_*``, ``_ZdlPvm@CXXABI_*``).
_SYSTEM_VERSION_PREFIXES: tuple[str, ...] = (
    "GLIBC_",
    "GLIBCXX_",
    "CXXABI_",
    "GCC_",
    "LIBGCC_",
    "LIBC_",
    "GOMP_",
    "OMP_",
    "GFORTRAN_",
    "GLIBCABI_",
)


def _looks_system_version(version: str) -> bool:
    """True when a required symbol version is a C/C++ runtime/toolchain namespace."""
    return any(version.startswith(prefix) for prefix in _SYSTEM_VERSION_PREFIXES)


def _import_is_external(
    consumer: ConsumerEntry,
    consumer_meta: ElfMetadata,
    snapshot: BundleSnapshot,
) -> bool:
    """Classify an import as external using version + provider evidence.

    An import is external (never a *dropped intra-bundle* dependency) when
    its required symbol version is satisfied by a library outside the bundle.

    The precise signal is the **per-symbol verneed provider**
    (``ConsumerEntry.version_soname``): GNU version labels are scoped per
    verneed provider, not globally unique, so two providers can both advertise
    e.g. ``FOO_1.0``. When that soname is known we resolve this exact import —
    external iff its provider soname does not resolve inside the bundle —
    without guessing from the bare label.

    ``is_intra_bundle_provider`` matches by exact soname *and* filename stem, so
    a SONAME-major bump (``libcore.so.1`` → ``libcore.so.2``) where a sibling
    still NEEDs the old soname is still recognised as intra-bundle.

    When the per-symbol verneed soname is unavailable, fall back to the
    label-level scan over ``versions_required`` — provider evidence still wins
    over the ``_looks_system_version`` toolchain-namespace heuristic.

    Unversioned imports (``version == ""``) return ``False`` so an unversioned
    internal sibling import still produces a finding: classify by
    provider/version evidence, not only by symbol-name allow-lists.
    """
    version = consumer.version
    if not version:
        return False

    def _resolves_intra(soname: str) -> bool:
        # Two independent, complementary signals, either sufficient:
        # resolution.soname_to_name is the exact map
        # _reachable_intra_libraries()/the version_soname pinning above use
        # (knows a member's real on-disk filename when retained through a
        # differently named symlink alias with no DT_SONAME -- Codex
        # review); is_intra_bundle_provider() additionally does filename-
        # stem matching (a SONAME-major bump, e.g. libcore.so.1 ->
        # libcore.so.2, where a sibling's DT_NEEDED still cites the old
        # soname -- test_versioned_import_after_soname_bump_still_fires),
        # which the exact map alone does not cover since it only indexes a
        # library's *current* soname/canonical key/real filename, never a
        # soname it used to advertise.
        return (
            soname in snapshot.resolution.soname_to_name
            or snapshot.is_intra_bundle_provider(soname)
        )

    # Preferred path: the exact verneed provider for *this* symbol is known.
    # No label-collision ambiguity — resolve this import directly.
    if consumer.version_soname:
        return not _resolves_intra(consumer.version_soname)
    # Fallback: per-symbol verneed soname not captured. Scan the label across
    # all verneed providers. If any soname carrying this label resolves inside
    # the bundle, treat the import as intra (keep the finding); only when every
    # provider of the label is external is it external. Provider evidence wins
    # over the system-namespace shortcut so a vendored runtime stays visible.
    external_match = False
    for soname, versions in consumer_meta.versions_required.items():
        if version not in versions:
            continue
        if _resolves_intra(soname):
            return False  # required from a bundle sibling — keep the finding
        external_match = True
    if external_match:
        return True
    return _looks_system_version(version)


_ELF_MAGIC = b"\x7fELF"


def _path_looks_like_elf(path: Path) -> bool:
    """Cheap ELF-magic sniff. Avoids spurious warnings from
    :func:`parse_elf_metadata` on JSON snapshot inputs and other non-ELF
    artefacts present in a release directory."""
    try:
        with open(path, "rb") as f:
            return f.read(4) == _ELF_MAGIC
    except OSError:
        return False


def _strip_namespace_prefix(name: str) -> str:
    """Return the unqualified component of a possibly C++-qualified name.

    Used by :func:`_detect_intra_type_changed` to find type references
    inside mangled symbols even when the diff reports the type by its
    fully-qualified name.
    """
    if "::" in name:
        return name.rsplit("::", 1)[-1]
    return name
