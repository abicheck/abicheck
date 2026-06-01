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
    - :func:`compare_bundle`      — main entry point.

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
from typing import TYPE_CHECKING, cast

from .checker_policy import ChangeKind, Verdict, compute_verdict
from .checker_types import Change, DiffResult
from .elf_metadata import ElfMetadata, parse_elf_metadata

if TYPE_CHECKING:
    from .diff_cpp_patterns import BundleMember

log = logging.getLogger(__name__)


# Symbols imported by virtually every C/C++ shared library that are
# provided by the system loader, not by the bundle. Resolution against the
# bundle is meaningless for these; ignore unresolved imports against this
# set when emitting :class:`ChangeKind.BUNDLE_INTRA_DEP_REMOVED`.
DEFAULT_SYSTEM_PROVIDERS: frozenset[str] = frozenset({
    "libc.so.6", "libc.so.7",
    "libm.so.6",
    "libdl.so.2",
    "libpthread.so.0",
    "librt.so.1",
    "libstdc++.so.6",
    "libc++.so.1", "libc++abi.so.1",
    "libgcc_s.so.1",
    "libgomp.so.1",
    "libtbb.so.12", "libtbb.so.2",
    "libsycl.so", "libsycl.so.7", "libsycl.so.8",
    "libOpenCL.so.1",
    "libz.so.1",
    "ld-linux-x86-64.so.2", "ld-linux-aarch64.so.1",
})


@dataclass(frozen=True)
class ProviderEntry:
    """One library in the bundle that exports ``symbol``."""

    library: str          # e.g. "libcore.so"
    version: str          # gnu.version_d tag, "" if unversioned


@dataclass(frozen=True)
class ConsumerEntry:
    """One library in the bundle that imports ``symbol``."""

    library: str          # e.g. "libalgo.so"
    version: str          # gnu.version_r required version, "" if unversioned
    weak: bool            # True when the import is weak (unresolved is OK)


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

    def providers_for(self, symbol: str) -> list[ProviderEntry]:
        return list(self.provides.get(symbol, ()))

    def consumers_of(self, symbol: str) -> list[ConsumerEntry]:
        return list(self.consumers.get(symbol, ()))


@dataclass
class BundleSnapshot:
    """A release directory captured as a bundle.

    Holds per-library ELF metadata and the precomputed resolution graph.
    """

    root: Path                                      # the release directory
    libraries: dict[str, Path]                      # library_name -> filesystem path
    metadata: dict[str, ElfMetadata]                # library_name -> parsed ELF metadata
    resolution: ResolutionGraph

    @property
    def library_names(self) -> list[str]:
        return sorted(self.libraries.keys())

    def is_intra_bundle_provider(self, soname: str) -> bool:
        """Return True if ``soname`` matches a library inside this bundle.

        Matches on either the raw filename (``libfoo.so``) or the soname
        encoded by the library (``libfoo.so.1``).
        """
        if soname in self.libraries:
            return True
        for name, meta in self.metadata.items():
            if meta.soname == soname:
                return True
            # Allow filename-stem fallback (libfoo.so matches libfoo.so.1)
            if soname.startswith(name + "."):
                return True
            if name.startswith(soname + "."):
                return True
        return False


@dataclass
class BundleFinding:
    """A single bundle-level finding.

    Mirrors :class:`Change` so the same reporter / suppression / severity
    machinery can consume bundle findings without special-casing. The
    ``consumer_library`` / ``provider_library`` fields distinguish bundle
    findings from per-library changes.
    """

    kind: ChangeKind
    symbol: str                              # mangled symbol name or type name
    description: str
    consumer_library: str | None = None      # affected library (for intra-dep findings)
    provider_library: str | None = None      # source-of-change library
    old_value: str | None = None
    new_value: str | None = None
    affected_libraries: list[str] = field(default_factory=list)

    def to_change(self) -> Change:
        """Lower a :class:`BundleFinding` into the :class:`Change` representation.

        Used by the JSON/Markdown reporters that already know how to render
        ``Change`` objects. The bundle attribution fields are flattened into
        ``description`` so they survive the lowering.
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
        )


@dataclass
class BundleDiffResult:
    """Output of :func:`compare_bundle`.

    Bundle findings are kept distinct from per-library diff results so a
    consumer (reporter, JSON output) can render them under their own
    section. The aggregate ``verdict`` is the worst of (worst per-library
    verdict, ``bundle_verdict``).
    """

    old_root: Path
    new_root: Path
    per_library: list[DiffResult] = field(default_factory=list)
    bundle_findings: list[BundleFinding] = field(default_factory=list)

    @property
    def bundle_verdict(self) -> Verdict:
        changes = [f.to_change() for f in self.bundle_findings]
        return compute_verdict(changes)

    @property
    def per_library_verdict(self) -> Verdict:
        order = [
            Verdict.NO_CHANGE, Verdict.COMPATIBLE, Verdict.COMPATIBLE_WITH_RISK,
            Verdict.API_BREAK, Verdict.BREAKING,
        ]
        worst = Verdict.NO_CHANGE
        for r in self.per_library:
            if order.index(r.verdict) > order.index(worst):
                worst = r.verdict
        return worst

    @property
    def verdict(self) -> Verdict:
        order = [
            Verdict.NO_CHANGE, Verdict.COMPATIBLE, Verdict.COMPATIBLE_WITH_RISK,
            Verdict.API_BREAK, Verdict.BREAKING,
        ]
        return max(self.per_library_verdict, self.bundle_verdict, key=order.index)


# ---------------------------------------------------------------------------
# Manifest input
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ManifestEntry:
    """One promised entry in an :class:`InstantiationManifest`.

    Exactly one of ``symbol``, ``pattern``, or ``template`` is set.

    - ``symbol`` — a literal mangled symbol name. Matched by equality
      against the bundle's exported-symbol set. Useful when the contract
      genuinely is one specific symbol (versioned entry points, dlsym
      plugin contracts).
    - ``pattern`` — a glob (``fnmatch`` semantics, with ``*`` and ``?``)
      matched against the *demangled* form of every exported symbol.
      Match is found-iff any exported symbol's demangled form matches
      the glob. Best for "any train_ops<*> for these algorithm classes"
      type promises that headers don't capture as a contract.
    - ``template`` — a C++ qualified template name plus an
      ``instantiations`` list of parameter assignments. abicheck
      expands each assignment into a substring of the demangled form
      ``Template<v1, v2, ...>`` and looks for at least one exported
      symbol whose demangled name contains it. The natural shape for
      libraries (for example oneDAL, libtorch, or MKL) that maintain an
      explicit instantiation matrix in their build system.
    """

    symbol: str | None = None                    # literal mangled symbol
    pattern: str | None = None                   # fnmatch glob on demangled form
    template: str | None = None                  # C++ qualified template name
    instantiations: tuple[dict[str, str], ...] = ()  # for template form
    library: str | None = None                   # provider when optional_provider=False
    optional_provider: bool = True               # True = any sibling may provide

    def kind(self) -> str:
        """Return ``'symbol'``, ``'pattern'``, or ``'template'`` for diagnostics."""
        if self.symbol is not None:
            return "symbol"
        if self.pattern is not None:
            return "pattern"
        return "template"

    def display_name(self) -> str:
        """Best human-readable identifier for the entry (used in findings).

        For template entries, expands the instantiations into the same
        ``Template<arg1, arg2>`` form the matcher uses so the finding
        actually identifies *which* parameter set failed — otherwise
        users would see ``Template`` and have no idea which instantiation
        was missing.
        """
        if self.symbol is not None:
            return self.symbol
        if self.pattern is not None:
            return self.pattern
        if self.template is not None and self.instantiations:
            expanded = _expand_instantiations(self.template, self.instantiations)
            return ", ".join(expanded)
        return self.template or "<empty>"


@dataclass(frozen=True)
class InstantiationManifest:
    """A list of symbols a release publicly promises to ship.

    Loaded from a YAML/JSON file via :func:`load_manifest`. The bundle
    layer enforces that every entry has at least one matching exported
    symbol in the new bundle (or at the named provider when
    ``optional_provider=False``).
    """

    entries: tuple[ManifestEntry, ...]

    @property
    def symbols(self) -> frozenset[str]:
        """Literal-symbol entries only (back-compat for existing callers)."""
        return frozenset(e.symbol for e in self.entries if e.symbol is not None)


def _expand_instantiations(template: str, instantiations: tuple[dict[str, str], ...]) -> list[str]:
    """Build demangled-form substring patterns from a template + parameter list.

    Returns a list of strings like ``"acme::lib::train_ops<float, method::dense, task::train>"``
    that the matcher tests as substring against the demangled form of
    each exported symbol. Parameter order in the produced angle-bracket
    list is the iteration order of the dict (insertion order, preserved
    in Python 3.7+). YAML/JSON manifests therefore declare parameters
    in the same order the template's parameter list takes them.
    """
    expanded: list[str] = []
    for inst in instantiations:
        args = ", ".join(str(v) for v in inst.values())
        expanded.append(f"{template}<{args}>")
    return expanded


def _load_manifest_data(path: Path) -> dict[str, object]:
    """Read and parse YAML or JSON manifest file; validate top-level shape."""
    import json

    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        import yaml
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("provides"), list):
        raise ValueError(f"manifest {path}: missing top-level 'provides:' list")
    return cast("dict[str, object]", data)


def _validate_manifest_entry_shape(path: Path, raw: dict[str, object]) -> str:
    """Validate that *raw* has exactly one of symbol/pattern/template; return it."""
    shape_keys = [k for k in ("symbol", "pattern", "template") if k in raw]
    if len(shape_keys) == 0:
        raise ValueError(
            f"manifest {path}: entry must have one of 'symbol', "
            f"'pattern', or 'template': {raw!r}",
        )
    if len(shape_keys) > 1:
        raise ValueError(
            f"manifest {path}: entry has conflicting fields "
            f"{shape_keys!r}; pick exactly one: {raw!r}",
        )
    return shape_keys[0]


def _parse_template_instantiations(path: Path, raw: dict[str, object]) -> tuple[dict[str, str], ...]:
    """Parse and coerce the 'instantiations' list from a template entry."""
    insts_raw = raw.get("instantiations", [])
    if not isinstance(insts_raw, list) or not insts_raw:
        raise ValueError(
            f"manifest {path}: template entry needs a non-empty "
            f"'instantiations:' list: {raw!r}",
        )
    insts: list[dict[str, str]] = []
    for inst in insts_raw:
        if not isinstance(inst, dict):
            raise ValueError(
                f"manifest {path}: each instantiation must be a "
                f"mapping of parameter name to value: {inst!r}",
            )
        # Preserve dict insertion order from YAML/JSON; coerce
        # values to str so YAML's `true`/`false`/numbers render
        # correctly in the expanded template signature.
        insts.append({str(k): str(v) for k, v in inst.items()})
    return tuple(insts)


def _parse_manifest_entry(path: Path, raw: dict[str, object]) -> ManifestEntry:
    """Convert one raw mapping from a manifest 'provides' list into a :class:`ManifestEntry`."""
    if not isinstance(raw, dict):
        raise ValueError(f"manifest {path}: entry is not a mapping: {raw!r}")
    shape = _validate_manifest_entry_shape(path, raw)
    optional_provider = raw.get("optional_provider", True)
    if not isinstance(optional_provider, bool):
        raise ValueError(
            f"manifest {path}: 'optional_provider' must be a boolean "
            f"(got {type(optional_provider).__name__} {optional_provider!r}): {raw!r}",
        )
    library = str(raw["library"]) if raw.get("library") else None
    if shape == "template":
        insts = _parse_template_instantiations(path, raw)
        return ManifestEntry(
            template=str(raw["template"]),
            instantiations=insts,
            library=library,
            optional_provider=optional_provider,
        )
    if shape == "pattern":
        return ManifestEntry(
            pattern=str(raw["pattern"]),
            library=library,
            optional_provider=optional_provider,
        )
    return ManifestEntry(
        symbol=str(raw["symbol"]),
        library=library,
        optional_provider=optional_provider,
    )


def load_manifest(path: Path) -> InstantiationManifest:
    """Load a manifest from YAML (``.yaml``/``.yml``) or JSON.

    Format (all three entry shapes are accepted; exactly one of
    ``symbol`` / ``pattern`` / ``template`` per entry)::

        version: 1
        provides:
          # 1. Literal symbol — exact match against .dynsym.
          - symbol: acme_lib_version
            library: libfoo_core.so.1
            optional_provider: false

          # 2. Glob pattern — fnmatch against demangled form.
          - pattern: "acme::lib::detail::train_kernel<*>*"
            library: libfoo_core.so.1
            optional_provider: false

          # 3. Template + instantiations — natural shape for template libs.
          - template: acme::lib::train_ops
            instantiations:
              - {Float: float,  Method: "method::dense",  Task: "task::train"}
              - {Float: float,  Method: "method::sparse", Task: "task::train"}
              - {Float: double, Method: "method::dense",  Task: "task::train"}
            library: libfoo_core.so.1
            optional_provider: false
    """
    data = _load_manifest_data(path)
    provides = cast("list[dict[str, object]]", data["provides"])
    entries = [_parse_manifest_entry(path, raw) for raw in provides]
    return InstantiationManifest(entries=tuple(entries))


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
    """
    metadata: dict[str, ElfMetadata] = {}
    surviving: dict[str, Path] = {}
    for name, path in libraries.items():
        # Bundle analysis is Linux/ELF-only by design (see ADR-018,
        # ADR-023). Skip JSON snapshots, PE/Mach-O, or other formats up
        # front so parse_elf_metadata never emits its "Magic number does
        # not match" warning on legitimately-non-ELF inputs.
        if not _path_looks_like_elf(path):
            log.debug("bundle: skipping non-ELF input %s", path)
            continue
        try:
            meta = parse_elf_metadata(path)
        except Exception as exc:  # pragma: no cover — parse_elf_metadata already swallows most
            log.warning("bundle: failed to parse %s: %s", path, exc)
            continue
        if meta is None or (not meta.soname and not meta.symbols and not meta.imports and not meta.needed):
            log.debug("bundle: skipping non-ELF or empty input %s", path)
            continue
        metadata[name] = meta
        surviving[name] = path

    resolution = _compute_resolution_graph(surviving, metadata)
    # Use the first library's parent as the root if available; otherwise empty path
    root = next(iter(surviving.values())).parent if surviving else Path()
    return BundleSnapshot(
        root=root,
        libraries=surviving,
        metadata=metadata,
        resolution=resolution,
    )


def _compute_resolution_graph(
    libraries: dict[str, Path],
    metadata: dict[str, ElfMetadata],
) -> ResolutionGraph:
    """Index exports/imports across every library in the bundle.

    A symbol is recorded as "intra-bundle imported" if its consumer's
    ``DT_NEEDED`` list contains a soname that resolves to another library
    in this bundle (or if the symbol itself is provided by another
    library in this bundle — covers the case where the linker dropped a
    DT_NEEDED line but the import is still in .dynsym).
    """
    graph = ResolutionGraph()

    # Build soname -> library_name reverse map for DT_NEEDED resolution.
    soname_to_name: dict[str, str] = {}
    for name, meta in metadata.items():
        if meta.soname:
            soname_to_name[meta.soname] = name
        # Also map raw filename so a missing SONAME doesn't hide siblings.
        soname_to_name.setdefault(name, name)

    # Index exports.
    for name, meta in metadata.items():
        for sym in meta.symbols:
            if sym.visibility not in ("default", "protected"):
                continue
            graph.provides.setdefault(sym.name, []).append(
                ProviderEntry(library=name, version=sym.version),
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
    """
    sys_libs = set(DEFAULT_SYSTEM_PROVIDERS) | set(system_providers or ())
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
    findings.extend(_detect_intra_dep_removed(new, sys_libs))

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
    new: BundleSnapshot,
    system_providers: set[str],
) -> list[BundleFinding]:
    """Find imports in the new bundle that no sibling provides.

    Excludes imports satisfied by system libraries (``libc``, ``libstdc++``,
    etc.) since they are out of bundle scope by design. Excludes weak
    imports (linker treats unresolved weak as 0/NULL at runtime).
    A consumer's import is treated as system-provided when every DT_NEEDED
    edge it carries that resolves *outside* the bundle is in the
    ``system_providers`` allow-list (built-in plus user-extended via
    ``--bundle-system-providers``).
    """
    findings: list[BundleFinding] = []

    for symbol, consumers in new.resolution.consumers.items():
        providers = new.resolution.providers_for(symbol)
        if providers:
            continue  # someone in the bundle provides it
        # Symbol not provided by any sibling. Is it system?
        for consumer in consumers:
            if consumer.weak:
                continue
            consumer_meta = new.metadata.get(consumer.library)
            if consumer_meta is None:
                continue
            # Note: an earlier version of this code short-circuited here
            # when consumer had no intra-bundle DT_NEEDED edges. That
            # heuristic hid the canonical regression where a refactor
            # drops both the only sibling provider *and* the DT_NEEDED
            # edge that pointed at it — the .dynsym still carries the
            # `U symbol` but no graph evidence remains. The downstream
            # system-symbol allow-list (DEFAULT_SYSTEM_SYMBOLS +
            # `_looks_system_symbol`) is what filters legitimately-
            # external imports; rely on that signal alone.
            # If every non-intra DT_NEEDED of this consumer is on the
            # allow-list (built-in libc/libstdc++/libgcc plus user extras),
            # any unresolved import is assumed to come from outside the
            # bundle. This is what --bundle-system-providers controls.
            extra_needed = new.resolution.extra_needed.get(consumer.library, [])
            if extra_needed and all(
                e in system_providers or _looks_system(e)
                for e in extra_needed
            ):
                # And the symbol itself looks system-shaped (mangled std::,
                # well-known C runtime entry, etc.) — skip the finding.
                if symbol in DEFAULT_SYSTEM_SYMBOLS or _looks_system_symbol(symbol):
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
            consumer_libs = sorted({c.library for c in consumers if c.library != provider_lib})
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
            # Look for the type name embedded in any other library's
            # exported symbol names (mangled C++ symbols include the type).
            crossing_consumers: list[str] = []
            stripped = _strip_namespace_prefix(type_name)
            for sib_name, sib_meta in new.metadata.items():
                if sib_name == provider_lib:
                    continue
                for sym in sib_meta.symbols:
                    if stripped and stripped in sym.name:
                        crossing_consumers.append(sib_name)
                        break
            for consumer_lib in sorted(set(crossing_consumers)):
                key = (consumer_lib, provider_lib, type_name)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    BundleFinding(
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
                    ),
                )
    return findings


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

    removed_by: dict[str, str] = {}        # symbol -> library that removed it
    added_by: dict[str, str] = {}          # symbol -> library that added it
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
            old_members, new_members, cohort_prefix=prefix,
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
    from .diff_cpp_patterns import BundleMember, _extract_soname_major

    def _members(snap: BundleSnapshot) -> list[BundleMember]:
        members: list[BundleMember] = []
        for name, path in snap.libraries.items():
            meta = snap.metadata.get(name)
            soname = (meta.soname if meta and meta.soname else "") or path.name
            major = _extract_soname_major(soname)
            if major is None:
                major = _extract_soname_major(path.name)
            if major is None:
                continue
            members.append(
                BundleMember(library=path.name, soname=soname, soname_major=major)
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
        ProviderEntry(library=name, version="")
        for name in sorted(provider_set)
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
    needs_index = any(
        kind != "symbol"
        for _, kind in _entry_targets(entry)
    )
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
                def _matches(prov: ProviderEntry, _entry: ManifestEntry = entry) -> bool:
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
DEFAULT_SYSTEM_SYMBOLS: frozenset[str] = frozenset({
    "__libc_start_main", "__cxa_atexit", "__cxa_finalize", "__cxa_throw",
    "__gxx_personality_v0", "__stack_chk_fail", "__stack_chk_guard",
    "__tls_get_addr", "__errno_location", "_ITM_registerTMCloneTable",
    "_ITM_deregisterTMCloneTable",
    "abort", "exit", "malloc", "free", "calloc", "realloc",
    "memcpy", "memmove", "memset", "memcmp", "strlen", "strcmp", "strncmp",
    "strcpy", "strncpy", "strdup", "fprintf", "printf", "puts",
    "pthread_once", "pthread_self", "pthread_create", "pthread_join",
})


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
