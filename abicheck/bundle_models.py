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

"""Data model for bundle-aware multi-library ABI analysis (ADR-023).

Holds the dataclasses that describe a release viewed as a bundle of
libraries: the resolution graph, the per-release snapshot, and the
finding/result types produced by :func:`abicheck.bundle.compare_bundle`.

This is a leaf module: it imports nothing from :mod:`abicheck.bundle`. The
types here are re-exported from :mod:`abicheck.bundle` so the historical
``from abicheck.bundle import BundleSnapshot`` import paths keep working.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .checker_policy import ChangeKind, Verdict, compute_verdict, effective_category
from .checker_types import Change, DiffResult
from .contract_gating import is_evaluated
from .model import AbiSnapshot, Function, Variable
from .model.elf_facts import ElfMetadata, SymbolBinding
from .model.scope_acquisition import ScopeAcquisitionRecord

if TYPE_CHECKING:
    from .policy_file import PolicyFile

# Symbols imported by virtually every C/C++ shared library that are
# provided by the system loader, not by the bundle. Resolution against the
# bundle is meaningless for these; ignore unresolved imports against this
# set when emitting :class:`ChangeKind.BUNDLE_INTRA_DEP_REMOVED`.
DEFAULT_SYSTEM_PROVIDERS: frozenset[str] = frozenset(
    {
        "libc.so.6",
        "libc.so.7",
        "libm.so.6",
        "libdl.so.2",
        "libpthread.so.0",
        "librt.so.1",
        "libstdc++.so.6",
        "libc++.so.1",
        "libc++abi.so.1",
        "libgcc_s.so.1",
        "libgomp.so.1",
        "libtbb.so.12",
        "libtbb.so.2",
        "libsycl.so",
        "libsycl.so.7",
        "libsycl.so.8",
        "libOpenCL.so.1",
        "libz.so.1",
        "ld-linux-x86-64.so.2",
        "ld-linux-aarch64.so.1",
        # oneTBB's own malloc/proxy shared libs -- distinct sonames from
        # libtbb.so above, and commonly DT_NEEDED alongside it by a library
        # that opts into TBB's scalable allocator (e.g. oneDAL). Given
        # without an explicit major (`soname_matches_providers`'s stem-match
        # rule -- see that function's own docstring) so any TBB major
        # matches, matching how libtbb.so itself needs two explicit-major
        # entries above only because it changed its own soname convention
        # between the 2020.x and 2021.x/oneAPI release lines.
        "libtbbmalloc",
        "libtbbmalloc_proxy",
        # Intel oneMKL's own runtime dispatch/threading-layer/compute-kernel
        # libraries -- real, common DT_NEEDED edges for any library built
        # against oneMKL (e.g. oneDAL). Not exhaustive over oneMKL's full
        # kernel-library surface (dozens of per-ISA/per-precision libs), but
        # covers the libraries an ordinary dynamic link against oneMKL pulls
        # in regardless of which specific kernels are used. Given without an
        # explicit major so any oneMKL release's own soname major matches.
        "libmkl_core",
        "libmkl_rt",
        "libmkl_intel_lp64",
        "libmkl_intel_ilp64",
        "libmkl_intel_thread",
        "libmkl_gnu_thread",
        "libmkl_tbb_thread",
        "libmkl_sequential",
        "libmkl_def",
        "libmkl_avx2",
        "libmkl_avx512",
        "libmkl_vml_avx2",
        "libmkl_vml_avx512",
        "libmkl_vml_def",
        "libmkl_sycl",
        # The Intel compiler/OpenMP runtime libraries a library built with
        # icc/icx/icpx/dpcpp commonly links against, distinct from the
        # GCC/LLVM runtime libraries already listed above.
        "libiomp5",
        "libimf",
        "libirng",
        "libsvml",
        "libintlc",
        # The oneAPI Level Zero loader -- the runtime a SYCL library
        # dispatches to for a Level Zero (as opposed to OpenCL) backend,
        # alongside libOpenCL.so.1 already listed above.
        "libze_loader",
    }
)

#: G38 stabilization (post-Phase-4, revised after a Codex review round on
#: PR #845): "confirmed, so don't claim total ignorance about this symbol"
#: (suppression) and "confirmed severely enough to promote a per-library
#: change to a consumer-attributed BREAKING bundle finding" (promotion) are
#: two different bars, not one. An earlier revision of this constant tried
#: to serve both with a single 12-kind set shared by `bundle.py`'s
#: promotion path and `bundle_signature_evidence.py`'s suppression path —
#: which was wrong in a concrete, checkable way: `FUNC_NOEXCEPT_ADDED`'s
#: own registry entry (`change_registry.py`) has `default_verdict=
#: COMPATIBLE`, and `FUNC_NOEXCEPT_REMOVED`/`FUNC_EXCEPTION_SPEC_CHANGED`
#: are `COMPATIBLE_WITH_RISK` with an explicit "not a binary break"
#: rationale — promoting either to a BREAKING
#: `BUNDLE_INTRA_DEP_SIGNATURE_CHANGED` would have fabricated a
#: release-blocking cross-library break out of a change the tool's own
#: policy layer says is not one. `FUNC_VIRTUAL_ADDED`/`FUNC_VIRTUAL_REMOVED`
#: are genuinely `BREAKING`, but about vtable-slot layout, not a direct
#: mismatched call boundary — a different failure mode than what
#: `BUNDLE_INTRA_DEP_SIGNATURE_CHANGED`'s own description ("Calling
#: convention is now mismatched") claims. This module therefore keeps two
#: sets, one a strict subset of the other (see the test asserting that
#: relationship in `tests/test_bundle.py`):
#:
#: - :data:`CONFIRMED_C_BOUNDARY_SIGNATURE_BREAK_KINDS` (broad) — used only
#:   by :mod:`abicheck.bundle_signature_evidence` for suppression: any one
#:   of these being confirmed means *something* concrete is known about the
#:   symbol, which is enough to withhold the "couldn't tell either way"
#:   `BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED` finding, without that
#:   confirmed fact itself needing to be a proven binary break.
#: - :data:`PROMOTABLE_C_BOUNDARY_SIGNATURE_BREAK_KINDS` (narrow) — used
#:   only by :func:`abicheck.bundle._detect_intra_dep_signature_changed`
#:   for promotion: a confirmed change of one of *these* kinds on a
#:   provider's own export is promoted to a consumer-attributed
#:   `BUNDLE_INTRA_DEP_SIGNATURE_CHANGED` finding. Every member is both
#:   `default_verdict=BREAKING` on its own and describes a genuine,
#:   direct call-boundary mismatch (parameter/return/variable type,
#:   variadicness, calling convention) rather than a vtable-layout or
#:   policy-adjustable source-level concern.
#:
#: Before the broad set existed, `bundle.py` promoted only three kinds
#: (`FUNC_PARAMS_CHANGED`/`FUNC_RETURN_CHANGED`/`VAR_TYPE_CHANGED`) while
#: `bundle_signature_evidence.py` independently suppressed on twelve — a
#: confirmed `CALLING_CONVENTION_CHANGED` correctly suppressed the
#: "unverified" finding but was never promoted, losing cross-library
#: causality. The narrow set closes exactly that gap (adding
#: `FUNC_VARIADIC_ADDED`/`FUNC_VARIADIC_REMOVED`/`CALLING_CONVENTION_
#: CHANGED` to the original three) without also promoting the nine kinds
#: that are correctly suppression-only evidence.
#:
#: Living here (not in either consumer module) matches this module's own
#: leaf-module contract: `bundle.py` imports it directly, and
#: `bundle_signature_evidence.py` — which must not import `bundle.py` (see
#: that module's own docstring on why it stays leaf-only) — already
#: imports this module for `BundleFinding`/`BundleSnapshot`/
#: `ConsumerEntry`/`ProviderEntry`, so this adds no new import edge either
#: direction.
#:
#: `CONFIRMED_C_BOUNDARY_SIGNATURE_BREAK_KINDS` deliberately excludes
#: `CTOR_EXPLICIT_ADDED`/`CTOR_EXPLICIT_REMOVED`: an `explicit` specifier
#: transition is a purely source-level fact that never alters the mangled
#: name (see `checker_policy.py`'s own `ChangeKind` comment for these two
#: kinds) and therefore proves nothing about whether the binary calling
#: signature — params, return type, variadicness, calling convention, ...
#: — this set exists to police still agrees. Also excludes
#: `FUNC_LANGUAGE_LINKAGE_CHANGED` (an `extern "C"` transition changes the
#: mangled name itself, so old/new can't share the symbol key either
#: consumer matches on) and the vtable-slot/inline-transition kinds (about
#: virtual-dispatch layout and definition placement, not calling-signature
#: agreement).
CONFIRMED_C_BOUNDARY_SIGNATURE_BREAK_KINDS: frozenset[ChangeKind] = frozenset(
    {
        ChangeKind.FUNC_PARAMS_CHANGED,
        ChangeKind.FUNC_RETURN_CHANGED,
        ChangeKind.VAR_TYPE_CHANGED,
        ChangeKind.FUNC_VARIADIC_ADDED,
        ChangeKind.FUNC_VARIADIC_REMOVED,
        ChangeKind.CALLING_CONVENTION_CHANGED,
        ChangeKind.FUNC_NOEXCEPT_ADDED,
        ChangeKind.FUNC_NOEXCEPT_REMOVED,
        ChangeKind.FUNC_EXCEPTION_SPEC_CHANGED,
        ChangeKind.FUNC_REF_QUAL_CHANGED,
        ChangeKind.FUNC_VIRTUAL_ADDED,
        ChangeKind.FUNC_VIRTUAL_REMOVED,
    }
)

#: The narrow, promotion-only subset of the set above — see that constant's
#: own docstring for the full reasoning. Every member has
#: `default_verdict=BREAKING` in `change_registry.py` *and* describes a
#: direct call-boundary mismatch, not a vtable-layout or policy-adjustable
#: source-level concern. `FUNC_NOEXCEPT_ADDED`/`FUNC_NOEXCEPT_REMOVED`/
#: `FUNC_EXCEPTION_SPEC_CHANGED` (COMPATIBLE/RISK, explicitly "not a binary
#: break" per their own registry comments) and `FUNC_VIRTUAL_ADDED`/
#: `FUNC_VIRTUAL_REMOVED`/`FUNC_REF_QUAL_CHANGED` (BREAKING, but about
#: vtable-slot layout or overload-resolution mangling rather than a plain
#: mismatched calling boundary on an otherwise-identical symbol) are
#: deliberately excluded — promoting any of them would fabricate a
#: release-blocking cross-library finding from a change that is not one,
#: or not one of *this* shape.
PROMOTABLE_C_BOUNDARY_SIGNATURE_BREAK_KINDS: frozenset[ChangeKind] = frozenset(
    {
        ChangeKind.FUNC_PARAMS_CHANGED,
        ChangeKind.FUNC_RETURN_CHANGED,
        ChangeKind.VAR_TYPE_CHANGED,
        ChangeKind.FUNC_VARIADIC_ADDED,
        ChangeKind.FUNC_VARIADIC_REMOVED,
        ChangeKind.CALLING_CONVENTION_CHANGED,
    }
)

assert PROMOTABLE_C_BOUNDARY_SIGNATURE_BREAK_KINDS.issubset(
    CONFIRMED_C_BOUNDARY_SIGNATURE_BREAK_KINDS
), "every promotable kind must also count as confirmed evidence for suppression"


@dataclass(frozen=True)
class ProviderEntry:
    """One library in the bundle that exports ``symbol``."""

    library: str  # e.g. "libcore.so"
    version: str  # gnu.version_d tag, "" if unversioned
    # True for an unversioned export, or a versioned "@@default" definition
    # (ElfSymbol.is_default); False for a "@specific" (non-default) versioned
    # definition. The dynamic linker can only satisfy an *unversioned*
    # symbol reference against a default definition -- a provider whose
    # only definition of this symbol is non-default-only cannot satisfy an
    # unversioned import, even though the symbol name itself is "reachable"
    # (Codex review). Defaults to True for the few synthetic ProviderEntry
    # construction sites that have no real per-symbol version data.
    is_default: bool = True
    # ELF symbol binding (STB_GLOBAL/STB_WEAK/STB_GNU_UNIQUE/...). A WEAK or
    # UNIQUE definition is ordinary C++ vague linkage (inline function/
    # template instantiation COMDAT) -- every DSO that emits the same
    # definition is *expected* to carry an identical weak copy, deduplicated
    # by the dynamic linker at load time; it is not two libraries disputing
    # ownership of a symbol the way two GLOBAL definitions are. Defaults to
    # GLOBAL for synthetic construction sites with no real per-symbol
    # binding data (Codex review, PR H).
    binding: SymbolBinding = SymbolBinding.GLOBAL


@dataclass(frozen=True)
class ConsumerEntry:
    """One library in the bundle that imports ``symbol``."""

    library: str  # e.g. "libalgo.so"
    version: str  # gnu.version_r required version, "" if unversioned
    weak: bool  # True when the import is weak (unresolved is OK)
    # Verneed provider soname for this symbol's required version ("" if unknown
    # or unversioned). Disambiguates colliding version labels across providers.
    version_soname: str = ""


@dataclass
class ResolutionGraph:
    """Bundle-level symbol resolution graph.

    The bundle layer answers questions like "which library in this release
    provides core_add?" and "which siblings import a symbol that no sibling
    exports?" by indexing the metadata of every library found in the
    release directory.
    """

    # symbol -> [providers]; one entry per defining library
    provides: dict[str, list[ProviderEntry]] = field(default_factory=dict)
    # symbol -> [consumers]; one entry per importing library
    consumers: dict[str, list[ConsumerEntry]] = field(default_factory=dict)
    # Per-library DT_NEEDED edges as bundle-relative library names.
    # library -> list of NEEDED sonames (only those that resolve inside the bundle).
    intra_needed: dict[str, list[str]] = field(default_factory=dict)
    # library -> DT_NEEDED sonames that did NOT resolve inside the bundle
    # (likely system libs — see DEFAULT_SYSTEM_PROVIDERS).
    extra_needed: dict[str, list[str]] = field(default_factory=dict)
    # The exact reverse map _compute_resolution_graph() used to classify
    # each DT_NEEDED edge as intra vs. extra (soname / canonical key / real
    # on-disk filename -> library name). Stored so a later BFS resolving
    # those same edges (_reachable_intra_libraries) uses the identical
    # mapping instead of independently re-deriving one -- the two used to
    # disagree for a versioned, no-DT_SONAME library discovered via a
    # differently-named symlink alias, where the classifying map's
    # resolved-real-filename entry had no equivalent in
    # provider_library_for_soname()'s own name/soname/stem heuristic
    # (Codex review).
    soname_to_name: dict[str, str] = field(default_factory=dict)

    def providers_for(self, symbol: str) -> list[ProviderEntry]:
        return list(self.provides.get(symbol, ()))

    def consumers_of(self, symbol: str) -> list[ConsumerEntry]:
        return list(self.consumers.get(symbol, ()))


@dataclass
class BundleSnapshot:
    """A release directory captured as a bundle.

    Holds per-library ELF metadata and the precomputed resolution graph.
    """

    root: Path  # the release directory
    libraries: dict[str, Path]  # library_name -> filesystem path
    metadata: dict[str, ElfMetadata]  # library_name -> parsed ELF metadata
    resolution: ResolutionGraph
    # Whether `.libraries`' paths name real, live files worth resolving
    # against the filesystem (symlink targets, CWD-relative lookups), or are
    # synthetic identity-only paths reconstructed from stored facts (G38
    # Phase 2's `bundle_facts.bundle_snapshot_from_facts()`). Mirrors
    # `build_bundle_snapshot_from_metadata`'s own `probe_filesystem` --
    # `bundle._detect_soname_skew()`'s SONAME-major fallback reads this to
    # decide whether re-resolving a path is safe: for a synthetic path (a
    # bare `Path("libfoo.so.1")` with no real file behind it),
    # `Path.resolve()` still succeeds by walking the *current working
    # directory* -- so if the CWD happens to hold an unrelated real file or
    # symlink with that same name, resolution would silently substitute its
    # target for the persisted, already-resolved basename (Codex review,
    # fresh evidence). Defaults `True` (safe for every pre-existing direct
    # construction of this dataclass -- real filesystem paths, mostly in
    # tests -- since the default preserves the prior always-resolve
    # behavior).
    filesystem_backed: bool = True
    # Per-member override of `filesystem_backed` above, when a snapshot
    # mixes live and non-resolvable members (`bundle.build_bundle_snapshot_
    # mixed`'s own stored/live mix -- CodeRabbit review, fresh evidence: the
    # single snapshot-wide flag forced every live member to also be treated
    # as non-resolvable whenever at least one stored member participated,
    # so a live symlink with no DT_SONAME lost its resolved target name in
    # `bundle._detect_soname_skew`'s own cohort-major fallback). `None`
    # (the default) means every member follows `filesystem_backed` above,
    # unchanged for every pre-existing caller; when given, a member's own
    # membership in this set decides, not the snapshot-wide flag.
    filesystem_backed_names: frozenset[str] | None = None

    def member_is_filesystem_backed(self, name: str) -> bool:
        """Whether *name*'s own `.libraries[name]` path is safe to
        re-resolve against the real filesystem -- `filesystem_backed_names`
        when given, else the snapshot-wide `filesystem_backed` flag."""
        if self.filesystem_backed_names is not None:
            return name in self.filesystem_backed_names
        return self.filesystem_backed

    @property
    def library_names(self) -> list[str]:
        return sorted(self.libraries.keys())

    def is_intra_bundle_provider(self, soname: str) -> bool:
        """Return True if ``soname`` matches a library inside this bundle.

        Matches on either the raw filename (``libfoo.so``) or the soname
        encoded by the library (``libfoo.so.1``).
        """
        return self.provider_library_for_soname(soname) is not None

    def provider_library_for_soname(self, soname: str) -> str | None:
        """Resolve ``soname`` to the bundle library name it identifies, if any.

        Same matching rules as :meth:`is_intra_bundle_provider` (raw filename
        or encoded soname, with filename-stem fallback in either direction),
        but returns the matched library name instead of a bool — used by
        ADR-056's audit-mode detector to resolve a consumer's precise
        ``version_soname`` to the specific provider it must come from,
        rather than merely confirming *some* provider resolves it.
        """
        if soname in self.libraries:
            return soname
        for name, meta in self.metadata.items():
            if meta.soname == soname:
                return name
            # Allow filename-stem fallback (libfoo.so matches libfoo.so.1)
            if soname.startswith(name + "."):
                return name
            if name.startswith(soname + "."):
                return name
        return None


def basename_to_bundle_key(snapshot: BundleSnapshot) -> dict[str, str]:
    """Map each library's real on-disk file basename to its bundle-canonical
    key (``snapshot.libraries``' own keys -- the same version-stripped key
    :func:`~abicheck.binary_utils._canonical_library_key` produces, e.g.
    ``libfoo.so`` for a real ``libfoo.so.1.2.3``).

    G38 stabilization (Codex/CodeRabbit review on PR #845, fresh evidence,
    with a concrete repro traced through the wired ``compare --release``
    path): :class:`~abicheck.checker_types.DiffResult.library` is always set
    from ``path.name`` (`abicheck/service.py`/`abicheck/dumper.py`, every
    ELF/PE/Mach-O dump site) -- the literal on-disk filename, not the
    bundle's canonical key -- so for any normally-versioned SONAME the two
    differ. `bundle_signature_evidence.py`'s own `_confirmed_provider_
    symbols` needed this exact fix once already (see that module's git
    history); `bundle.py`'s `diff_by_library` construction had the
    identical bug independently, confirmed via `cli_compare_release.py`'s
    own `_bundle_key`/`DiffResult.library` split: the release fan-out
    stores the canonical key separately and leaves `DiffResult.library` as
    the real, possibly-versioned filename, so `compare_bundle()`'s
    `_detect_intra_dep_signature_changed`/`_detect_intra_type_changed`/
    `_detect_provider_changed` could attribute a promoted finding to
    ``provider_library="libcore.so.1.2.3"`` while `BundleSnapshot.
    resolution` keys the same provider as ``"libcore.so"`` -- silently
    breaking the ``consumer.library != provider_lib``/lookup comparisons
    those detectors depend on. Living here (not duplicated in each
    consumer module) is exactly what this module's own leaf-module
    contract exists for -- see :data:`CONFIRMED_C_BOUNDARY_SIGNATURE_
    BREAK_KINDS`'s docstring for the identical reasoning applied to a
    shared constant instead of a shared function.

    A basename with no matching bundle entry is left unmapped -- the
    caller degrades to comparing the raw basename, rather than raising.
    """
    return {path.name: key for key, path in snapshot.libraries.items()}


def consumer_resolves_via_provider(
    new: BundleSnapshot,
    consumer: ConsumerEntry,
    provider_lib: str,
    symbol: str,
    reachable: set[str],
) -> bool:
    """Whether *consumer*'s import of *symbol* actually resolves against
    *provider_lib* specifically, not merely "some library in the bundle
    happens to export a same-named symbol."

    Mirrors ``bundle._detect_unresolved_intra_dependency``'s version-aware,
    reachability-constrained provider matching (see that function's own
    docstring for the full contract, including the unversioned
    ``is_default`` subtlety) -- narrowed here to check one specific
    candidate provider rather than "does this consumer resolve *anywhere*."
    Two same-named exports across unrelated bundle siblings is a real, if
    unusual, shape (CodeRabbit review, G38 stabilization): without this
    check, a promoted signature-change finding could attribute a consumer
    to a provider it never actually links against. Lives here rather than
    in ``bundle.py`` purely to keep that module under its AI-readiness
    file-size cap -- no other reason for the split.
    """
    if provider_lib not in reachable:
        return False
    all_entries = new.resolution.providers_for(symbol)
    # A single provider library can legitimately export several *versioned*
    # definitions of the same bare symbol name (e.g. compat-symbol pattern
    # `foo@V1` alongside `foo@@V2`) -- check every one of this provider's
    # own entries (`any`, mirroring `_detect_unresolved_intra_dependency`'s
    # own matching), not just the first one found (Codex review): picking
    # only the first could test a non-matching V1 entry while the real
    # match is a later V2 one from the same provider.
    candidates = [p for p in all_entries if p.library == provider_lib]
    if not candidates:
        return False

    if consumer.version and consumer.version_soname:
        # The per-symbol verneed provider pins resolution to one specific
        # library by construction (GNU symbol versioning) -- no
        # interposition ambiguity applies to this branch.
        target_lib = new.resolution.soname_to_name.get(consumer.version_soname)
        if target_lib != provider_lib:
            return False
        return any(c.version == consumer.version for c in candidates)

    # No per-symbol version pin (an unversioned reference, or a versioned
    # one whose verneed soname wasn't captured): two reachable bundle
    # siblings can each independently define a matching entry for this
    # bare symbol, and this model has no notion of real ELF symbol-search
    # order (DT_NEEDED / global-scope precedence) to say which one a
    # consumer's unversioned reference actually binds to (Codex review,
    # G38 stabilization -- fresh evidence beyond the reachability fix
    # above). Attributing to any one of several equally-plausible
    # providers would be a guess, so decline (ambiguous) rather than
    # fabricate an attribution -- a missed promotion is the safe
    # direction here, not a false one.
    def _matches(p: ProviderEntry) -> bool:
        if p.library not in reachable:
            return False
        if not consumer.version:
            return p.is_default
        return p.version == consumer.version

    matching_libs = {p.library for p in all_entries if _matches(p)}
    return matching_libs == {provider_lib}


def diff_change_is_breaking(
    diff: DiffResult,
    change: Change,
    policy_sets: tuple[
        frozenset[ChangeKind],
        frozenset[ChangeKind],
        frozenset[ChangeKind],
        frozenset[ChangeKind],
    ],
) -> bool:
    """Whether *change* classifies as ``BREAKING`` under the policy that
    actually scored *diff* (Codex review, G38 stabilization).

    ``policy_kind_sets(policy)`` alone only knows a *named* base policy
    (``strict_abi``/``plugin_abi``/...) -- a ``--policy-file`` demotion is
    resolved through a completely separate path
    (``PolicyFile.compute_verdict``) that does **not** populate
    ``Change.effective_verdict``, so ``checker_policy.effective_category``
    alone cannot see it. When the originating diff carries a resolved
    ``PolicyFile``, defer to its own per-change classification instead --
    the same one that already scored the per-library finding -- so a
    policy-file override can't be defeated by promotion the way the
    named-policy override already couldn't be.

    Also checks ADR-049 contract-relevance status first (Codex review):
    a ``NOT_EVALUATED`` (out-of-contract-scope) finding stays in
    ``diff.changes`` but is excluded from the per-library verdict/exit
    code, so promoting it here would contradict that already-scored
    result -- ``is_evaluated`` defaults ``True`` for an unstamped finding,
    so a run with no ``--contract`` is unaffected.
    """
    if not is_evaluated(change):
        return False
    if diff.policy_file is not None:
        return diff.policy_file.compute_verdict([change]) == Verdict.BREAKING
    return effective_category(change, *policy_sets) == Verdict.BREAKING


@dataclass(frozen=True)
class BundleSignatureEvidence:
    """Compact, per-library stand-in for an :class:`~abicheck.model.
    AbiSnapshot`, carrying only what
    :func:`abicheck.bundle_signature_evidence.find_unverified_signature_
    findings` actually reads from one (G38 stabilization Phase 9, memory
    regression fix).

    Wiring Phase 4 into the live ``compare --release`` path made every
    directory/package comparison retain each library's full old+new
    ``AbiSnapshot`` (functions, types, layouts, source graph, build-source
    evidence -- everything) until the whole release finished and bundle
    analysis ran, even when neither JUnit nor ``--bundle-facts-out`` needed
    it. Confirmed by reading every attribute
    ``find_unverified_signature_findings``'s own helpers touch on a
    snapshot: exactly ``function_map``, ``variable_map``, and
    ``elf_only_mode`` -- never a type, a record, the source graph, or any
    other field. This type exposes only those three, built once right
    after a per-library comparison completes
    (:meth:`from_snapshot`), so the caller can drop its reference to the
    full ``AbiSnapshot`` and let the rest of it (the part that actually
    dominates memory for a template-heavy C++ library) be garbage
    collected instead of retained for the whole release.

    Deliberately duck-type compatible with ``AbiSnapshot`` for this one
    consumer's purposes rather than a narrower ``Protocol`` -- both this
    type and ``AbiSnapshot`` are accepted anywhere
    ``find_unverified_signature_findings`` takes an
    ``old_snapshots``/``new_snapshots`` mapping, so a caller that *does*
    need the full snapshot for another reason (JUnit, ``--bundle-facts-
    out``) can keep passing the real thing with no special-casing on the
    receiving end.
    """

    function_map: Mapping[str, Function]
    variable_map: Mapping[str, Variable]
    elf_only_mode: bool

    @classmethod
    def from_snapshot(cls, snapshot: AbiSnapshot) -> BundleSignatureEvidence:
        """Project *snapshot* down to its bundle-signature-evidence-relevant
        fields. Holds references to the same ``Function``/``Variable``
        objects (no deep copy) -- correct because those are exactly what
        the caller wants to keep alive; everything else *not* referenced
        from here becomes eligible for garbage collection once the caller
        drops its own reference to ``snapshot``.
        """
        return cls(
            function_map=snapshot.function_map,
            variable_map=snapshot.variable_map,
            elf_only_mode=snapshot.elf_only_mode,
        )


@dataclass
class BundleFinding:
    """A single bundle-level finding.

    Mirrors :class:`Change` so the same reporter / suppression / severity
    machinery can consume bundle findings without special-casing. The
    ``consumer_library`` / ``provider_library`` fields distinguish bundle
    findings from per-library changes.
    """

    kind: ChangeKind
    symbol: str  # mangled symbol name or type name
    description: str
    consumer_library: str | None = None  # affected library (for intra-dep findings)
    provider_library: str | None = None  # source-of-change library
    old_value: str | None = None
    new_value: str | None = None
    affected_libraries: list[str] = field(default_factory=list)
    # ADR-027 A3/D3.2 — per-finding reachability modulation, mirroring the A4
    # Change override so a demotion reaches the bundle verdict (which lowers
    # findings to Change and classifies via effective_category). Default None =
    # classify by kind, i.e. today's behaviour.
    effective_verdict: Verdict | None = None
    modulation_reason: str | None = None
    modulation_rule: str | None = None

    def to_change(self) -> Change:
        """Lower a :class:`BundleFinding` into the :class:`Change` representation.

        Used by the JSON/Markdown reporters that already know how to render
        ``Change`` objects. The bundle attribution fields are flattened into
        ``description`` so they survive the lowering. A reachability modulation
        (D3.2) is propagated onto the lowered ``Change`` so the bundle verdict
        and the compare-release exit code honour it.
        """
        prefix = ""
        if self.consumer_library and self.provider_library:
            prefix = f"[{self.consumer_library} ← {self.provider_library}] "
        elif self.provider_library:
            prefix = f"[{self.provider_library}] "
        elif self.consumer_library:
            prefix = f"[{self.consumer_library}] "
        return Change(
            kind=self.kind,
            symbol=self.symbol,
            description=prefix + self.description,
            old_value=self.old_value,
            new_value=self.new_value,
            affected_symbols=list(self.affected_libraries) or None,
            effective_verdict=self.effective_verdict,
            modulation_reason=self.modulation_reason,
            modulation_rule=self.modulation_rule,
        )


@dataclass
class BundleDiffResult:
    """Output of :func:`abicheck.bundle.compare_bundle`.

    Bundle findings are kept distinct from per-library diff results so a
    consumer (reporter, JSON output) can render them under their own
    section. The aggregate ``verdict`` is the worst of (worst per-library
    verdict, ``bundle_verdict``).
    """

    old_root: Path
    new_root: Path
    per_library: list[DiffResult] = field(default_factory=list)
    bundle_findings: list[BundleFinding] = field(default_factory=list)
    #: Policy profile bundle-level findings are scored under (see
    #: :func:`abicheck.checker_policy.compute_verdict`'s own `policy`
    #: parameter). Defaults to ``strict_abi`` — every caller that predates
    #: this field behaves exactly as before.
    policy: str = "strict_abi"
    #: G38 stabilization Phase 11: structured record of a partial-analysis
    #: degradation (``compare_bundle`` itself raising, or
    #: ``find_unverified_signature_findings`` raising) that
    #: `cli_compare_release_helpers._run_bundle_analysis` previously only
    #: ever reported as a stderr warning -- an empty list here is a real,
    #: positive claim that analysis completed cleanly, not merely "nothing
    #: to say"; a report consumer (CI, a downstream tool) can now
    #: distinguish "bundle_findings is empty because nothing broke" from
    #: "bundle_findings is empty because analysis degraded partway
    #: through" by checking this field instead of grepping the run's own
    #: stderr. Deliberately plain strings, not a richer per-error type --
    #: see this phase's own plan-doc entry for what a fuller structured
    #: coverage ledger (mirroring `contract_coverage_ledger.py`) would add
    #: beyond this.
    analysis_errors: list[str] = field(default_factory=list)
    #: Optional :class:`~abicheck.policy_file.PolicyFile`, applied on top of
    #: *policy* when scoring ``bundle_verdict`` -- previously bundle-level
    #: findings were always scored under the bare *policy* name alone, even
    #: when a caller supplied a policy file whose overrides/reclassify rules
    #: it wanted applied everywhere (Codex review, fresh evidence: a
    #: `policy_file` selecting `plugin_abi`-style downgrades for per-library
    #: findings had no effect on `BUNDLE_*` findings). ``None`` (the
    #: default): behavior is unchanged from before this field existed.
    policy_file: PolicyFile | None = None
    #: ADR-065 S2 (declared after the pre-existing ``policy_file`` tail, so a
    #: positional caller of this published result type keeps binding it --
    #: Codex review): the acquisition record a stored-baseline driver
    #: (`bundle_side_input`/`workflows.bundle_stored_pair_compare`) builds
    #: for its members -- a degraded (D8) member is `failed` here, so the
    #: dispatcher's completeness axis gates on it instead of reading a
    #: clean `per_library` list as a fully checked scope. `None` for the
    #: live `compare_bundle` path, whose own fan-out owns its record.
    scope_record: ScopeAcquisitionRecord | None = None
    #: ADR-065 D1 (Codex review): matched members whose NEW artifact failed
    #: extraction *in this run* (a damaged snapshot file, an unreadable
    #: binary), keyed by member with the reason -- the stored-live driver's
    #: counterpart of the native fan-out's per-library ``ERROR`` result.
    #: Distinct from a stored capture's own D8 ``degraded`` marker (a
    #: recorded past failure the scope axis governs): a current-run failure
    #: is an operational error, so a consumer floors the exit on it under
    #: either ``--on-incomplete-scope`` policy. Empty for every other path.
    extraction_failures: dict[str, str] = field(default_factory=dict)

    @property
    def bundle_verdict(self) -> Verdict:
        changes = [f.to_change() for f in self.bundle_findings]
        if self.policy_file is not None:
            return self.policy_file.compute_verdict(changes)
        return compute_verdict(changes, policy=self.policy)

    @property
    def per_library_verdict(self) -> Verdict:
        order = [
            Verdict.NO_CHANGE,
            Verdict.COMPATIBLE,
            Verdict.COMPATIBLE_WITH_RISK,
            Verdict.API_BREAK,
            Verdict.BREAKING,
        ]
        worst = Verdict.NO_CHANGE
        for r in self.per_library:
            if order.index(r.verdict) > order.index(worst):
                worst = r.verdict
        return worst

    @property
    def verdict(self) -> Verdict:
        order = [
            Verdict.NO_CHANGE,
            Verdict.COMPATIBLE,
            Verdict.COMPATIBLE_WITH_RISK,
            Verdict.API_BREAK,
            Verdict.BREAKING,
        ]
        return max(self.per_library_verdict, self.bundle_verdict, key=order.index)
