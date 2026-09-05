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

"""``BundleSideInput``: one resolution pipeline for a live-or-stored bundle
side (G38 Phase 13).

Before this module existed, "get a bundle-comparable side" meant two
independent code paths that never shared a resolution step:

- **Live**: ``cli_compare_release_helpers._run_bundle_analysis`` calls
  ``bundle.build_bundle_snapshot(dict(old_map))`` directly over a
  ``{canonical_name: Path}`` map of real, on-disk ``.so`` files, and reads
  per-library signature evidence out of the ``_old_bundle_evidence``/
  ``_new_bundle_evidence`` stash ``cli_compare_release._compare_one_library``
  leaves on each library's release-report entry (G38 Phase 9).
- **Stored**: ``bundle_facts.compare_bundle_from_facts()`` reconstructs a
  live-equivalent ``BundleSnapshot`` from a persisted
  :class:`~abicheck.bundle_facts.BundleFacts` document via
  ``bundle_facts.bundle_snapshot_from_facts()``, and its own mandatory
  ``per_library_snapshots`` field *is* the OLD-side signature-evidence map.

Both already resolve to the identical shape --
``(BundleSnapshot, {canonical_name: AbiSnapshot | BundleSignatureEvidence},
InstantiationManifest | None)`` -- :func:`abicheck.bundle_analysis.
analyze_bundle` actually consumes. :class:`BundleSideInput` (a
``LiveBundleInput | StoredBundleFactsInput`` union) and
:func:`resolve_bundle_side` make that shape explicit and give live/live,
stored/live, and stored/stored comparisons one shared resolution step
instead of hand-assembling the tuple twice per caller.

:func:`compare_release_against_bundle_facts` is the concrete unblocking this
module exists for: G38 Phase 2/12 already made ``compare_bundle_from_facts()``
fully implemented and parity-tested, but the plan's own Phase 2 status note
records that no CLI surface feeds it a real stored OLD side --
``cli_compare_release.py`` (the release fan-out's Click entry point) and its
``cli_compare_helpers.py``/``cli_helpers_compare.py`` siblings are all within
two lines of the AI-readiness 2000-line hard cap at the time this module was
written (confirmed by ``wc -l`` immediately before this change; see this
module's own Phase 13 status note in the G38 plan doc for the exact counts),
so a new Click option plus its dispatch branch cannot land in any of them
without first splitting one of those files -- a separate, larger refactor of
its own, not attempted reactively here. This function is the Python-API-level
driver a future CLI flag would delegate to once that room exists: given a
stored OLD-side ``BundleFacts`` path and a live NEW-side directory/package
extraction root, it resolves each matched library's NEW-side snapshot (Tier-2
``service.resolve_input``/``service.compare_snapshots``, never the Tier-1
core directly -- this is a plain library module, not a ``cli_*`` front end,
but it still follows the same discipline), builds the compact
``BundleSignatureEvidence`` projection for the NEW side (G38 Phase 9's memory
discipline -- never a full retained ``AbiSnapshot`` map beyond what one
in-flight comparison needs), and calls
``bundle_facts.compare_bundle_from_facts()`` with that real
``new_signature_evidence`` populated -- closing the literal gap Phase 12's own
"Known gap" note named: "no end-to-end CLI invocation" exercising the Phase 4
parity guarantee. Reachable from ``abicheck compare ...`` since CLI cleanup
phase two, PR I (``frontends/cli/commands/compare_bundle_facts.py``), which
routes to it whenever OLD_INPUT classifies as a stored ``BundleFacts``
document and NEW_INPUT does not.

:func:`~abicheck.workflows.bundle_stored_pair_compare.
compare_stored_bundle_facts_pair` is PR I's stored/stored sibling -- both
sides already resolved, persisted ``BundleFacts`` documents, so it reads no
binaries and parses no header AST on either side at all: a pure in-memory
per-library diff plus the same shared
``bundle_facts.compare_bundle_from_facts()`` bundle-level call. Also
reachable from ``abicheck compare ...`` (via ``compare_bundle_facts.py``,
once NEW_INPUT classifies as stored too). Lives in the real
``abicheck/workflows/`` package rather than here -- it coordinates a
compare workflow rather than sharing this module's own "one live/stored
resolution primitive" scope, so it doesn't extend this file's own
grandfathered-legacy footprint (Codex review). The remaining operand
shape -- live/stored (OLD_INPUT live, NEW_INPUT a stored document) -- has
no driver yet and is rejected outright by
``compare_bundle_operand_dispatch.py``; see that module's own docstring
and PR I's tracking in ``docs/contribute/plans/cli-cleanup-phase-two.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bundle_manifest import InstantiationManifest
    from .bundle_models import BundleDiffResult, BundleSignatureEvidence, BundleSnapshot
    from .checker_types import DiffResult
    from .compile_context import CompileContext
    from .model import AbiSnapshot
    from .policy_file import PolicyFile
    from .workflows.suppression import SuppressionList


@dataclass(frozen=True)
class LiveBundleInput:
    """One bundle side resolved from real, on-disk ``.so`` files.

    *libraries* is a ``{canonical_name: Path}`` map -- the same shape
    ``cli_helpers_compare._build_match_map``/``bundle.build_bundle_snapshot``
    already use. *signature_evidence*, when given, is a pre-resolved
    ``{canonical_name: AbiSnapshot | BundleSignatureEvidence}`` map (e.g. the
    Phase 9 compact projections a release fan-out already stashed per
    library) -- resolving it fresh from *libraries* would mean re-dumping
    every binary a caller has typically already dumped once for its own
    per-library diff. Omitted (the default): :func:`resolve_bundle_side`
    returns an empty evidence map for this side, so the Phase 4
    signature-evidence gate simply does not run for it (identical to every
    pre-Phase-13 caller that never passed one).
    """

    libraries: dict[str, Path]
    signature_evidence: dict[str, AbiSnapshot | BundleSignatureEvidence] = field(
        default_factory=dict
    )
    manifest: InstantiationManifest | None = None


@dataclass(frozen=True)
class StoredBundleFactsInput:
    """One bundle side resolved from a persisted
    :class:`~abicheck.bundle_facts.BundleFacts` file (G38 Phase 2/13).

    No binaries are read -- see ``bundle_facts.bundle_snapshot_from_facts``.

    *max_json_object_nodes*, when given, overrides
    ``bundle_facts.DEFAULT_MAX_JSON_OBJECT_NODES`` for this side's load --
    forwarded to ``serialization.load_bundle_facts``. A real per-library
    facts blob can legitimately need well over the default budget to decode
    (see that constant's own docstring); ``None`` (the default) uses the
    library default, unchanged from before this field existed.
    """

    path: Path
    max_json_object_nodes: int | None = None


#: A bundle side is either a live, on-disk library set or a stored,
#: persisted ``BundleFacts`` document -- see the module docstring.
BundleSideInput = LiveBundleInput | StoredBundleFactsInput


@dataclass(frozen=True)
class ResolvedBundleSide:
    """One side's resolution, in the shape :func:`abicheck.bundle_analysis.
    analyze_bundle` actually consumes -- the common target both
    :class:`LiveBundleInput` and :class:`StoredBundleFactsInput` resolve to.
    """

    snapshot: BundleSnapshot
    signature_evidence: dict[str, AbiSnapshot | BundleSignatureEvidence]
    manifest: InstantiationManifest | None


def resolve_bundle_side(side: BundleSideInput) -> ResolvedBundleSide:
    """Resolve *side* (live or stored) into one :class:`ResolvedBundleSide`.

    Neither branch performs new *ABI* extraction: the live branch parses
    only ``ElfMetadata`` (``bundle.build_bundle_snapshot``, the same
    resolution-graph computation ``_run_bundle_analysis`` already uses for a
    live release), and the stored branch reads no binaries at all
    (``bundle_facts.bundle_snapshot_from_facts``).
    """
    from .bundle import build_bundle_snapshot
    from .bundle_facts import bundle_snapshot_from_facts
    from .serialization import load_bundle_facts

    if isinstance(side, StoredBundleFactsInput):
        facts = load_bundle_facts(side.path, max_json_object_nodes=side.max_json_object_nodes)
        return ResolvedBundleSide(
            snapshot=bundle_snapshot_from_facts(facts),
            signature_evidence=dict(facts.per_library_snapshots),
            manifest=facts.manifest,
        )
    return ResolvedBundleSide(
        snapshot=build_bundle_snapshot(dict(side.libraries)),
        signature_evidence=dict(side.signature_evidence),
        manifest=side.manifest,
    )


def compare_bundle_sides(
    old: BundleSideInput,
    new: BundleSideInput,
    per_library_results: list[DiffResult],
    *,
    manifest: InstantiationManifest | None = None,
    system_providers: list[str] | None = None,
    cohorts: list[str] | None = None,
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
) -> BundleDiffResult:
    """Bundle-level comparison over any live/stored pairing of *old*/*new*.

    Resolves both sides via :func:`resolve_bundle_side` and delegates to
    :func:`abicheck.bundle_analysis.analyze_bundle` -- the single orchestrator
    every other bundle-comparison entry point in this codebase already
    shares (``_run_bundle_analysis`` for live/live,
    ``bundle_facts.compare_bundle_from_facts`` for stored/live) -- so
    live/live, stored/live, live/stored, and stored/stored all run the
    identical detector suite (the core graph-native/diff-derived checks plus
    the Phase 4 C-boundary signature-evidence gate) and can never
    independently drift.

    *manifest*, given explicitly, overrides either side's own resolved
    manifest, mirroring ``compare_bundle()``'s/``compare_bundle_from_facts()``'s
    own ``manifest=`` precedence: an explicit manifest always wins over
    whatever either resolved side happened to carry.

    *per_library_results* must already be diffed old-vs-new for every
    library this bundle-level pass should reason about -- this function
    performs no per-library diffing itself (mirroring every existing bundle-
    analysis entry point, which all take an already-computed diff list
    rather than re-deriving one).
    """
    from .bundle_analysis import analyze_bundle

    old_resolved = resolve_bundle_side(old)
    new_resolved = resolve_bundle_side(new)
    effective_manifest = (
        manifest if manifest is not None else (old_resolved.manifest or new_resolved.manifest)
    )
    return analyze_bundle(
        old_resolved.snapshot,
        new_resolved.snapshot,
        per_library_results,
        manifest=effective_manifest,
        system_providers=system_providers,
        cohorts=cohorts,
        policy=policy,
        policy_file=policy_file,
        old_signature_evidence=old_resolved.signature_evidence or None,
        new_signature_evidence=new_resolved.signature_evidence or None,
    )


def compare_release_against_bundle_facts(
    old_facts_path: Path,
    new_dir: Path,
    *,
    headers: list[Path] | None = None,
    includes: list[Path] | None = None,
    header_backend: str = "auto",
    compile: CompileContext | None = None,
    per_library_headers: dict[str, list[Path]] | None = None,
    per_library_includes: dict[str, list[Path]] | None = None,
    per_library_compile: dict[str, CompileContext] | None = None,
    new_version: str = "",
    lang: str = "c++",
    lang_explicit: bool = False,
    include_private_dso: bool = False,
    manifest_path: Path | None = None,
    system_providers: list[str] | None = None,
    cohorts: list[str] | None = None,
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
    suppress: SuppressionList | None = None,
    include_dependencies: bool = False,
    max_json_object_nodes: int | None = None,
) -> BundleDiffResult:
    """End-to-end driver: a stored OLD-side ``BundleFacts`` file compared
    against a live NEW-side directory/package extraction root (G38 Phase 13).

    The concrete Python-API path ``abicheck.bundle_facts.
    compare_bundle_from_facts()`` was fully implemented and parity-tested
    (G38 Phase 2/12) for, but had no real driver exercising end to end: this
    function discovers every ``.so`` under *new_dir*, matches it by canonical
    library key against *old_facts_path*'s ``per_library_snapshots``, dumps
    and diffs each matched pair through the Tier-2 ``service`` chokepoints
    (never ``checker.compare``/``dumper.dump`` directly), and folds the
    result into one bundle-level comparison -- with a real NEW-side
    signature-evidence map populated, so the Phase 4 C-boundary gate
    actually runs (Phase 12's own "Known gap" note: prior to this function,
    that gate's stored-facts path was verified only by a Python-API-level
    test passing the evidence map by hand).

    Deliberately not exposed on any CLI command -- see this module's own
    docstring for exactly why (every file that would host the dispatch is
    within two lines of the AI-readiness 2000-line hard cap). A library
    present in *new_dir* but absent from the stored OLD facts (or vice
    versa) is simply not diffed by this function -- it is not itself a
    release fan-out's ``--fail-on-removed-library``/added-library
    accounting, which stays a CLI-only concern.

    Only the ELF-only per-library evidence a bundle comparison actually
    needs is resolved (no debug-info package resolution, no PDB) -- the
    plan's own Phase 2 status note names exactly this narrowing ("most of
    compare's ~40 release-fan-out flags ... lose their old-side meaning
    once the old side is already a resolved snapshot") as why this is a
    genuinely smaller surface than ``compare_release_cmd``, not an
    oversight.

    *header_backend*/*compile* forward straight to ``service.resolve_input``
    (both were previously silently dropped: this driver called
    ``resolve_input`` without either kwarg, so a header-scoped NEW side always
    resolved under ``header_backend="auto"`` -- which, absent a real castxml
    on the host, means ``resolve_input`` picks castxml anyway and dies on a
    clang/icpx-only host rather than falling back). A caller with a
    resolved, working ``CompileContext`` (compiler binding, frontend,
    extra flags -- e.g. a SYCL/DPC++ host that needs
    ``CompileContext(gcc_path="icpx", frontend="clang", ...)``) can now pass
    it directly instead of monkeypatching this module.

    *lang_explicit* (Codex review, fresh evidence): whether *lang* reflects
    a genuinely explicit ``--lang`` on the command line rather than the
    identical, indistinguishable Click default -- forwarded straight to
    ``service.resolve_input()``'s own parameter of the same name. Left at
    the default ``False`` (this function's own pre-existing behavior)
    silently forces ``resolve_input``'s auto-detection to run even when a
    caller explicitly asked for ``lang="c++"`` on a language-ambiguous
    header, which can change the extracted API and findings.

    *headers*/*includes*/*compile* are the **uniform** fallback applied to
    every matched library -- correct only when every library in the bundle
    shares one header tree and one compile configuration, which does not
    hold for a mixed-toolchain release (e.g. oneDAL's ``daal``/``oneapi::dal``
    libraries built as plain C++ alongside a ``dpc`` library built
    ``-fsycl``/``icpx``): resolving *every* library with the same
    ``headers``/``includes``/``compile`` in that case parses each library's
    headers under whichever single configuration was supplied, which is
    correct for at most one of them. *per_library_headers*/
    *per_library_includes*/*per_library_compile* are optional
    ``{canonical_name: ...}`` overrides, consulted before the uniform
    fallback for each matched library -- a library with no entry in a given
    per-library map still falls back to that map's uniform sibling
    (*headers*/*includes*/*compile* respectively), so a caller only needs to
    override the libraries that actually differ. A comparison run entirely
    with the uniform fallback (no per-library overrides) remains a **cost
    proof**, not a **correctness proof**, for a mixed-toolchain bundle: it
    demonstrates the driver runs to completion in reasonable time/memory,
    not that every library was parsed under its own real compile
    configuration -- use the per-library maps once any library's headers
    need flags another library's don't.

    *policy_file*, when given, is forwarded to each per-library
    ``service.compare_snapshots()`` call alongside *policy* -- exactly the
    same ``(policy, policy_file)`` pair the native ``compare``/``scan`` CLIs
    already pass through together (a ``policy_file`` never replaces
    *policy*; ``PolicyFile.base_policy``/``overrides``/reclassify rules are
    applied on top of whichever base *policy* set the per-library kind
    classification uses). Previously dropped entirely: this driver forwarded
    only *policy* (a bare base-policy name) to ``service.compare_snapshots``,
    so a caller's ``--policy-file``-shaped reclassify/override rules (kind
    overrides, ``ReclassifyRule`` selectors) never reached the per-library
    diff this function computes, regardless of whether the caller's own
    ``.abicheck.yml``/policy document declared any -- silently scoring every
    matched library under the unmodified base policy alone (Codex review;
    the highest-leverage gap in this driver, since a real policy file's
    reclassify rules can be the difference between a library reading
    COMPATIBLE_WITH_RISK and BREAKING). Omitted (the default): behavior is
    unchanged from before this fix. Also forwarded to the final
    ``compare_bundle_from_facts`` call, so ``BundleDiffResult.bundle_verdict``
    (the ``BUNDLE_*``-kind aggregate) is scored under it too, not just the
    per-library diffs (Codex review, same PR).

    *include_dependencies* (default ``False``) mirrors ``--include-system-
    declarations``'s own Click default (``cli_options.
    include_dependencies_option`` -- ``dumper_scoping.py``'s header-origin
    filter, unrelated to ``--follow-deps``/DT_NEEDED), not
    ``service.resolve_input``'s own bare-Python default of ``True``: a
    ``BundleFacts`` file produced by the ordinary ``compare
    --bundle-facts-out`` CLI flow is dependency-*scoped* by default, so
    resolving the NEW side with the library's other default
    (unfiltered) would give the two sides different
    ``AbiSnapshot.dependency_scope`` values and
    ``service.compare_snapshots`` would raise ``ScopeMismatchError`` for
    every ordinary invocation of this function (Codex review, root
    AGENTS.md's dependency-scoping entry). Pass ``True`` only when
    *old_facts_path* was itself captured with ``--include-system-
    declarations``.

    *max_json_object_nodes*, when given, overrides ``bundle_facts.
    DEFAULT_MAX_JSON_OBJECT_NODES`` for loading *old_facts_path* -- a real
    per-library facts blob (especially a G40 archive-format file for a
    SYCL/DPC++-heavy library with a large template instantiation surface)
    can legitimately need well over the default budget to decode; see
    ``bundle_facts.read_bundle_facts_archive``'s own docstring. ``None``
    (the default) uses the library default, matching this driver's
    pre-existing behavior.

    *suppress*, when given, is forwarded to each per-library
    ``service.compare_snapshots()`` call as its own *suppression* argument
    -- the same object the native ``compare``/``compare-release`` CLIs pass
    (``--suppress``). Previously this driver had no way to honor a caller's
    suppression list at all: every matched library was scored with every
    known/intentional change still live, unlike every other comparison
    entry point in this codebase. Omitted (the default): behavior is
    unchanged from before this parameter existed. Not applied to
    *bundle_findings* (the cross-library ``BUNDLE_*`` aggregate) -- the
    native release fan-out does not apply per-library suppression there
    either, since a suppression rule is authored against one library's own
    symbol/type identity, not a cross-library relationship.
    """
    from . import service
    from .bundle_facts import compare_bundle_from_facts
    from .bundle_manifest import load_manifest
    from .bundle_models import BundleSignatureEvidence
    from .errors import (
        IncompatibleSnapshotSchemaError,
        SnapshotError,
        UnsupportedArtifactError,
    )
    from .package import discover_shared_libraries
    from .serialization import load_bundle_facts
    from .workflows.bundle_facts_library_overrides import (
        validate_matched_library_overrides,
    )
    from .workflows.extraction import build_match_map

    old_facts = load_bundle_facts(old_facts_path, max_json_object_nodes=max_json_object_nodes)

    if new_dir.is_dir():
        new_files = discover_shared_libraries(new_dir, include_private=include_private_dso)
    else:
        new_files = [new_dir]
    # Version-aware duplicate resolution (the same rule the live release
    # fan-out uses -- `cli_compare_release.py`'s own `_build_match_map`,
    # itself now a thin `click.ClickException`-translating wrapper over this
    # same `binary_utils.build_match_map` primitive), rather than a plain
    # last-write-wins dict build: a directory carrying more than one version
    # of a library (e.g. `libfoo.so.9` and `libfoo.so.10`) must not silently
    # resolve to whichever sorts last lexicographically (Codex review). Calls
    # the pure, Click-free primitive directly (ADR-061: this module is
    # classified `workflows`, which may not import the `frontends`-legacy
    # `cli_helpers_compare.py`) -- an `AmbiguousLibraryMatchError` here
    # propagates as a plain Python exception, appropriate for a module with
    # callers outside any Click command.
    new_map, _match_warnings = build_match_map(new_files)

    # Codex review, fresh evidence: the actual validation logic lives in
    # workflows/bundle_facts_library_overrides.py (a `workflows`-classified
    # module), not here -- this grandfathered flat-root module only
    # computes *matched_keys* (the one piece of data that function needs
    # and cannot get any other way, since it's derived from this
    # function's own already-loaded `old_facts`/`new_map`, with no second
    # OLD_FACTS load) and delegates the actual check.
    matched_keys = set(old_facts.per_library_snapshots) & set(new_map)
    validate_matched_library_overrides(
        per_library_headers=per_library_headers,
        per_library_includes=per_library_includes,
        per_library_compile=per_library_compile,
        matched_keys=matched_keys,
    )

    per_library_results: list[DiffResult] = []
    new_signature_evidence: dict[str, BundleSignatureEvidence] = {}
    # ADR-065 D8: a member the capture recorded as degraded (its dump
    # failed; the stored snapshot is an ELF-only stand-in) is *failed*, not
    # evidence -- diffing it would read every real declaration on NEW as an
    # addition. Skipped here, named below, and gated through `scope_record`.
    degraded: dict[str, str] = {
        key: reason
        for key, reason in old_facts.degraded_members.items()
        if key in new_map
    }
    compared: list[str] = []
    # ADR-065 D6: a NEW artifact this build cannot analyze at all is
    # `unsupported` on the scope record (the live fan-out's own per-member
    # rule), not a generic error that escapes before any record exists.
    unsupported: dict[str, str] = {}
    # ADR-065 D1: a NEW artifact whose extraction *fails* (a damaged
    # snapshot file, an unreadable binary) is `failed` on the record, the
    # same per-member handling the native fan-out applies -- never an
    # exception that escapes before the record exists and discards every
    # sibling's completed comparison (Codex review).
    failed: dict[str, str] = {}
    for key, old_snapshot in old_facts.per_library_snapshots.items():
        new_path = new_map.get(key)
        if new_path is None or key in degraded:
            continue
        # Per-library overrides win over the uniform fallback -- a library
        # absent from a given override map still falls back to that map's
        # own uniform sibling (headers/includes/compile respectively), so a
        # caller only needs to name the libraries that actually differ. See
        # the docstring above for why a uniform-only invocation is a cost
        # proof, not a correctness proof, for a mixed-toolchain bundle.
        lib_headers = (per_library_headers or {}).get(key, headers)
        lib_includes = (per_library_includes or {}).get(key, includes)
        lib_compile = (per_library_compile or {}).get(key, compile)
        try:
            new_snapshot = service.resolve_input(
                new_path,
                headers=lib_headers,
                includes=lib_includes,
                version=new_version,
                lang=lang,
                lang_explicit=lang_explicit,
                header_backend=header_backend,
                compile=lib_compile,
                include_dependencies=include_dependencies,
            )
        except (IncompatibleSnapshotSchemaError, UnsupportedArtifactError) as exc:
            unsupported[key] = str(exc)
            continue
        except (SnapshotError, OSError, ValueError) as exc:
            failed[key] = str(exc)
            continue
        compared.append(key)
        diff = service.compare_snapshots(
            old_snapshot, new_snapshot, suppress, policy=policy, policy_file=policy_file
        )
        per_library_results.append(diff)
        new_signature_evidence[key] = BundleSignatureEvidence.from_snapshot(new_snapshot)

    # *old_facts* is already loaded in memory (needed above for the
    # per-library matching loop) -- routed straight to
    # compare_bundle_from_facts() rather than through
    # StoredBundleFactsInput/resolve_bundle_side, which would reload and
    # re-parse the identical file from disk a second time for no benefit.
    from .bundle import build_bundle_snapshot
    from .workflows.release_scope import (
        build_stored_baseline_scope_record,
        bundle_analysis_members,
        out_of_scope_provider_names,
        restrict_bundle_facts,
        scope_manifest_to_members,
    )

    scope_record = build_stored_baseline_scope_record(
        old_facts.per_library_snapshots,
        new_map,
        compared=compared,
        degraded=degraded,
        old_provenance="stored bundle-facts capture that made no complete-inventory assertion",
        new_provenance="live directory/archive listing: no declared inventory",
        new_single_artifact=not new_dir.is_dir(),
        unsupported=unsupported,
        failed=failed,
        old_complete=old_facts.inventory_complete,
    )
    # ADR-065 D2: the bundle graph sees matched members and proven
    # removals/additions only (Codex review) -- see bundle_analysis_members.
    bundle_members = bundle_analysis_members(scope_record)
    # ADR-065 D2 (Codex review): a promise only an excluded member could
    # answer is withheld, not reported as manifest drift.
    manifest, manifest_note = scope_manifest_to_members(
        load_manifest(manifest_path) if manifest_path is not None else old_facts.manifest,
        scope_record,
    )
    new_bundle_snapshot = build_bundle_snapshot(
        {k: v for k, v in new_map.items() if k in bundle_members}
    )
    result = compare_bundle_from_facts(
        # `manifest=` is the resolved, scoped manifest even when it is None
        # (fully withheld): compare_bundle_from_facts's own fallback to
        # `old_facts.manifest` must not re-enforce a stored manifest an
        # explicit one already replaced (CodeRabbit review).
        replace(restrict_bundle_facts(old_facts, scope_record), manifest=manifest),
        new_bundle_snapshot,
        per_library_results,
        manifest=manifest,
        system_providers=[
            *(system_providers or ()),
            *out_of_scope_provider_names(scope_record),
        ],
        cohorts=cohorts,
        policy=policy,
        policy_file=policy_file,
        new_signature_evidence=dict(new_signature_evidence),
    )
    result.analysis_errors.extend(
        f"{key}: OLD side was captured degraded ({reason}); per-library "
        "comparison skipped (ADR-065 D8)"
        for key, reason in sorted(degraded.items())
    )
    result.analysis_errors.extend(
        f"{key}: NEW artifact is unsupported by this build ({reason}); "
        "per-library comparison skipped (ADR-065 D6)"
        for key, reason in sorted(unsupported.items())
    )
    result.analysis_errors.extend(
        f"{key}: NEW artifact failed extraction ({reason}); per-library "
        "comparison skipped (ADR-065 D1)"
        for key, reason in sorted(failed.items())
    )
    if manifest_note is not None:
        result.analysis_errors.append(manifest_note)
    result.scope_record = scope_record
    result.extraction_failures = dict(failed)
    return result
