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

"""Bundle-aware multi-library ABI analysis (ADR-023).

The per-library compare implemented in ``checker.compare`` treats each
library as an isolated unit. Real releases (for example oneDAL or
libtorch) ship multiple libraries that reference each other's symbols;
intra-bundle
breakage (sibling removes a symbol another sibling imports, extern-C
signature drift across the DSO boundary, cross-DSO type drift, provider
migration, instantiation-manifest drift) is invisible to per-library diff.

This module computes a *bundle finding* layer on top of per-library diff
results. It reuses :mod:`abicheck.resolver` for the dependency graph and
:mod:`abicheck.elf_metadata` for ELF parsing. The actual per-library diff
input is what ``compare-release`` already produces.

Public surface:
    - :class:`BundleSnapshot`     — a release viewed as a set of libraries.
    - :class:`BundleFinding`      — one cross-library change with provider
                                    and consumer attribution.
    - :class:`BundleDiffResult`   — output of :func:`compare_bundle`.
    - :func:`compare_bundle`      — main entry point, given two already-built
                                    :class:`BundleSnapshot`\\ s and the
                                    per-library diffs to correlate.
    - :func:`discover_artifact_set` — resolve a set of paths into a
                                    ``{canonical_name: path}`` bundle map,
                                    the shape :func:`build_bundle_snapshot`
                                    and :func:`compare_bundle` both need.

For a caller with two plain *directories* on disk (e.g. two
:mod:`abicheck.product_baseline` archives already unpacked) who doesn't want
to build snapshots or run the per-library compares themselves, see
:func:`abicheck.product_baseline.compare_product_directories` — it discovers,
matches, and diffs for you, then calls :func:`compare_bundle`. Kept in
``product_baseline`` rather than here: it needs the per-pair compare engine
(``service_compare_pipeline.run_compare``), and that module's own import
graph already reaches back into this one (``service_scan`` calls
:func:`audit_bundle`), so importing it from this module would create an
import cycle.

Bundle findings use the ``ChangeKind.BUNDLE_*`` values registered in
:mod:`abicheck.change_registry`. They participate in policy classification,
suppression, severity, and reporter machinery identically to per-library
``Change`` entries.

Individual finding-producers (the ``_detect_*`` functions this module's
``compare_bundle``/``audit_bundle`` orchestrate) and the heuristic
primitives they share live in :mod:`abicheck.bundle_detectors` and its own
sibling :mod:`abicheck.bundle_detector_heuristics`, split out purely to
stay under the AI-readiness 2000-line hard cap -- see either module's own
docstring.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

from .bundle_detector_heuristics import (  # noqa: F401  (re-exported for back-compat)
    _build_demangled_index as _build_demangled_index,
    _detect_manifest_drift,
    _detect_soname_skew,
    _looks_system_symbol as _looks_system_symbol,
    _match_entry as _match_entry,
    _path_looks_like_elf,
    _soname_skew_findings as _soname_skew_findings,
    _strip_namespace_prefix as _strip_namespace_prefix,
)
from .bundle_detectors import (
    _detect_intra_dep_removed,
    _detect_intra_dep_signature_changed,
    _detect_intra_type_changed,
    _detect_library_structural_changes,
    _detect_provider_changed,
    _detect_unresolved_intra_dependency,
    _detect_version_drift,
)
from .bundle_manifest import (  # noqa: F401  (re-exported for back-compat)
    InstantiationManifest as InstantiationManifest,
    ManifestEntry as ManifestEntry,
    _expand_instantiations as _expand_instantiations,
    load_manifest as load_manifest,
)
from .bundle_models import (  # noqa: F401  (re-exported for back-compat)
    DEFAULT_SYSTEM_PROVIDERS as DEFAULT_SYSTEM_PROVIDERS,
    BundleDiffResult as BundleDiffResult,
    BundleFinding as BundleFinding,
    BundleSnapshot as BundleSnapshot,
    ConsumerEntry as ConsumerEntry,
    ProviderEntry as ProviderEntry,
    ResolutionGraph as ResolutionGraph,
    basename_to_bundle_key,
)
from .bundle_soname import hard_link_alias_basenames
from .checker_policy import Verdict, compute_verdict
from .checker_types import DiffResult
from .elf_metadata import ElfMetadata, parse_elf_metadata

if TYPE_CHECKING:
    from .policy_file import PolicyFile

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bundle snapshot construction
# ---------------------------------------------------------------------------


def build_bundle_snapshot(libraries: dict[str, Path]) -> BundleSnapshot:
    """Parse every library in the release and build the resolution graph.

    Args:
        libraries: A {canonical_name: path} map (the same shape
            ``_build_match_map`` produces in :mod:`abicheck.cli`).

    Returns:
        A :class:`BundleSnapshot` with all libraries' :class:`ElfMetadata`
        and the resolution graph populated.

    Non-ELF inputs are skipped with a warning; the bundle layer is
    Linux/ELF-only by design (see ADR-018 — PE/Mach-O bundle analysis is
    out of scope for this iteration).

    A thin wrapper around :func:`build_bundle_snapshot_from_metadata`: this
    function's own job is *only* turning a path into an :class:`ElfMetadata`
    (parsing, format-sniffing, the parse-failure/empty-result skip logic);
    everything after that — the emptiness check, the resolution graph, the
    root computation — lives in the shared function so a caller that
    already has parsed :class:`ElfMetadata` (e.g. from an
    :class:`~abicheck.model.AbiSnapshot`'s own ``.elf`` field, populated by
    every ELF ``dump``) doesn't have to re-parse the binary from disk to
    reach the identical bundle-analysis result (see that function's own
    docstring for why this split exists).
    """
    from . import deadline

    metadata: dict[str, ElfMetadata] = {}
    for name, path in libraries.items():
        # Cooperative checkpoint (no-op unless a deadline.deadline_scope()
        # is active, e.g. service_scan.run_scan_set's audit-mode call) --
        # lets a large/pathological set's parsing loop abort between
        # members instead of only being caught by an elapsed-time check
        # after the whole snapshot finishes building (Codex review).
        # Deliberately not a bound on the parse_elf_metadata() call itself
        # (Codex review, fresh finding): a single large/malformed member
        # can still run past --budget mid-parse before this checkpoint is
        # reached again on the *next* member, unlike a spawned castxml/
        # clang subprocess (bounded per-call via deadline.bounded_timeout())
        # -- parse_elf_metadata is pure in-process struct parsing, not a
        # killable child process, and threading deadline.check() calls
        # into its shared, general-purpose ELF-parsing internals (used far
        # beyond this one audit-mode caller) is out of scope for this
        # narrow fix. The one place this call path *is* genuinely bounded
        # by an OS-level killable timeout end to end is
        # service_scan.run_scan_set_subprocess (what MCP's abi_scan uses);
        # the CLI path (cli_scan._run_artifact_set) calls run_scan_set()
        # directly, the same architecture the single-binary scan command
        # already uses for run_scan_core, so this is a pre-existing CLI-
        # wide limitation (cooperative checkpoints only, no OS-level kill),
        # not one specific to bundle auditing.
        deadline.check()
        # Bundle analysis is Linux/ELF-only by design (see ADR-018,
        # ADR-023). Skip JSON snapshots, PE/Mach-O, or other formats up
        # front so parse_elf_metadata never emits its "Magic number does
        # not match" warning on legitimately-non-ELF inputs.
        if not _path_looks_like_elf(path):
            log.debug("bundle: skipping non-ELF input %s", path)
            continue
        try:
            meta = parse_elf_metadata(path)
        except (
            Exception
        ) as exc:  # pragma: no cover — parse_elf_metadata already swallows most
            log.warning("bundle: failed to parse %s: %s", path, exc)
            continue
        if meta is None:
            log.debug("bundle: skipping non-ELF input %s", path)
            continue
        metadata[name] = meta

    # probe_filesystem=True: unlike build_bundle_snapshot_from_metadata's
    # own metadata-only default, this wrapper is handed real, live paths on
    # disk -- preserve its pre-existing filesystem alias-probing behavior
    # (symlink targets, hard-link aliases) rather than silently losing it
    # to that function's metadata-only default (Codex review, fresh
    # evidence: a second-round regression in the first-round filesystem-
    # independence fix, which changed this callee's own default without
    # threading this caller's real requirement through it).
    return build_bundle_snapshot_from_metadata(
        metadata, paths=libraries, probe_filesystem=True
    )


def build_bundle_snapshot_mixed(libraries: dict[str, Path]) -> BundleSnapshot:
    """`build_bundle_snapshot`'s ADR-062 A1.7 counterpart: *libraries* may
    map some names to a live ELF file (parsed exactly as
    `build_bundle_snapshot` already does) and others to a stored,
    directory-backed `ProjectSnapshot` sub-package
    (`project_snapshot_legacy.materialize_release_variant_artifacts`'s own
    output, or any other pre-resolved package directory) -- resolved via
    `_stored_elf_metadata` (a direct, `serialization.py`-free document read,
    not the general `workflows.input_resolution.resolve_input` dispatcher --
    see that helper's own docstring for why) instead of a live ELF parse,
    reusing whatever `ElfMetadata` its own stored document already carries.

    Without this split, `_path_looks_like_elf` (which `build_bundle_
    snapshot` alone would apply) treats every directory as "not ELF, skip"
    -- silently dropping every stored-side library from bundle-level
    analysis (`compare_bundle`'s cross-DSO `DT_NEEDED`/symbol-removal/
    provider-migration checks) rather than raising, which would otherwise
    let a real intra-bundle break (a sibling library still importing a
    symbol a stored library actually removed) go completely unreported --
    both per-library *and* bundle-level -- for any release compared against
    a stored package operand (Codex review, security finding: a CI ABI
    gate silently passing on a real break).

    Delegates the live-path subset to `build_bundle_snapshot` itself
    (rather than re-deriving the identical parse loop here) -- including
    the pure-live case (no directory entries at all), which returns
    `build_bundle_snapshot(libraries)`'s own result unchanged. This keeps
    `build_bundle_snapshot` the single owner of "how a live path becomes
    `ElfMetadata`" and preserves it as the one seam a caller mocks to test
    bundle-analysis wiring without real binaries.

    For each stored entry, also recovers its real on-disk filename and
    filesystem aliases from the sub-package's own `ArtifactRef.
    native_identity` (`_stored_library_identity`, best-effort -- absent for
    a package a different writer produced without that evidence) and
    threads them through as `build_bundle_snapshot_from_metadata`'s `paths`/
    `extra_aliases`, the same way `bundle_facts.bundle_snapshot_from_facts`
    already does for its own purely-metadata-driven reconstruction: without
    this, a stored provider with no `DT_SONAME` is only ever known by its
    bundle key (a canonical match key, or an artifact id -- never a real
    filename), so `_detect_soname_skew`'s own SONAME-major filename
    fallback and a sibling's DT_NEEDED naming that real filename verbatim
    both silently fail to resolve (Codex review, fresh evidence: a real
    intra-bundle break going unreported for exactly the case this function
    exists to catch).
    """
    stored = {name: path for name, path in libraries.items() if path.is_dir()}
    if not stored:
        return build_bundle_snapshot(libraries)

    metadata: dict[str, ElfMetadata] = {}
    stored_paths: dict[str, Path] = {}
    extra_aliases: dict[str, tuple[str, ...]] = {}
    for name, path in stored.items():
        elf = _stored_elf_metadata(path)
        if elf is None:
            continue
        metadata[name] = elf
        real_filename, aliases = _stored_library_identity(path)
        if real_filename is not None:
            stored_paths[name] = real_filename
        if aliases:
            extra_aliases[name] = aliases

    live = {name: path for name, path in libraries.items() if name not in stored}
    if live:
        metadata.update(build_bundle_snapshot(live).metadata)

    # `probe_filesystem_names=frozenset(live)` -- *not* the merged `paths`
    # below -- is what actually keeps the real-filesystem symlink-resolve/
    # hard-link scan scoped to genuinely live paths: `stored_paths` above
    # deliberately also lands in `paths` (for display and DT_NEEDED-name
    # matching purposes), but a stored sub-package directory's recovered
    # filename is never a real file *at that path* on this filesystem, so
    # probing it would resolve against -- and could accidentally match an
    # unrelated real file/symlink sitting in -- the caller's own cwd
    # (Codex review, security finding: metadata-only resolution must not
    # depend on ambient filesystem state).
    return build_bundle_snapshot_from_metadata(
        metadata,
        paths={**stored_paths, **live},
        probe_filesystem=True,
        probe_filesystem_names=frozenset(live),
        extra_aliases=extra_aliases or None,
    )


def _stored_elf_metadata(path: Path) -> ElfMetadata | None:
    """*path*'s own `ElfMetadata`, read directly from its materialized
    single-artifact `ProjectSnapshot` sub-package document.

    Deliberately **not** `workflows.input_resolution.resolve_input` (the
    general dispatcher every other "turn a path into a snapshot" call site
    in this codebase uses): that module reaches `serialization.py`, which
    carries a `TYPE_CHECKING`-only `BundleFacts` import back to
    `bundle_facts.py`, which itself reaches back into *this* module
    (`bundle_snapshot_from_facts`, function-local) -- so `bundle.py`
    importing that dispatcher, however deep, closes a real
    `bundle -> workflows.input_resolution -> serialization -> bundle_facts
    -> bundle` import cycle (`scripts/check_ai_readiness.py`'s
    `import-cycle-growth` check; see `storage/native_identity_aliases.py`'s
    own docstring for the sibling cycle its own split avoids).

    `project_snapshot_legacy.read_legacy_snapshot_document` reads the exact
    same flat, `serialization.snapshot_from_dict()`-shaped document without
    ever importing `serialization.py` itself (it works directly with
    `storage.import_v1`'s per-section dict reconstruction, never the parsed
    `AbiSnapshot` type -- see that module's own docstring), and
    `snapshot_platform_blocks.elf_from_dict` -- the identical parser
    `serialization.snapshot_from_dict`'s own `elf` field uses -- is a
    dependency-free leaf module. Combined, they parse the identical
    `ElfMetadata` a full `resolve_input(path).elf` would, for the one
    section `build_bundle_snapshot_mixed` actually needs, without ever
    reaching `serialization.py`.

    Returns `None` -- never raises -- for anything that doesn't parse as a
    single-artifact package with ELF metadata present: matches
    `build_bundle_snapshot`'s own "non-ELF/failed parse is skipped, not
    fatal" contract.
    """
    from .project_snapshot_legacy import read_legacy_snapshot_document
    from .snapshot_platform_blocks import elf_from_dict

    try:
        document = read_legacy_snapshot_document(path)
    except Exception as exc:
        log.warning("bundle: failed to resolve stored package %s: %s", path, exc)
        return None
    elf_data = document.get("elf")
    if not isinstance(elf_data, dict):
        log.debug("bundle: stored package %s has no ELF metadata", path)
        return None
    schema_version = int(document.get("schema_version", 1))
    return cast("ElfMetadata", elf_from_dict(elf_data, schema_version))


def _stored_library_identity(path: Path) -> tuple[Path | None, tuple[str, ...]]:
    """Best-effort real on-disk filename + filesystem aliases for a stored
    `ProjectSnapshot` sub-package directory at *path*, read from its sole
    artifact's `ArtifactRef.native_identity` -- the same
    `library_filename`/`filesystem_aliases` keys `bundle_facts_store.py`'s
    own writer stamps there, defined in the shared leaf module `storage.
    native_identity_aliases` rather than read back from `bundle_facts_store`
    itself: that module is `workflows`-classified and imports `bundle_facts`
    at module load, which reaches back into this module via a function-local
    import (`bundle_snapshot_from_facts`) -- so a `bundle ->
    bundle_facts_store` edge here would close a real
    `bundle -> bundle_facts_store -> bundle_facts -> bundle` cycle
    (`scripts/check_ai_readiness.py`'s `import-cycle-growth` check, see
    `storage/native_identity_aliases.py`'s own docstring for the full
    account). See `bundle_facts_store.py`'s module docstring for why there
    are two independent, not-yet-reconciled writers of this native_identity
    contract.

    Returns `(None, ())` -- never raises -- for anything that doesn't parse
    as a single-artifact package with that evidence recorded: a stored
    package produced by something other than `project_snapshot_legacy.
    materialize_release_variant_artifacts` (`build_bundle_snapshot_mixed`'s
    own docstring accepts "any other pre-resolved package directory") may
    simply not carry it, which is not itself an error --
    `build_bundle_snapshot_from_metadata` already degrades to a synthetic
    `Path(name)` when `paths` has no entry for a name.
    """
    from .project_snapshot_store import read_artifact_ref, read_manifest_summary
    from .storage.native_identity_aliases import (
        NATIVE_IDENTITY_ALIASES_KEY,
        NATIVE_IDENTITY_FILENAME_KEY,
        decode_native_identity_aliases,
    )

    try:
        summary = read_manifest_summary(path)
        if len(summary.artifact_ids) != 1:
            return None, ()
        artifact = read_artifact_ref(path, summary.artifact_ids[0])
    except Exception as exc:
        log.debug("bundle: no native_identity evidence for %s: %s", path, exc)
        return None, ()

    filename = artifact.native_identity.get(NATIVE_IDENTITY_FILENAME_KEY)
    real_filename = Path(filename) if filename else None

    aliases: tuple[str, ...] = ()
    aliases_text = artifact.native_identity.get(NATIVE_IDENTITY_ALIASES_KEY)
    if aliases_text:
        try:
            aliases, _ = decode_native_identity_aliases(aliases_text, 0)
        except Exception as exc:
            log.debug("bundle: malformed filesystem_aliases for %s: %s", path, exc)
            aliases = ()
    return real_filename, aliases


def build_bundle_snapshot_from_metadata(
    metadata: dict[str, ElfMetadata],
    *,
    paths: dict[str, Path] | None = None,
    root: Path | None = None,
    probe_filesystem: bool = False,
    extra_aliases: dict[str, tuple[str, ...]] | None = None,
    probe_filesystem_names: frozenset[str] | None = None,
) -> BundleSnapshot:
    """Build a :class:`BundleSnapshot` from already-parsed :class:`ElfMetadata`,
    without re-parsing (or even requiring) the underlying binary files.

    :func:`build_bundle_snapshot` needs real files on disk purely to call
    :func:`~abicheck.elf_metadata.parse_elf_metadata` — everything the
    bundle layer actually analyzes (``DT_NEEDED``, ``.gnu.version_r``/
    ``.gnu.version_d``, exported symbols) lives in the resulting
    :class:`~abicheck.elf_metadata.ElfMetadata` alone. That struct is
    exactly what :attr:`abicheck.model.AbiSnapshot.elf` already stores for
    every ELF ``dump`` (populated straight from the same
    :func:`~abicheck.elf_metadata.parse_elf_metadata` call), so a caller
    holding two sides' worth of *snapshots* (rather than two directories of
    live binaries — e.g. a future snapshot-only product baseline, or any
    other caller that already dumped each library) can build a real,
    fully-functional :class:`BundleSnapshot` — cross-DSO ``DT_NEEDED``/
    version-table analysis included — directly from that stored metadata,
    with no binaries required at compare time at all. This is the primitive
    a snapshot-first product baseline would build on; it does not itself
    change how ``dump``/``pack_product_baseline`` work today.

    Args:
        metadata: A ``{library_name: ElfMetadata}`` map — the same keying
            :func:`build_bundle_snapshot` uses, e.g. one entry per
            ``AbiSnapshot.elf`` already on hand. An entry whose metadata is
            empty (no soname, symbols, imports, or needed libraries — the
            same heuristic :func:`build_bundle_snapshot` applies after a
            successful parse) is dropped, the same as a non-ELF/failed
            parse is dropped there.
        paths: Optional ``{library_name: Path}`` map used only for
            :attr:`BundleSnapshot.libraries`' values and the default
            *root* computation (both currently used only for their
            ``.name``/``.parent`` — see ``_detect_soname_skew``'s own
            ``path.name`` SONAME fallback). A name with no entry here
            synthesizes ``Path(name)``, which still gives a sensible
            ``.name`` for that same fallback when *name* is (or ends in) a
            real filename — the common case for every caller so far.
        root: Explicit bundle root. When omitted, derived from the first
            surviving library's resolved path's parent (matching
            :func:`build_bundle_snapshot`'s own behavior exactly when
            *paths* holds real filesystem paths).
        probe_filesystem: Forwarded to :func:`_compute_resolution_graph`.
            Defaults to ``False`` — this function's whole contract is
            metadata-only resolution, so even a caller-supplied *paths*
            (given only for display purposes — a library's own
            ``.name``/``.parent``) must not make the resolution graph
            silently depend on what those paths happen to resolve to on
            the real filesystem (Codex review, fresh evidence).
            :func:`build_bundle_snapshot` — the one caller that genuinely
            holds real, live binaries on disk, not just their already-
            parsed metadata — passes ``True`` explicitly to preserve its
            own pre-existing live-filesystem alias-probing behavior
            (symlink targets, hard-link aliases): this function is its
            thin metadata-extraction wrapper (see its own docstring), so a
            regression here silently reached every real caller of it, not
            only a genuinely metadata-only one (Codex review, fresh
            evidence — a second-round regression in the first-round fix
            above).
        probe_filesystem_names: Restricts which names *probe_filesystem*
            actually probes on the real filesystem, when given. ``None``
            (the default) probes every name that has a caller-supplied
            *paths* entry — ``build_bundle_snapshot``'s own behavior,
            unchanged, since it always supplies one real path per name.
            A caller that mixes real, live paths with paths that merely
            *carry a real filename for display/matching* but are not
            themselves resolvable on disk (``build_bundle_snapshot_mixed``'s
            stored-package entries) must pass an explicit, narrower set —
            otherwise ``_compute_resolution_graph`` would ``.resolve()`` a
            non-live name's synthesized/recovered path against the
            process's own current working directory (Codex review,
            security finding).
    """
    from . import deadline

    paths = paths or {}
    surviving_metadata: dict[str, ElfMetadata] = {}
    surviving_paths: dict[str, Path] = {}
    for name, meta in metadata.items():
        deadline.check()
        if meta is None or (
            not meta.soname
            and not meta.symbols
            and not meta.imports
            and not meta.needed
        ):
            log.debug("bundle: skipping empty metadata for %s", name)
            continue
        surviving_metadata[name] = meta
        surviving_paths[name] = paths.get(name, Path(name))

    resolution = _compute_resolution_graph(
        surviving_paths,
        surviving_metadata,
        probe_filesystem=probe_filesystem,
        extra_aliases=extra_aliases,
        probe_filesystem_names=(
            frozenset(paths)
            if probe_filesystem_names is None
            else probe_filesystem_names
        ),
    )
    # Use the first library's parent as the root if available; otherwise empty path
    resolved_root = (
        root
        if root is not None
        else (
            next(iter(surviving_paths.values())).parent if surviving_paths else Path()
        )
    )
    return BundleSnapshot(
        root=resolved_root,
        libraries=surviving_paths,
        metadata=surviving_metadata,
        resolution=resolution,
        # probe_filesystem already means "real, live paths" (see
        # BundleSnapshot.filesystem_backed's own docstring).
        filesystem_backed=probe_filesystem,
    )


class ArtifactSetError(ValueError):
    """Raised by :func:`discover_artifact_set` for an invalid `--artifact-set`.

    A plain, framework-agnostic exception — the CLI layer (``cli_scan.py``)
    turns this into a ``click.UsageError`` (exit 64); this module has no
    click dependency.
    """


def discover_artifact_set(paths: list[Path], *, explicit: bool) -> dict[str, Path]:
    """Resolve a list of paths into a ``{canonical_name: path}`` bundle map.

    ADR-056: shared by both ``--artifact-set`` forms (a directory the caller
    already expanded to its member files, or an explicit comma-separated
    path list) — the caller passes ``explicit=True`` only for the latter.

    Two corrections folded in after review (both real, not edge cases):

    - **Symlink-alias deduplication.** A completely ordinary Unix install
      layout has both a versioned real file (``libfoo.so.1``) and an
      unversioned dev symlink to it (``libfoo.so``) — ``discover_shared_
      libraries()`` (``abicheck/package.py``) lists both as separate
      discovered paths, and both canonicalize to the same name. Resolving
      each path (``Path.resolve()``) and deduplicating identical targets
      *before* collision-checking means this common layout is accepted, not
      rejected.
    - **Collision rejection for genuinely distinct files.** Once aliases are
      collapsed, two *different* resolved files that still canonicalize to
      the same library name (e.g. an explicit ``dir1/libfoo.so,
      dir2/libfoo.so`` naming two unrelated real files) are rejected
      outright — unlike ``compare``'s two-sided old-vs-new matching
      (``_build_match_map``), a one-sided audit set has no "newest version
      wins" tiebreak that would be sound here.

    For the explicit-list form, every named path must look like a real ELF
    shared object (``package._is_elf_shared_object``) — every entry was
    deliberately named by the caller, so silently dropping an unsupported
    one (the way
    :func:`build_bundle_snapshot` does for a directory scan, where "some
    files aren't libraries" is expected) would misrepresent the audit as
    covering the full declared set. Raises :class:`ArtifactSetError` for any
    unsupported explicit member, or for a genuine name collision.
    """
    from .binary_utils import _canonical_library_key

    resolved_by_real: dict[Path | tuple[int, int], Path] = {}
    for path in paths:
        try:
            real = path.resolve()
        except OSError:
            real = path
        # Path.resolve() only follows symlinks -- it does not coalesce two
        # hard links to the same inode (a real, if unusual, way a library
        # directory can carry both a versioned name and an unversioned
        # alias). When available, key on filesystem identity
        # (st_dev, st_ino) instead of the resolved path, so both survive-
        # as-distinct-members outcomes Codex flagged are avoided: two
        # hard-linked aliases with the *same* canonical name no longer
        # spuriously collide below, and two differently-named hard-linked
        # aliases no longer pass the cardinality check as if they were
        # genuinely distinct libraries (bundle analysis is Linux/ELF-only
        # by design, ADR-018/023, so POSIX inode semantics always apply
        # here).
        try:
            st = real.stat()
            identity: Path | tuple[int, int] = (st.st_dev, st.st_ino)
        except OSError:
            identity = real
        # Keep the first-seen original (unresolved) path for user-facing
        # messages/reporting identity; only the resolution key is the
        # canonicalized real path / filesystem identity.
        resolved_by_real.setdefault(identity, path)

    if explicit:
        # A full ET_DYN-vs-PIE shared-object check (package.py's
        # _is_elf_shared_object), not just the cheap 4-byte magic sniff
        # _path_looks_like_elf uses elsewhere in this module: an explicitly
        # named ELF executable, relocatable object, or core file has the
        # right magic bytes but is not a library, and directory discovery
        # (package.discover_shared_libraries) already restricts its own
        # members to real shared objects -- the explicit-list form must not
        # be laxer just because the caller typed the path out (Codex review).
        from .package import _is_elf_shared_object

        unsupported = [
            p for p in resolved_by_real.values() if not _is_elf_shared_object(p)
        ]
        if unsupported:
            names = ", ".join(str(p) for p in unsupported)
            raise ArtifactSetError(
                f"--artifact-set names unsupported (non-ELF-shared-object) "
                f"member(s): {names}. Every explicitly-named path must be a "
                "real shared library (not an executable, relocatable "
                "object, or core file); for a mixed directory, pass the "
                "directory instead."
            )

    buckets: dict[str, list[Path]] = {}
    for path in resolved_by_real.values():
        buckets.setdefault(_canonical_library_key(path), []).append(path)

    collisions = {key: vals for key, vals in buckets.items() if len(vals) > 1}
    if collisions:
        detail = "; ".join(
            f"'{key}': {[str(p) for p in vals]}" for key, vals in collisions.items()
        )
        raise ArtifactSetError(
            f"--artifact-set has colliding library identities: {detail}. "
            "Each library in an artifact set must have a distinct canonical "
            "name; rename or drop the duplicate(s)."
        )

    return {key: vals[0] for key, vals in buckets.items()}


@dataclass
class BundleAuditResult:
    """Output of :func:`audit_bundle` — the no-old-side sibling of
    :class:`BundleDiffResult`.

    Unlike :class:`BundleDiffResult` there is no ``old_root``/``per_library``:
    an audit has exactly one side (the declared artifact set), no diff to
    read, and therefore only the subset of bundle findings computable from a
    single-side resolution graph (see
    :func:`_detect_unresolved_intra_dependency`).
    """

    snapshot: BundleSnapshot
    findings: list[BundleFinding] = field(default_factory=list)

    @property
    def verdict(self) -> Verdict:
        changes = [f.to_change() for f in self.findings]
        return compute_verdict(changes)


def check_artifact_set_soname_collisions(libraries: dict[str, Path]) -> None:
    """Reject an ambiguous duplicate-``DT_SONAME`` artifact set up front.

    P2 regression (Codex review): :func:`audit_bundle` only discovers this
    ambiguity *after* building the full resolution graph — for
    ``scan --artifact-set``, that means only after every member has already
    been individually scanned (:func:`~abicheck.service_scan.run_scan_set`
    runs :func:`discover_artifact_set` → per-member scans →
    :func:`audit_bundle`, in that order). If an earlier member scan then
    exhausted ``--budget``, this genuine usage error was masked as an
    ordinary ``BUDGET_OVERFLOW``, and every member's own (potentially
    expensive, e.g. a build/compiler query) scan already ran for a request
    that was always going to be rejected. ``discover_artifact_set``'s own
    prevalidation is filesystem-identity-only and cannot see this — a
    ``DT_SONAME`` collision is only visible after parsing each member's ELF
    dynamic section.

    Deliberately re-parses each member (the same per-library
    :func:`~abicheck.elf_metadata.parse_elf_metadata` cost
    :func:`build_bundle_snapshot` already pays) rather than building the
    full snapshot's resolution graph here — the collision check only needs
    each member's ``DT_SONAME``, not the (comparatively more expensive)
    intra-set dependency edges ``build_bundle_snapshot`` also computes.
    """
    from . import deadline

    metadata: dict[str, ElfMetadata] = {}
    for name, path in libraries.items():
        deadline.check()
        if not _path_looks_like_elf(path):
            continue
        try:
            meta = parse_elf_metadata(path)
        except (
            Exception
        ) as exc:  # pragma: no cover — parse_elf_metadata already swallows most
            log.warning("bundle: failed to parse %s: %s", path, exc)
            continue
        if meta is not None:
            metadata[name] = meta
    duplicate_sonames = _find_duplicate_sonames(metadata)
    if duplicate_sonames:
        detail = "; ".join(
            f"'{soname}': {names}" for soname, names in duplicate_sonames.items()
        )
        raise ArtifactSetError(
            f"--artifact-set has ambiguous duplicate SONAME provider(s): "
            f"{detail}. Each library in an artifact set must advertise a "
            "distinct DT_SONAME; rename or drop the duplicate(s)."
        )


def artifact_set_member_exports(
    libraries: dict[str, Path],
) -> dict[str, frozenset[str]]:
    """Each artifact-set member's own default-exported symbol names (G35).

    Deliberately narrow and cheap: an ELF header/dynsym-only parse
    (:func:`~abicheck.elf_metadata.parse_elf_metadata`, never raises — an
    unparseable member just contributes an empty set) with no DWARF/header-AST
    work, run once by :func:`~abicheck.service_scan.run_scan_set` before any
    member's full scan so each member's own ``public_not_exported``
    cross-check can be told the union of what its *siblings* export (a shared
    umbrella header commonly declares more than one member's own public API —
    see :class:`~abicheck.buildsource.crosscheck.CrosscheckConfig`'s
    ``sibling_exported_symbols`` field for what consumes this). Mirrors the
    same ``is_default``-only filter
    :func:`~abicheck.buildsource.crosscheck_base._exported_symbol_names`
    applies to a live snapshot's own ELF export table, so "satisfied by a
    sibling" uses the identical default/unversioned-binding notion of
    "exported" as "satisfied by this member itself" — a symbol that exists
    only as a non-default version alias on a sibling would not satisfy an
    unversioned consumer link there either, and must not satisfy one here.

    Separate from :func:`build_bundle_snapshot`'s own full ELF parse (used by
    :func:`audit_bundle` for the resolution-graph bundle findings) rather
    than reusing its result: that call still happens after every member's
    own scan in ``run_scan_set``, and this export union is needed *before*
    that loop so each member's scan can consult it while running, not after.

    **Known gap (Codex review, not fixed here — see the G35 plan doc):** a
    raw export name only, with no L4 reconciliation applied. A sibling that
    exports a declaration only under a variant spelling (a ctor's base-object
    clone, a Mach-O/demangle drift — the same class
    ``crosscheck._l4_reconciled_symbols`` already exempts for the *current*
    member) still false-positives here, since that reconciliation mapping
    lives on a member's own built snapshot, which doesn't exist yet at this
    point — fixing it would mean building every member's full snapshot before
    scanning any of them, a heavier change than this ELF-only pass.
    """
    from . import deadline

    exports: dict[str, frozenset[str]] = {}
    for name, path in libraries.items():
        deadline.check()
        meta = parse_elf_metadata(path)
        exports[name] = frozenset(
            s.name for s in meta.symbols if s.name and s.is_default
        )
    return exports


def audit_bundle(
    libraries: dict[str, Path],
    *,
    bundle_system_providers: Iterable[str] = (),
) -> BundleAuditResult:
    """Run the audit-mode (no old side) bundle analysis for a declared set.

    ADR-056: the ``scan --artifact-set`` entry point into the bundle layer.
    ``libraries`` is expected to already be collision-free and ELF-validated
    (:func:`discover_artifact_set`) — this function does not re-validate
    that, it only builds the snapshot and runs the audit-mode detector.
    """
    snapshot = build_bundle_snapshot(libraries)
    # P2 regression (Codex review): two distinct set members advertising the
    # same DT_SONAME make provider resolution genuinely ambiguous --
    # provider_library_for_soname() (bundle_models.py) returns the first
    # metadata match, while _compute_resolution_graph()'s reverse-soname map
    # keeps the *last* match, so the same DT_NEEDED edge is classified
    # against two different candidate providers by two different call
    # sites. Rather than silently guessing (either a false "unresolved"
    # finding when the picked provider doesn't actually export the symbol,
    # or a false negative if it happens to), reject the ambiguity outright
    # -- mirroring discover_artifact_set()'s own collision rejection for
    # duplicate canonical names.
    duplicate_sonames = _find_duplicate_sonames(snapshot.metadata)
    if duplicate_sonames:
        detail = "; ".join(
            f"'{soname}': {names}" for soname, names in duplicate_sonames.items()
        )
        raise ArtifactSetError(
            f"--artifact-set has ambiguous duplicate SONAME provider(s): "
            f"{detail}. Each library in an artifact set must advertise a "
            "distinct DT_SONAME; rename or drop the duplicate(s)."
        )
    sys_providers = set(DEFAULT_SYSTEM_PROVIDERS) | set(bundle_system_providers)
    findings = _detect_unresolved_intra_dependency(snapshot, sys_providers)
    return BundleAuditResult(snapshot=snapshot, findings=findings)


def _find_duplicate_sonames(
    metadata: dict[str, ElfMetadata],
) -> dict[str, list[str]]:
    """Return ``{soname: [library_names]}`` for every non-empty DT_SONAME
    shared by 2+ distinct libraries in *metadata* (empty dict if none)."""
    by_soname: dict[str, list[str]] = {}
    for name, meta in metadata.items():
        if meta.soname:
            by_soname.setdefault(meta.soname, []).append(name)
    return {soname: names for soname, names in by_soname.items() if len(names) > 1}


def render_bundle_findings_markdown(findings: list[BundleFinding]) -> list[str]:
    """Markdown lines for a list of bundle findings (G34 Phase 4).

    Shared by ``cli_compare_release_helpers._release_md_bundle_findings``
    (:class:`BundleDiffResult`'s two-sided findings) and
    ``cli_scan._render_artifact_set_text`` (:class:`BundleAuditResult`'s
    single-sided ``scan --artifact-set`` findings, ADR-056) — the rendering
    itself only ever needs the flat ``list[BundleFinding]``, never the
    wrapper object, so one function covers both call sites. Returns ``[]``
    for an empty list (the caller decides whether/how to still render a
    section heading for "no findings").
    """
    lines: list[str] = []
    for f in findings:
        # Library-scoped findings (bundle_library_added /
        # bundle_library_removed) carry the library name in `symbol`;
        # manifest/import findings carry the symbol. Both are non-empty in
        # practice, but guard against future finding shapes with no attribution.
        lines.append(
            f"- **{f.kind.value}**"
            + (f" — `{f.symbol}`" if f.symbol else "")
            + (f" (consumer: `{f.consumer_library}`)" if f.consumer_library else "")
            + (f" (provider: `{f.provider_library}`)" if f.provider_library else ""),
        )
        lines.append(f"  - {f.description}")
    return lines


def _compute_resolution_graph(
    libraries: dict[str, Path],
    metadata: dict[str, ElfMetadata],
    *,
    probe_filesystem: bool = True,
    extra_aliases: dict[str, tuple[str, ...]] | None = None,
    probe_filesystem_names: frozenset[str] | None = None,
) -> ResolutionGraph:
    """Index exports/imports across every library in the bundle.

    A symbol is recorded as "intra-bundle imported" if its consumer's
    ``DT_NEEDED`` list contains a soname that resolves to another library
    in this bundle (or if the symbol itself is provided by another
    library in this bundle — covers the case where the linker dropped a
    DT_NEEDED line but the import is still in .dynsym).

    *probe_filesystem*: when ``True`` (the default, used by
    :func:`build_bundle_snapshot`'s real-path callers), also resolves each
    library's symlink target and scans its directory for hard-linked
    aliases (see the two filesystem calls below) to recover soname
    spellings a purely metadata-driven index would miss. Set ``False`` for
    a *synthesized* ``libraries`` map (see
    :func:`build_bundle_snapshot_from_metadata`) — a bare
    ``Path(library_name)`` is a relative path with no real file behind it,
    so ``.resolve()`` silently resolves against the *process's current
    working directory* instead, and the hard-link scan walks whatever
    directory that resolves to. Metadata-only resolution must not depend
    on ambient filesystem state that happens to share a name with a
    library (Codex review, fresh evidence: an unrelated ``libfoo.so ->
    libfoo.so.1`` symlink sitting in the caller's cwd could silently
    change which DT_NEEDED edges resolve as intra-bundle).

    *probe_filesystem_names*: further restricts *which* names the two
    filesystem calls above run for, when ``probe_filesystem`` is ``True``.
    ``None`` (every direct caller except :func:`build_bundle_snapshot_from_metadata`)
    means no restriction — probe every name present in *libraries*, the
    original behavior. :func:`build_bundle_snapshot_from_metadata` passes
    an explicit set so a caller that mixes real, live paths with paths that
    merely *carry a real filename for display* (recovered, not
    filesystem-resolvable — :func:`build_bundle_snapshot_mixed`'s stored
    entries) doesn't extend the live-filesystem probe to those too (Codex
    review, security finding: same ambient-cwd-dependence concern as
    ``probe_filesystem=False`` above, just reachable through a *subset* of
    ``libraries`` instead of all of it).
    """
    graph = ResolutionGraph()

    # Build soname -> library_name reverse map for DT_NEEDED resolution.
    soname_to_name: dict[str, str] = {}
    for name, meta in metadata.items():
        if meta.soname:
            soname_to_name[meta.soname] = name
        # Also map the canonical key itself so a missing SONAME doesn't hide
        # siblings when a consumer's DT_NEEDED happens to spell the
        # canonical form verbatim.
        soname_to_name.setdefault(name, name)
        # And the library's *actual* on-disk filename (e.g. "libfoo.so.1")
        # -- distinct from the canonical key (e.g. "libfoo.so") whenever the
        # library is versioned. Without a DT_SONAME, a sibling's DT_NEEDED
        # entry names this real filename verbatim; indexing only the
        # canonical key left that edge unresolved, misclassifying a real
        # intra-bundle DT_NEEDED as "extra" (external) and breaking
        # reachability for consumers of that provider (Codex review).
        #
        # ``libraries[name]`` is the discovered path, which for the common
        # ``libfoo.so -> libfoo.so.1`` symlink pair is the *symlink itself*
        # (discover_artifact_set/directory discovery sort alphabetically and
        # keep the first-seen alias, i.e. the unversioned symlink, as the
        # representative path) -- so ``.name`` on it is just "libfoo.so"
        # again, not the real target's filename. Resolve through the
        # symlink to index the actual on-disk basename a sibling's
        # DT_NEEDED would name (Codex review: the original fix only worked
        # when the discovered path already *was* the real file).
        if name in libraries:
            soname_to_name.setdefault(libraries[name].name, name)
            if probe_filesystem and (
                probe_filesystem_names is None or name in probe_filesystem_names
            ):
                try:
                    resolved_name = libraries[name].resolve().name
                except OSError:
                    resolved_name = libraries[name].name
                soname_to_name.setdefault(resolved_name, name)
                # A provider can also have *hard-linked* aliases -- distinct
                # directory entries sharing one inode, common for a
                # ``libfoo.so.1``/``libfoo.so.1.0.0`` pair. discover_artifact_
                # set()'s own dedup keeps only one representative path per
                # inode, so an alias basename a consumer's DT_NEEDED names is
                # otherwise never indexed here at all -- the provider reads as
                # unreachable and the audit emits a false
                # bundle_unresolved_intra_dependency (Codex review, fresh
                # evidence). Recover the discarded aliases by scanning the
                # representative path's own directory for siblings sharing its
                # (st_dev, st_ino) identity and indexing each one's basename
                # too -- self-contained here, no need to thread alias lists
                # through discover_artifact_set's public dict[str, Path] return
                # type.
                for alias in hard_link_alias_basenames(libraries[name]):
                    soname_to_name.setdefault(alias, name)

    for name, aliases in (extra_aliases or {}).items():  # G38 Phase 2 replay
        # A name whose own snapshot had no ELF metadata (AbiSnapshot.elf is
        # None -- non-ELF or header-only) is dropped before this function
        # is ever called (bundle_snapshot_from_facts()), but its captured
        # aliases still ride along in extra_aliases unconditionally. Indexing
        # one anyway would resolve a consumer's DT_NEEDED to a bundle member
        # that doesn't actually exist in this snapshot, misclassifying a
        # real "extra" (external/unresolved) edge as intra-bundle -- unlike
        # a live build_bundle_snapshot(), which never has an alias for a
        # provider it didn't parse in the first place (Codex review, fresh
        # evidence).
        if name not in metadata:
            continue
        soname_to_name.update({a: name for a in aliases if a not in soname_to_name})
    graph.soname_to_name = soname_to_name

    # Index exports.
    for name, meta in metadata.items():
        for sym in meta.symbols:
            if sym.visibility not in ("default", "protected"):
                continue
            graph.provides.setdefault(sym.name, []).append(
                ProviderEntry(
                    library=name, version=sym.version, is_default=sym.is_default
                ),
            )

    # Index DT_NEEDED edges and intra-bundle imports.
    for name, meta in metadata.items():
        intra: list[str] = []
        extra: list[str] = []
        for needed in meta.needed:
            if needed in soname_to_name and soname_to_name[needed] != name:
                intra.append(needed)
            else:
                extra.append(needed)
        graph.intra_needed[name] = intra
        graph.extra_needed[name] = extra

        for imp in meta.imports:
            graph.consumers.setdefault(imp.name, []).append(
                ConsumerEntry(
                    library=name,
                    version=imp.version,
                    weak=str(imp.binding) in ("SymbolBinding.WEAK", "weak"),
                    version_soname=imp.version_soname,
                ),
            )

    return graph


# ---------------------------------------------------------------------------
# Bundle diff
# ---------------------------------------------------------------------------


def compare_bundle(
    old: BundleSnapshot,
    new: BundleSnapshot,
    per_library_results: list[DiffResult],
    *,
    manifest: InstantiationManifest | None = None,
    system_providers: Iterable[str] | None = None,
    cohorts: list[str] | None = None,
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
) -> BundleDiffResult:
    """Compute bundle-level findings from per-library diffs and bundle snapshots.

    Args:
        old: Bundle snapshot of the old release.
        new: Bundle snapshot of the new release.
        per_library_results: Output of running :func:`abicheck.checker.compare`
            on each matched library pair. Not modified.
        manifest: Optional :class:`InstantiationManifest` to enforce.
            When supplied, missing promised symbols become
            ``BUNDLE_MANIFEST_INSTANTIATION_REMOVED`` findings.
        system_providers: Sonames to treat as system-provided (extends
            :data:`DEFAULT_SYSTEM_PROVIDERS`).
        cohorts: Explicit co-versioned cohort prefixes (e.g. ``"libfoo_"``)
            for the opt-in ``BUNDLE_SONAME_SKEW`` check. When empty/None the
            skew check is disabled — cohorts are never inferred from filenames.
        policy: The same policy profile name :func:`abicheck.checker_policy.
            compute_verdict` accepts, applied to *bundle-level* findings when
            :attr:`BundleDiffResult.bundle_verdict`/``.verdict`` are read.
            Previously unparameterized — every caller's bundle verdict was
            always scored under the hardcoded ``strict_abi`` default even
            when the caller explicitly selected a different policy for its
            per-library comparisons, so a policy that reclassifies a bundle
            kind (e.g. ``plugin_abi`` promoting a risk-classified
            ``bundle_provider_changed`` to breaking) never reached the
            aggregate verdict (Codex review, fresh evidence). Defaults to
            ``strict_abi``, matching every existing caller's prior behavior
            exactly.
    """
    explicit_providers = set(system_providers or ())
    sys_libs = set(DEFAULT_SYSTEM_PROVIDERS) | explicit_providers
    findings: list[BundleFinding] = []

    # Index per-library diff results by the resolution graph's own
    # bundle-canonical key, not the raw (possibly SONAME-versioned)
    # on-disk basename -- see `basename_to_bundle_key`'s own docstring
    # (G38 plan doc Phase 5). Canonicalise once instead of double-indexing.
    #
    # Merge OLD+NEW maps (Codex review): `checker.compare()` sets
    # `DiffResult.library = old.library`, so a versioned basename that
    # changed between old and new (`libcore.so.1.2` -> `.1.3`) only
    # resolves through `old`'s own map. New wins a collision -- it's what
    # the resolution graph these keys feed into was built from.
    basename_to_key = {**basename_to_bundle_key(old), **basename_to_bundle_key(new)}
    diff_by_library: dict[str, DiffResult] = {}
    for result in per_library_results:
        basename = Path(result.library).name
        canonical = basename_to_key.get(basename, basename)
        diff_by_library.setdefault(canonical, result)

    # 1. bundle_library_removed / bundle_library_added (structural)
    findings.extend(_detect_library_structural_changes(old, new))

    # 2. bundle_intra_dep_removed: an import in the new bundle has no provider.
    findings.extend(_detect_intra_dep_removed(old, new, sys_libs, explicit_providers))

    # 3. bundle_intra_dep_signature_changed: a promotable confirmed change
    #    on a symbol some sibling imports (see that function's docstring).
    findings.extend(_detect_intra_dep_signature_changed(new, diff_by_library, policy))

    # 4. bundle_intra_type_changed: a type_*_changed touches a type that
    #    appears in a public symbol of a sibling library.
    findings.extend(_detect_intra_type_changed(old, new, diff_by_library))

    # 5. bundle_provider_changed: same mangled name appears as func_removed
    #    in library A's diff AND func_added in library B's diff.
    findings.extend(_detect_provider_changed(new, diff_by_library))

    # 6. bundle_intra_dep_resolved_to_different_version: same symbol but
    #    different gnu.version_d between old and new providers.
    findings.extend(_detect_version_drift(old, new))

    # 7. bundle_soname_skew: declared co-versioned cohort members bumped
    #    their major SONAME inconsistently (some bumped, some lagged). A
    #    cohort-level invariant: no individual library is wrong, but the set
    #    is. Opt-in only — runs solely for the cohorts the caller declares
    #    (compare-release --bundle-cohort). See examples/case84_bundle_soname_skew/.
    findings.extend(_detect_soname_skew(old, new, cohorts))

    # 8. Manifest enforcement
    if manifest is not None:
        findings.extend(_detect_manifest_drift(old, new, manifest))

    return BundleDiffResult(
        old_root=old.root,
        new_root=new.root,
        per_library=list(per_library_results),
        bundle_findings=findings,
        policy=policy,
        policy_file=policy_file,
    )
