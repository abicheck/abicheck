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
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

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
)
from .bundle_soname import hard_link_alias_basenames, soname_matches_providers
from .checker_policy import ChangeKind, Verdict, compute_verdict
from .checker_types import DiffResult
from .elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, parse_elf_metadata

if TYPE_CHECKING:
    from .diff_cpp_patterns import BundleMember

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


def build_bundle_snapshot_from_metadata(
    metadata: dict[str, ElfMetadata],
    *,
    paths: dict[str, Path] | None = None,
    root: Path | None = None,
    probe_filesystem: bool = False,
    extra_aliases: dict[str, tuple[str, ...]] | None = None,
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
            if probe_filesystem:
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

    # Index per-library diff results by canonical basename. This is the
    # same key the resolution graph uses for libraries (see
    # build_bundle_snapshot's `libraries` dict), so look-ups in the
    # detectors agree. We canonicalise once instead of double-indexing —
    # double-indexing caused detectors to iterate the same DiffResult
    # twice when DiffResult.library happened to differ from its basename.
    diff_by_library: dict[str, DiffResult] = {}
    for result in per_library_results:
        canonical = Path(result.library).name
        diff_by_library.setdefault(canonical, result)

    # 1. bundle_library_removed / bundle_library_added (structural)
    findings.extend(_detect_library_structural_changes(old, new))

    # 2. bundle_intra_dep_removed: an import in the new bundle has no provider.
    findings.extend(_detect_intra_dep_removed(old, new, sys_libs, explicit_providers))

    # 3. bundle_intra_dep_signature_changed: provider's per-library diff
    #    flagged func_params_changed / func_return_changed / var_type_changed
    #    on a symbol some sibling imports.
    findings.extend(_detect_intra_dep_signature_changed(new, diff_by_library))

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
    )


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


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
            # Known limitation (Codex review, pre-existing -- shared by the
            # audit-mode sibling below): this allow-list match is absence of
            # a *bundle* regression, not proof the symbol is exported by a
            # system DSO -- neither detector parses a system library's own
            # export table, only its soname, so a genuinely never-provided
            # symbol (typo, forgotten dependency) is suppressed the same
            # way a real system-provided one is. Fixing this needs an
            # actual export-table probe of each allow-listed DSO.
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


def _reachable_intra_libraries(snapshot: BundleSnapshot, root: str) -> set[str]:
    """BFS over intra-bundle ``DT_NEEDED`` edges starting at ``root``.

    Returns every library transitively reachable from ``root`` through the
    bundle's own resolution graph (i.e. what would actually be loaded when
    ``root`` is loaded) — not including ``root`` itself. Used by
    :func:`_detect_unresolved_intra_dependency` and
    :func:`_detect_intra_dep_removed` so a symbol is only considered
    resolved (or previously reached) by a provider the consumer can
    actually reach, not merely one present somewhere else in the snapshot.
    """
    seen: set[str] = set()
    queue = [root]
    while queue:
        lib = queue.pop()
        for soname in snapshot.resolution.intra_needed.get(lib, []):
            # Resolve via the same soname_to_name map
            # _compute_resolution_graph() used to classify this edge as
            # intra in the first place, so the two agree even for a library
            # discovered via a differently-named symlink alias.
            target = snapshot.resolution.soname_to_name.get(soname)
            if target is None or target == lib or target in seen:
                continue
            seen.add(target)
            queue.append(target)
    return seen


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


def _detect_intra_dep_signature_changed(
    new: BundleSnapshot,
    diff_by_library: dict[str, DiffResult],
) -> list[BundleFinding]:
    """Promote provider-side signature changes to consumer-side findings.

    For each per-library ``func_params_changed`` / ``func_return_changed``
    / ``var_type_changed``, look up which siblings import that symbol in
    the new bundle and emit one finding per (consumer, symbol) pair.
    Multiple change kinds against the same symbol collapse into one
    finding to avoid double-counting params+return changes.
    """
    findings: list[BundleFinding] = []
    seen: set[tuple[str, str, str]] = set()
    relevant_kinds = {
        ChangeKind.FUNC_PARAMS_CHANGED,
        ChangeKind.FUNC_RETURN_CHANGED,
        ChangeKind.VAR_TYPE_CHANGED,
    }
    for provider_lib, diff in diff_by_library.items():
        for change in diff.changes:
            if change.kind not in relevant_kinds:
                continue
            consumers = new.resolution.consumers_of(change.symbol)
            consumer_libs = sorted(
                {c.library for c in consumers if c.library != provider_lib}
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
    work — out of scope for ADR-023 first cut).

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
        for change in diff.changes:
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
    findings into one ``BUNDLE_PROVIDER_CHANGED`` event.
    """
    findings: list[BundleFinding] = []

    removed_by: dict[str, str] = {}  # symbol -> library that removed it
    added_by: dict[str, str] = {}  # symbol -> library that added it
    for lib_name, diff in diff_by_library.items():
        for change in diff.changes:
            if change.kind in (
                ChangeKind.FUNC_REMOVED,
                ChangeKind.VAR_REMOVED,
            ):
                removed_by.setdefault(change.symbol, lib_name)
            elif change.kind in (
                ChangeKind.FUNC_ADDED,
                ChangeKind.VAR_ADDED,
            ):
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
    for providers in old.resolution.provides.values():
        pass  # symbols are keys
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
        members: list[BundleMember] = []
        for name, path in snap.libraries.items():
            meta = snap.metadata.get(name)
            soname = (meta.soname if meta and meta.soname else "") or path.name
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
                major = _extract_soname_major(strip_vendor_hash(path.name))
            if major is None:
                continue
            members.append(
                BundleMember(
                    library=path.name,
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
    """
    from .demangle import demangle as _demangle

    index: list[tuple[str, str]] = []
    for lib_name, meta in snapshot.metadata.items():
        for sym in meta.symbols:
            if sym.visibility not in ("default", "protected"):
                continue
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
        providers = snapshot.resolution.providers_for(target)
        return ([target] if providers else []), providers

    if index is None:
        index = _build_demangled_index(snapshot)

    matched: list[str] = []
    provider_set: set[str] = set()
    for demangled, lib_name in index:
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
    needs_index = any(kind != "symbol" for _, kind in _entry_targets(entry))
    if index is None and needs_index:
        index = _build_demangled_index(snapshot)
    out: list[tuple[str, str, list[str], list[ProviderEntry]]] = []
    for target, kind in _entry_targets(entry):
        matched, providers = _match_target_against_index(target, kind, snapshot, index)
        out.append((target, kind, matched, providers))
    return out


def _detect_manifest_drift(
    old: BundleSnapshot,
    new: BundleSnapshot,
    manifest: InstantiationManifest,
) -> list[BundleFinding]:
    """Enforce a release manifest against the new bundle.

    Decomposes template entries into one virtual target per
    instantiation so each instantiation is checked independently.
    Per-snapshot demangle indexes are built once and reused across
    every manifest entry — manifest enforcement scales O(symbols +
    Σtargets) rather than O(symbols × Σtargets).

    For each target:
      - If no exported symbol matches → ``BUNDLE_MANIFEST_INSTANTIATION_REMOVED``.
      - If matched but at the wrong provider (when ``optional_provider=False``)
        → ``BUNDLE_MANIFEST_INSTANTIATION_REMOVED`` (contract names the lib).

    Symbols in the new bundle but not in the manifest are not flagged
    here (out-of-manifest exports are not necessarily promised).
    """
    findings: list[BundleFinding] = []
    # Build the per-snapshot demangle indexes once; both the
    # "missing in new" and "newly promised" passes reuse them.
    new_index = _build_demangled_index(new)
    old_index = _build_demangled_index(old)

    for entry in manifest.entries:
        for target, kind, matched, providers in _match_entry(entry, new, new_index):
            if not matched:
                findings.append(
                    BundleFinding(
                        kind=ChangeKind.BUNDLE_MANIFEST_INSTANTIATION_REMOVED,
                        symbol=target,
                        description=(
                            f"Manifest promises {kind} {target!r} but no "
                            f"exported symbol in the new bundle matches it."
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
                    meta = new.metadata.get(prov.library)
                    return meta is not None and meta.soname == _entry.library

                if not any(_matches(p) for p in providers):
                    got = ", ".join(sorted(p.library for p in providers))
                    findings.append(
                        BundleFinding(
                            kind=ChangeKind.BUNDLE_MANIFEST_INSTANTIATION_REMOVED,
                            symbol=target,
                            description=(
                                f"Manifest requires {kind} {target!r} to be "
                                f"provided by {entry.library}, but it is "
                                f"provided by {got} instead."
                            ),
                            provider_library=entry.library,
                            new_value=got,
                        ),
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
