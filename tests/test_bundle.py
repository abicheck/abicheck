"""Unit tests for the bundle layer (ADR-023, abicheck/bundle.py).

These tests use minimal in-memory ElfMetadata fixtures so they do not need
gcc or castxml. Integration tests that build real .so files from the
examples/case90-93 fixtures live in tests/test_bundle_examples.py.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Phase 3 resolver (scripts/CLAUDE.md, docs/contribute/plans/examples-catalog-split.md).
_REPO_DIR = Path(__file__).resolve().parent.parent
if str(_REPO_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO_DIR / "scripts"))
import example_catalog  # noqa: E402

from abicheck.bundle import (  # noqa: E402
    BundleSnapshot,
    ConsumerEntry,
    InstantiationManifest,
    ManifestEntry,
    ProviderEntry,
    _compute_resolution_graph,
    compare_bundle,
    load_manifest,
)
from abicheck.checker_policy import ChangeKind, Verdict  # noqa: E402
from abicheck.checker_types import Change, DiffResult  # noqa: E402
from abicheck.elf_metadata import ElfImport, ElfMetadata, ElfSymbol  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _meta(
    *,
    soname: str = "",
    needed: list[str] | None = None,
    exports: list[str] | None = None,
    imports: list[str] | None = None,
    export_versions: dict[str, str] | None = None,
    import_versions: dict[str, str] | None = None,
    import_version_sonames: dict[str, str] | None = None,
    versions_required: dict[str, list[str]] | None = None,
) -> ElfMetadata:
    """Construct a minimal ElfMetadata for testing."""
    syms = []
    for name in exports or []:
        syms.append(
            ElfSymbol(
                name=name,
                visibility="default",
                version=(export_versions or {}).get(name, ""),
            )
        )
    imps = []
    for name in imports or []:
        imps.append(
            ElfImport(
                name=name,
                version=(import_versions or {}).get(name, ""),
                version_soname=(import_version_sonames or {}).get(name, ""),
            )
        )
    return ElfMetadata(
        soname=soname or "",
        needed=needed or [],
        symbols=syms,
        imports=imps,
        versions_required=versions_required or {},
    )


def _snapshot(libraries: dict[str, ElfMetadata]) -> BundleSnapshot:
    """Build a BundleSnapshot from in-memory metadata (skips ELF parsing)."""
    libs = {name: Path(f"/fake/{name}") for name in libraries}
    graph = _compute_resolution_graph(libs, libraries)
    return BundleSnapshot(
        root=Path("/fake"),
        libraries=libs,
        metadata=libraries,
        resolution=graph,
    )


def _write_elf_shared_object_stub(path: Path) -> None:
    """Write a minimal, structurally-valid ELF64 shared-object (ET_DYN, no
    program headers) -- enough to pass package._is_elf_shared_object's
    magic/class/type/PT_INTERP-absence checks (discover_artifact_set's
    explicit-list form validates against that, not just the 4-byte magic
    sniff -- Codex review), without needing a real compiled binary.
    """
    import struct

    data = bytearray(64)
    data[0:4] = b"\x7fELF"
    data[4] = 2  # ELFCLASS64
    data[5] = 1  # little-endian
    struct.pack_into("<H", data, 16, 3)  # e_type = ET_DYN
    struct.pack_into("<Q", data, 32, 0)  # e_phoff = 0
    struct.pack_into("<H", data, 56, 0)  # e_phnum = 0
    path.write_bytes(bytes(data))


def _diff(
    library: str, *changes: Change, verdict: Verdict = Verdict.BREAKING
) -> DiffResult:
    return DiffResult(
        old_version="old",
        new_version="new",
        library=library,
        changes=list(changes),
        verdict=verdict,
    )


# ---------------------------------------------------------------------------
# Resolution graph
# ---------------------------------------------------------------------------


class TestResolutionGraph:
    def test_indexes_exports_and_imports(self) -> None:
        meta = {
            "libcore.so": _meta(soname="libcore.so.1", exports=["core_add"]),
            "libalgo.so": _meta(
                soname="libalgo.so.1",
                needed=["libcore.so.1"],
                imports=["core_add"],
            ),
        }
        snap = _snapshot(meta)
        assert snap.resolution.providers_for("core_add") == [
            ProviderEntry(library="libcore.so", version=""),
        ]
        assert snap.resolution.consumers_of("core_add") == [
            ConsumerEntry(library="libalgo.so", version="", weak=False),
        ]
        assert snap.resolution.intra_needed["libalgo.so"] == ["libcore.so.1"]
        assert snap.resolution.intra_needed["libcore.so"] == []

    def test_skips_hidden_visibility(self) -> None:
        # Hidden exports are not part of the public surface.
        meta = ElfMetadata(
            soname="lib.so",
            symbols=[
                ElfSymbol(name="public_func", visibility="default"),
                ElfSymbol(name="hidden_func", visibility="hidden"),
            ],
        )
        snap = _snapshot({"lib.so": meta})
        assert "public_func" in snap.resolution.provides
        assert "hidden_func" not in snap.resolution.provides

    def test_extra_needed_records_system_libs(self) -> None:
        # DT_NEEDED that doesn't match a sibling in the bundle goes into extra.
        meta = {
            "libcore.so": _meta(
                soname="libcore.so", needed=["libc.so.6", "libalgo.so.1"]
            ),
            "libalgo.so": _meta(soname="libalgo.so.1"),
        }
        snap = _snapshot(meta)
        assert "libalgo.so.1" in snap.resolution.intra_needed["libcore.so"]
        assert "libc.so.6" in snap.resolution.extra_needed["libcore.so"]

    def test_dt_needed_resolves_via_real_filename_without_soname(self) -> None:
        # P2 regression (Codex review): a versioned library with no
        # DT_SONAME must still resolve a sibling's DT_NEEDED entry that
        # names its real on-disk filename (e.g. "libfoo.so.1"), not just
        # its canonical key ("libfoo.so") -- indexing only the canonical
        # key misclassified this as an "extra" (external) edge instead of
        # "intra", breaking reachability for consumers of that provider.
        libraries = {
            "libfoo.so": Path("/fake/libfoo.so.1"),
            "libconsumer.so": Path("/fake/libconsumer.so"),
        }
        metadata = {
            "libfoo.so": _meta(soname=""),  # no DT_SONAME
            "libconsumer.so": _meta(soname="libconsumer.so", needed=["libfoo.so.1"]),
        }
        graph = _compute_resolution_graph(libraries, metadata)
        assert graph.intra_needed["libconsumer.so"] == ["libfoo.so.1"]
        assert graph.extra_needed["libconsumer.so"] == []

    @pytest.mark.skipif(
        sys.platform == "win32", reason="symlinks need admin on Windows"
    )
    def test_dt_needed_resolves_via_real_filename_of_symlinked_member(
        self, tmp_path: Path
    ) -> None:
        # P2 regression (Codex review): the earlier fix above indexed
        # ``libraries[name].name`` -- fine when the discovered path already
        # *is* the real file, but directory discovery's usual
        # "libfoo.so -> libfoo.so.1" pair sorts the symlink first and keeps
        # it as the representative discovered path, so ``.name`` on it was
        # still just "libfoo.so", never the real target's on-disk filename
        # a sibling's DT_NEEDED actually names. Must resolve through the
        # symlink to index the real basename.
        real = tmp_path / "libfoo.so.1"
        real.write_bytes(b"")
        link = tmp_path / "libfoo.so"
        link.symlink_to(real)

        libraries = {
            "libfoo.so": link,
            "libconsumer.so": tmp_path / "libconsumer.so",
        }
        metadata = {
            "libfoo.so": _meta(soname=""),  # no DT_SONAME
            "libconsumer.so": _meta(soname="libconsumer.so", needed=["libfoo.so.1"]),
        }
        graph = _compute_resolution_graph(libraries, metadata)
        assert graph.intra_needed["libconsumer.so"] == ["libfoo.so.1"]
        assert graph.extra_needed["libconsumer.so"] == []


# ---------------------------------------------------------------------------
# bundle_intra_dep_removed
# ---------------------------------------------------------------------------


class TestIntraDepRemoved:
    def test_detects_missing_import(self) -> None:
        old = _snapshot(
            {
                "libcore.so": _meta(
                    soname="libcore.so.1", exports=["core_add", "core_mul"]
                ),
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libcore.so.1"],
                    imports=["core_add", "core_mul"],
                ),
            }
        )
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["core_add"]),
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libcore.so.1"],
                    imports=["core_add", "core_mul"],
                ),
            }
        )
        result = compare_bundle(old, new, per_library_results=[])
        kinds = {f.kind for f in result.bundle_findings}
        assert ChangeKind.BUNDLE_INTRA_DEP_REMOVED in kinds
        finding = next(
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
        )
        assert finding.symbol == "core_mul"
        assert finding.consumer_library == "libalgo.so"

    def test_ignores_system_symbols(self) -> None:
        # libc/libstdc++ imports must not fire bundle findings.
        new = _snapshot(
            {
                "libfoo.so": _meta(
                    soname="libfoo.so.1",
                    needed=["libcore.so.1"],
                    imports=["__cxa_atexit", "malloc", "memcpy"],
                ),
                "libcore.so": _meta(soname="libcore.so.1", exports=["dummy"]),
            }
        )
        result = compare_bundle(new, new, per_library_results=[])
        assert not any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
            for f in result.bundle_findings
        )

    def test_extends_system_providers_via_arg(self) -> None:
        new = _snapshot(
            {
                "libfoo.so": _meta(
                    soname="libfoo.so.1",
                    needed=["libcore.so.1", "libcustom.so.1"],
                    imports=["custom_init"],
                ),
                "libcore.so": _meta(soname="libcore.so.1", exports=["dummy"]),
            }
        )
        # Without user-extended allow-list, custom_init is bundle-relevant.
        result_default = compare_bundle(new, new, per_library_results=[])
        # Note: heuristic — custom_init may already be excluded by the
        # "no intra-bundle siblings imported" path. Either way the
        # explicit allow-list must not introduce findings.
        with_extra = compare_bundle(
            new,
            new,
            per_library_results=[],
            system_providers=["libcustom.so.1"],
        )
        assert len(with_extra.bundle_findings) <= len(result_default.bundle_findings)

    def test_system_providers_suppresses_non_system_shaped_symbol(self) -> None:
        """Regression: --bundle-system-providers must suppress a finding for
        a non-system-shaped symbol (not std::-mangled, not in
        DEFAULT_SYSTEM_SYMBOLS) once every one of the consumer's non-intra
        DT_NEEDED edges is covered by the allow-list -- e.g. a vendor C API
        symbol like Acme's acme_custom_op imported from a soname the user
        explicitly named via --bundle-system-providers (fictitious vendor,
        not a real default -- this tests the generic override mechanism).
        A prior revision gated this allow-list match on the symbol *also*
        looking system-shaped, which made --bundle-system-providers inert
        for exactly this case."""
        new = _snapshot(
            {
                "libfoo.so": _meta(
                    soname="libfoo.so.1",
                    needed=["libacme_math.so.2"],
                    imports=["acme_custom_op"],
                ),
            }
        )
        # Without the allow-list entry: real, reportable finding.
        without_extra = compare_bundle(new, new, per_library_results=[])
        assert any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
            and f.symbol == "acme_custom_op"
            for f in without_extra.bundle_findings
        )
        # With the exact soname allow-listed: finding must be suppressed.
        with_extra = compare_bundle(
            new,
            new,
            per_library_results=[],
            system_providers=["libacme_math.so.2"],
        )
        assert not any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
            for f in with_extra.bundle_findings
        )

    def test_system_providers_matches_versioned_soname_by_stem(self) -> None:
        """A user-supplied allow-list entry without the real DT_NEEDED
        version suffix (e.g. 'libacme_math', no '.so.2') must still match
        the real, versioned soname -- hand-typed allow-list entries rarely
        carry the exact runtime version suffix. Fictitious vendor soname,
        same reason as the sibling test above."""
        new = _snapshot(
            {
                "libfoo.so": _meta(
                    soname="libfoo.so.1",
                    needed=["libacme_math.so.2"],
                    imports=["acme_custom_op"],
                ),
            }
        )
        with_extra = compare_bundle(
            new,
            new,
            per_library_results=[],
            system_providers=["libacme_math"],
        )
        assert not any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
            for f in with_extra.bundle_findings
        )

    def test_system_providers_explicit_major_version_does_not_match_a_different_one(
        self,
    ) -> None:
        """Regression (Codex review): an allow-list entry that itself names
        a specific major version (`libvendor.so.1`) must require an exact
        match -- it must NOT also match an unrelated major
        (`libvendor.so.2`) via stem comparison. Stem fallback exists for a
        version-*generic* entry (`libmkl_core`/`libmkl_core.so`, no numeric
        major) matching any real runtime version, not for treating two
        different, explicitly-pinned majors as interchangeable."""
        new = _snapshot(
            {
                "libfoo.so": _meta(
                    soname="libfoo.so.1",
                    needed=["libvendor.so.2"],
                    imports=["vendor_custom_op"],
                ),
            }
        )
        with_extra = compare_bundle(
            new,
            new,
            per_library_results=[],
            system_providers=["libvendor.so.1"],
        )
        assert any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
            and f.symbol == "vendor_custom_op"
            for f in with_extra.bundle_findings
        )

    def test_fires_when_dt_needed_was_stripped(self) -> None:
        # Regression for the CodeRabbit feedback: previously the bundle
        # layer short-circuited when consumer.intra_needed was empty,
        # which hid the case where a build refactor removed BOTH the
        # only sibling provider *and* the DT_NEEDED edge that pointed at
        # it. The unresolved import remains in .dynsym; the bundle layer
        # must still flag it (the system-symbol allow-list separately
        # filters out genuinely-external imports).
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1"),  # provider gone
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=[],  # DT_NEEDED stripped too
                    imports=["onedal_internal_op"],  # not a system symbol
                ),
            }
        )
        result = compare_bundle(new, new, per_library_results=[])
        intra_removed = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
        ]
        assert len(intra_removed) == 1
        assert intra_removed[0].symbol == "onedal_internal_op"
        assert intra_removed[0].consumer_library == "libalgo.so"

    def test_stripped_provider_still_fires_when_a_system_dep_remains(self) -> None:
        """Regression (Codex review): the previous fix for
        test_fires_when_dt_needed_was_stripped above only exercised a
        consumer with an EMPTY DT_NEEDED after stripping -- but a real
        binary almost always still needs libc. If the allow-list check
        (--bundle-system-providers / DEFAULT_SYSTEM_PROVIDERS) were trusted
        whenever every *remaining* DT_NEEDED happens to be a system
        library, the canonical regression (provider AND its DT_NEEDED edge
        both dropped) would be silently swallowed the moment the consumer
        also links libc -- which is nearly always. The allow-list evidence
        must not be trusted here: `onedal_internal_op` was provided by a
        sibling in `old`, so its disappearance is a real, reportable
        regression regardless of what other (system) libraries the
        consumer still needs.
        """
        old = _snapshot(
            {
                "libcore.so": _meta(
                    soname="libcore.so.1", exports=["onedal_internal_op"]
                ),
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libcore.so.1", "libc.so.6"],
                    imports=["onedal_internal_op"],
                ),
            }
        )
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1"),  # provider gone
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libc.so.6"],  # intra-bundle edge dropped too,
                    # but the consumer still needs libc -- a real, near-
                    # universal system dependency, already covered by
                    # DEFAULT_SYSTEM_PROVIDERS with no --bundle-system-
                    # providers involved at all.
                    imports=["onedal_internal_op"],
                ),
            }
        )
        result = compare_bundle(old, new, per_library_results=[])
        intra_removed = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
        ]
        assert len(intra_removed) == 1
        assert intra_removed[0].symbol == "onedal_internal_op"
        assert intra_removed[0].consumer_library == "libalgo.so"

    def test_explicit_provider_migration_still_suppressed(self) -> None:
        """Regression (Codex review): a legitimate provider migration --
        a symbol moved from an in-tree sibling to an explicitly allow-listed
        external DSO -- must still be suppressed by --bundle-system-providers
        even though a sibling *used to* provide it (the guard the previous
        finding above added must not over-correct into vetoing every
        allow-list match once `old` had any provider at all)."""
        old = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["vendor_op"]),
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libcore.so.1"],
                    imports=["vendor_op"],
                ),
            }
        )
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1"),  # no longer exports it
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libvendor.so.1"],  # migrated to an external DSO
                    imports=["vendor_op"],
                ),
            }
        )
        result = compare_bundle(
            old, new, per_library_results=[], system_providers=["libvendor.so.1"]
        )
        assert not any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
            for f in result.bundle_findings
        )

    def test_explicit_provider_migration_needs_the_explicit_soname(self) -> None:
        """The migration exception above only fires when the user actually
        named the new provider -- a plain, unexplained soname swap to a
        library that merely *happens* to be default-system-shaped (but was
        never asserted by the user for this run) must still be treated as
        a potential regression, not silently trusted."""
        old = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["vendor_op"]),
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libcore.so.1"],
                    imports=["vendor_op"],
                ),
            }
        )
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1"),
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libvendor.so.1"],
                    imports=["vendor_op"],
                ),
            }
        )
        # No --bundle-system-providers given, and libvendor.so.1 doesn't
        # match _looks_system -- extra_needed isn't even fully allow-listed,
        # so this must still be reported regardless of the migration guard.
        result = compare_bundle(old, new, per_library_results=[])
        assert any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED and f.symbol == "vendor_op"
            for f in result.bundle_findings
        )

    def test_old_provider_veto_is_scoped_to_the_reaching_consumer(self) -> None:
        """Regression (Codex review): "did a sibling ever provide this
        symbol" must be scoped to *this consumer's* own old reachability,
        not to whether *any* consumer anywhere in the old bundle reached a
        provider. `liba.so` used to reach `libcore.so`'s `vendor_op`
        export; `libb.so` has always imported the same unversioned symbol
        name from the built-in-allow-listed `libsycl.so.7` and never
        depended on `libcore.so` at all. Once `liba.so`/`libcore.so`'s
        export are both removed, `libb.so`'s own always-external import
        must not be vetoed by an unrelated consumer's old provider."""
        old = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["vendor_op"]),
                "liba.so": _meta(
                    soname="liba.so.1",
                    needed=["libcore.so.1"],
                    imports=["vendor_op"],
                ),
                "libb.so": _meta(
                    soname="libb.so.1",
                    needed=["libsycl.so.7"],
                    imports=["vendor_op"],
                ),
            }
        )
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1"),  # no longer exports it
                "libb.so": _meta(
                    soname="libb.so.1",
                    needed=["libsycl.so.7"],
                    imports=["vendor_op"],
                ),
            }
        )
        # No --bundle-system-providers given -- libsycl.so.7 is covered by
        # the built-in DEFAULT_SYSTEM_PROVIDERS allow-list alone.
        result = compare_bundle(old, new, per_library_results=[])
        assert not any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
            and f.consumer_library == "libb.so"
            for f in result.bundle_findings
        )

    def test_old_provider_veto_requires_version_compatibility(self) -> None:
        """Regression (Codex review): an old reachable sibling that could
        never have satisfied *this* consumer's own reference must not veto
        the allow-list either. `libcore.so` reachably exported `vendor_op`
        only as a non-default versioned definition (`vendor_op@V1`, never
        `vendor_op@@V1`) -- the dynamic linker can only satisfy `libalgo.so`'s
        genuinely unversioned `vendor_op` reference against a *default*
        definition, so that sibling could never have resolved it in the
        first place. Once `libcore.so`/its `DT_NEEDED` edge are both
        removed, `libalgo.so`'s always-external (built-in-allow-listed)
        import must not be vetoed by a provider that was never compatible."""
        old_core = _meta(soname="libcore.so.1")
        old_core.symbols.append(
            ElfSymbol(
                name="vendor_op", visibility="default", version="V1", is_default=False
            )
        )
        old = _snapshot(
            {
                "libcore.so": old_core,
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libcore.so.1", "libsycl.so.7"],
                    imports=["vendor_op"],
                ),
            }
        )
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1"),  # no longer exports it
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libsycl.so.7"],
                    imports=["vendor_op"],
                ),
            }
        )
        result = compare_bundle(old, new, per_library_results=[])
        assert not any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
            for f in result.bundle_findings
        )

    @pytest.mark.parametrize(
        ("symbol", "version"),
        [
            ("syscall", "GLIBC_2.2.5"),
            ("stdout", "GLIBC_2.2.5"),
            ("_ZdlPvm", "CXXABI_1.3"),  # operator delete(void*, unsigned long)
            ("_ZSt9terminatev", "GLIBCXX_3.4"),
            ("GOMP_parallel", "GOMP_4.0"),
            ("omp_get_thread_num", "OMP_1.0"),
        ],
    )
    def test_versioned_system_import_is_not_intra_dep(
        self, symbol: str, version: str
    ) -> None:
        # Field-derived oneDAL fix: an import bound to a C/C++ runtime or
        # toolchain version namespace is external by construction and must not
        # produce bundle_intra_dep_removed — even when its symbol name is not
        # on the static DEFAULT_SYSTEM_SYMBOLS allow-list (syscall/stdout are
        # not, _ZdlPvm is not std::-mangled).
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["dummy"]),
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libcore.so.1"],
                    imports=[symbol],
                    import_versions={symbol: version},
                ),
            }
        )
        result = compare_bundle(new, new, per_library_results=[])
        assert not any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
            for f in result.bundle_findings
        )

    def test_unversioned_internal_sibling_import_still_fires(self) -> None:
        # The flip side of the version filter: an *unversioned* import that no
        # sibling provides is still a dropped intra-bundle dependency.
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["dummy"]),
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libcore.so.1"],
                    imports=["onedal_internal_op"],  # version="" → internal
                ),
            }
        )
        result = compare_bundle(new, new, per_library_results=[])
        intra_removed = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
        ]
        assert len(intra_removed) == 1
        assert intra_removed[0].symbol == "onedal_internal_op"

    def test_version_required_from_external_soname_is_skipped(self) -> None:
        # Provider evidence half of the fix: a versioned import whose version
        # is declared (.gnu.version_r) against a soname that does NOT resolve
        # inside the bundle is external — even when the version namespace is
        # not a well-known toolchain prefix (here a third-party "FOO_" tag).
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["dummy"]),
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libcore.so.1", "libthirdparty.so.2"],
                    imports=["tp_init"],
                    import_versions={"tp_init": "FOO_1.0"},
                    # version required from an external (non-bundle) soname
                    versions_required={"libthirdparty.so.2": ["FOO_1.0"]},
                ),
            }
        )
        result = compare_bundle(new, new, per_library_results=[])
        assert not any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
            for f in result.bundle_findings
        )

    def test_version_required_from_intra_sibling_still_fires(self) -> None:
        # Contrast with the previous test: when the required version resolves
        # against an *intra-bundle* sibling soname but that sibling no longer
        # exports the symbol, it IS a dropped intra-bundle dependency.
        new = _snapshot(
            {
                "libcore.so": _meta(
                    soname="libcore.so.1", exports=["dummy"]
                ),  # provider for core_op gone
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libcore.so.1"],
                    imports=["core_op"],
                    import_versions={"core_op": "LIBCORE_1.0"},
                    versions_required={"libcore.so.1": ["LIBCORE_1.0"]},
                ),
            }
        )
        result = compare_bundle(new, new, per_library_results=[])
        intra_removed = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
        ]
        assert len(intra_removed) == 1
        assert intra_removed[0].symbol == "core_op"

    def test_ambiguous_version_label_from_intra_and_external_still_fires(self) -> None:
        # GNU version names are scoped per verneed provider, not globally
        # unique: the same label ("FOO_1.0") can be required from both an
        # intra-bundle sibling and an external soname. Provider evidence is
        # then ambiguous and must NOT suppress the dropped-sibling finding.
        new = _snapshot(
            {
                "libcore.so": _meta(
                    soname="libcore.so.1", exports=["dummy"]
                ),  # provider for core_op gone
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libcore.so.1", "libthirdparty.so.2"],
                    imports=["core_op"],
                    import_versions={"core_op": "FOO_1.0"},
                    # FOO_1.0 advertised by BOTH an intra sibling and an
                    # external soname.
                    versions_required={
                        "libcore.so.1": ["FOO_1.0"],
                        "libthirdparty.so.2": ["FOO_1.0"],
                    },
                ),
            }
        )
        result = compare_bundle(new, new, per_library_results=[])
        intra_removed = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
        ]
        assert len(intra_removed) == 1
        assert intra_removed[0].symbol == "core_op"

    def test_versioned_import_after_soname_bump_still_fires(self) -> None:
        # SONAME-major transition (an explicit oneDAL datapoint): the provider
        # bumped its SONAME libcore.so.1 -> libcore.so.2 and dropped core_op,
        # while a surviving sibling still NEEDs the OLD soname and imports
        # core_op@LIBCORE_1.0. The old soname no longer resolves *exactly*, but
        # the bundle still contains libcore.so (filename-stem match), so the
        # versioned import must NOT be treated as external — the release will
        # fail to load and bundle_intra_dep_removed must fire.
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.2", exports=["other_op"]),
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libcore.so.1"],  # old soname — no exact resolve
                    imports=["core_op"],
                    import_versions={"core_op": "LIBCORE_1.0"},
                    versions_required={"libcore.so.1": ["LIBCORE_1.0"]},
                ),
            }
        )
        result = compare_bundle(new, new, per_library_results=[])
        intra_removed = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
        ]
        assert len(intra_removed) == 1
        assert intra_removed[0].symbol == "core_op"

    def test_vendored_runtime_dropping_system_versioned_symbol_still_fires(
        self,
    ) -> None:
        # The release VENDORS the runtime DSO (libgomp.so.1) and a sibling's
        # verneed ties GOMP_4.0 to that bundled soname. If the vendored runtime
        # drops the export the sibling is unresolved at load — provider
        # evidence must win over the system-version-namespace shortcut, which
        # would otherwise classify GOMP_parallel@GOMP_4.0 as external.
        new = _snapshot(
            {
                "libgomp.so": _meta(soname="libgomp.so.1", exports=["other_gomp"]),
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libgomp.so.1"],
                    imports=["GOMP_parallel"],
                    import_versions={"GOMP_parallel": "GOMP_4.0"},
                    versions_required={"libgomp.so.1": ["GOMP_4.0"]},
                ),
            }
        )
        result = compare_bundle(new, new, per_library_results=[])
        intra_removed = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
        ]
        assert len(intra_removed) == 1
        assert intra_removed[0].symbol == "GOMP_parallel"

    def test_non_vendored_system_version_with_external_verneed_skipped(self) -> None:
        # Counterpart: the runtime is NOT vendored — GOMP_4.0 verneed points at
        # an external libgomp.so.1, so the import stays external (no finding).
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["dummy"]),
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libcore.so.1", "libgomp.so.1"],
                    imports=["GOMP_parallel"],
                    import_versions={"GOMP_parallel": "GOMP_4.0"},
                    versions_required={"libgomp.so.1": ["GOMP_4.0"]},
                ),
            }
        )
        result = compare_bundle(new, new, per_library_results=[])
        assert not any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
            for f in result.bundle_findings
        )

    def test_colliding_version_label_disambiguated_per_symbol(self) -> None:
        # One consumer needs two providers that BOTH advertise FOO_1.0: a
        # bundled libcore.so.1 and an external libthirdparty.so.2. Per-symbol
        # verneed provider (ElfImport.version_soname) must resolve each import
        # independently: the bundled core_op (dropped) fires, while the genuinely
        # external tp_init must NOT be reported as bundle_intra_dep_removed.
        new = _snapshot(
            {
                "libcore.so": _meta(
                    soname="libcore.so.1", exports=["dummy"]
                ),  # core_op dropped
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libcore.so.1", "libthirdparty.so.2"],
                    imports=["core_op", "tp_init"],
                    import_versions={"core_op": "FOO_1.0", "tp_init": "FOO_1.0"},
                    import_version_sonames={
                        "core_op": "libcore.so.1",  # bundled sibling
                        "tp_init": "libthirdparty.so.2",  # external
                    },
                    versions_required={
                        "libcore.so.1": ["FOO_1.0"],
                        "libthirdparty.so.2": ["FOO_1.0"],
                    },
                ),
            }
        )
        result = compare_bundle(new, new, per_library_results=[])
        intra_removed = {
            f.symbol
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
        }
        assert intra_removed == {"core_op"}  # tp_init correctly excluded


# ---------------------------------------------------------------------------
# bundle_intra_dep_signature_changed
# ---------------------------------------------------------------------------


class TestIntraDepSignatureChanged:
    def test_promotes_provider_signature_change_to_consumer(self) -> None:
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["core_add"]),
                "libalgo.so": _meta(
                    soname="libalgo.so.1", needed=["libcore.so.1"], imports=["core_add"]
                ),
            }
        )
        diff_libcore = _diff(
            "libcore.so",
            Change(
                kind=ChangeKind.FUNC_PARAMS_CHANGED,
                symbol="core_add",
                description="int->long",
            ),
        )
        result = compare_bundle(new, new, [diff_libcore])
        findings = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED
        ]
        assert len(findings) == 1
        assert findings[0].consumer_library == "libalgo.so"
        assert findings[0].provider_library == "libcore.so"

    def test_dedupe_params_plus_return_change(self) -> None:
        # libcore changes both params AND return of the same symbol; we
        # should emit a SINGLE bundle finding per (consumer, symbol).
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["core_add"]),
                "libalgo.so": _meta(
                    soname="libalgo.so.1", needed=["libcore.so.1"], imports=["core_add"]
                ),
            }
        )
        diff = _diff(
            "libcore.so",
            Change(
                kind=ChangeKind.FUNC_PARAMS_CHANGED, symbol="core_add", description=""
            ),
            Change(
                kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="core_add", description=""
            ),
        )
        result = compare_bundle(new, new, [diff])
        sig_findings = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED
        ]
        assert len(sig_findings) == 1

    def test_no_finding_when_no_consumers(self) -> None:
        # Provider changes but no sibling imports the symbol — bundle-level
        # finding does NOT fire; the per-library diff already covers it.
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["core_add"]),
                "libother.so": _meta(soname="libother.so.1"),
            }
        )
        diff = _diff(
            "libcore.so",
            Change(
                kind=ChangeKind.FUNC_PARAMS_CHANGED, symbol="core_add", description=""
            ),
        )
        result = compare_bundle(new, new, [diff])
        assert not any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED
            for f in result.bundle_findings
        )

    def test_promotable_kinds_are_a_strict_subset_of_confirmed_kinds(self) -> None:
        # G38 stabilization, revised after a Codex review round: promotion
        # ("confirmed severely enough to fabricate a consumer-attributed
        # BREAKING bundle finding") and suppression ("confirmed enough to
        # withhold the 'couldn't tell either way' finding") are two
        # different bars, not one -- an earlier revision of this test
        # asserted the two sets were *equal*, which was itself the bug:
        # it would have required promoting FUNC_NOEXCEPT_ADDED
        # (default_verdict=COMPATIBLE) and FUNC_NOEXCEPT_REMOVED/
        # FUNC_EXCEPTION_SPEC_CHANGED (COMPATIBLE_WITH_RISK) to a fabricated
        # BREAKING bundle finding. The promotable set must be a strict,
        # proper subset of the confirmed set (bundle_signature_evidence's
        # own suppression set), and every kind promoted to BREAKING must
        # itself carry default_verdict=BREAKING in change_registry.py.
        from abicheck.bundle_models import (
            CONFIRMED_C_BOUNDARY_SIGNATURE_BREAK_KINDS,
            PROMOTABLE_C_BOUNDARY_SIGNATURE_BREAK_KINDS,
        )
        from abicheck.bundle_signature_evidence import (
            _CONFIRMED_SIGNATURE_CHANGE_KINDS,
        )
        from abicheck.change_registry import REGISTRY
        from abicheck.checker_policy import Verdict

        assert PROMOTABLE_C_BOUNDARY_SIGNATURE_BREAK_KINDS < (
            CONFIRMED_C_BOUNDARY_SIGNATURE_BREAK_KINDS
        )
        assert (
            CONFIRMED_C_BOUNDARY_SIGNATURE_BREAK_KINDS
            == _CONFIRMED_SIGNATURE_CHANGE_KINDS
        )
        for kind in PROMOTABLE_C_BOUNDARY_SIGNATURE_BREAK_KINDS:
            meta = REGISTRY.get(kind.value)
            assert meta is not None and meta.default_verdict == Verdict.BREAKING, (
                f"{kind} is promotable to a BREAKING bundle finding but its "
                f"own default_verdict is not BREAKING"
            )
        # The specific fabrication this test guards against: noexcept
        # changes must never be promotable, regardless of their presence
        # in the (correctly broader) suppression-only confirmed set.
        assert (
            ChangeKind.FUNC_NOEXCEPT_ADDED
            not in PROMOTABLE_C_BOUNDARY_SIGNATURE_BREAK_KINDS
        )
        assert (
            ChangeKind.FUNC_NOEXCEPT_REMOVED
            not in PROMOTABLE_C_BOUNDARY_SIGNATURE_BREAK_KINDS
        )
        assert (
            ChangeKind.FUNC_NOEXCEPT_ADDED in CONFIRMED_C_BOUNDARY_SIGNATURE_BREAK_KINDS
        )
        # ctor-explicit stays excluded on purpose from both sets.
        assert (
            ChangeKind.CTOR_EXPLICIT_ADDED
            not in CONFIRMED_C_BOUNDARY_SIGNATURE_BREAK_KINDS
        )
        assert (
            ChangeKind.CTOR_EXPLICIT_REMOVED
            not in CONFIRMED_C_BOUNDARY_SIGNATURE_BREAK_KINDS
        )

    def test_does_not_promote_noexcept_added_to_a_breaking_finding(self) -> None:
        # Regression for the fabrication above: FUNC_NOEXCEPT_ADDED is
        # confirmed (suppresses bundle_signature_evidence's "unverified"
        # finding) but must never itself promote to
        # BUNDLE_INTRA_DEP_SIGNATURE_CHANGED -- that would turn a
        # default_verdict=COMPATIBLE per-library change into a fabricated
        # BREAKING cross-library one.
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["core_add"]),
                "libalgo.so": _meta(
                    soname="libalgo.so.1", needed=["libcore.so.1"], imports=["core_add"]
                ),
            }
        )
        diff_libcore = _diff(
            "libcore.so",
            Change(
                kind=ChangeKind.FUNC_NOEXCEPT_ADDED,
                symbol="core_add",
                description="gained noexcept",
            ),
        )
        result = compare_bundle(new, new, [diff_libcore])
        assert not any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED
            for f in result.bundle_findings
        )

    def test_promotes_calling_convention_change_to_consumer(self) -> None:
        # CALLING_CONVENTION_CHANGED is in both the confirmed (suppression)
        # and promotable sets -- it's a genuine, BREAKING, direct
        # call-boundary mismatch, unlike noexcept/exception-spec above.
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["core_add"]),
                "libalgo.so": _meta(
                    soname="libalgo.so.1", needed=["libcore.so.1"], imports=["core_add"]
                ),
            }
        )
        diff_libcore = _diff(
            "libcore.so",
            Change(
                kind=ChangeKind.CALLING_CONVENTION_CHANGED,
                symbol="core_add",
                description="cdecl->fastcall",
            ),
        )
        result = compare_bundle(new, new, [diff_libcore])
        findings = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED
        ]
        assert len(findings) == 1
        assert findings[0].consumer_library == "libalgo.so"
        assert findings[0].provider_library == "libcore.so"

    def test_promotes_using_canonical_provider_key_for_a_versioned_basename(
        self,
    ) -> None:
        # G38 stabilization (CodeRabbit review, fresh evidence, concrete
        # repro traced through cli_compare_release.py's own _bundle_key/
        # DiffResult.library split): DiffResult.library is always the real,
        # possibly SONAME-versioned on-disk filename, not the bundle's
        # canonical key -- so a promoted finding's own diff_by_library
        # lookup must canonicalize it the same way BundleSnapshot.resolution
        # does, or the promotion silently never fires (the versioned key
        # never matches any provider the resolution graph actually knows).
        libraries = {
            "libcore.so": Path("/fake/libcore.so.1.2.3"),
            "libalgo.so": Path("/fake/libalgo.so.1"),
        }
        metadata = {
            "libcore.so": _meta(soname="libcore.so.1", exports=["core_add"]),
            "libalgo.so": _meta(
                soname="libalgo.so.1", needed=["libcore.so.1"], imports=["core_add"]
            ),
        }
        graph = _compute_resolution_graph(libraries, metadata)
        new = BundleSnapshot(
            root=Path("/fake"), libraries=libraries, metadata=metadata, resolution=graph
        )
        diff_libcore = _diff(
            "libcore.so.1.2.3",  # the real, versioned on-disk basename
            Change(
                kind=ChangeKind.CALLING_CONVENTION_CHANGED,
                symbol="core_add",
                description="cdecl->fastcall",
            ),
        )
        result = compare_bundle(new, new, [diff_libcore])
        findings = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED
        ]
        assert len(findings) == 1
        assert findings[0].consumer_library == "libalgo.so"
        assert findings[0].provider_library == "libcore.so"  # canonical, not versioned

    def test_promotes_when_the_versioned_basename_changed_between_old_and_new(
        self,
    ) -> None:
        # G38 stabilization (Codex review, fresh evidence): checker.compare()
        # sets DiffResult.library from the OLD side, so a provider whose
        # versioned on-disk basename changed between old and new
        # (libcore.so.1.2 -> libcore.so.1.3) has a DiffResult.library that
        # only the OLD bundle's own basename map can resolve -- looking it
        # up only against the NEW map (as the single-snapshot test above
        # can't distinguish from a same-snapshot-both-sides lookup) left it
        # unresolved and the promotion silently never fired.
        old_libraries = {
            "libcore.so": Path("/fake/libcore.so.1.2"),
            "libalgo.so": Path("/fake/libalgo.so.1"),
        }
        new_libraries = {
            "libcore.so": Path("/fake/libcore.so.1.3"),
            "libalgo.so": Path("/fake/libalgo.so.1"),
        }
        metadata = {
            "libcore.so": _meta(soname="libcore.so.1", exports=["core_add"]),
            "libalgo.so": _meta(
                soname="libalgo.so.1", needed=["libcore.so.1"], imports=["core_add"]
            ),
        }
        old = BundleSnapshot(
            root=Path("/fake"),
            libraries=old_libraries,
            metadata=metadata,
            resolution=_compute_resolution_graph(old_libraries, metadata),
        )
        new = BundleSnapshot(
            root=Path("/fake"),
            libraries=new_libraries,
            metadata=metadata,
            resolution=_compute_resolution_graph(new_libraries, metadata),
        )
        diff_libcore = _diff(
            "libcore.so.1.2",  # the OLD side's real, versioned basename
            Change(
                kind=ChangeKind.CALLING_CONVENTION_CHANGED,
                symbol="core_add",
                description="cdecl->fastcall",
            ),
        )
        result = compare_bundle(old, new, [diff_libcore])
        findings = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED
        ]
        assert len(findings) == 1
        assert findings[0].consumer_library == "libalgo.so"
        assert findings[0].provider_library == "libcore.so"

    def test_checks_every_versioned_definition_from_the_same_provider(self) -> None:
        # G38 stabilization (Codex review, fresh evidence): a single
        # provider can legitimately export multiple versioned definitions
        # of one bare symbol (the compat-symbol pattern core_add@V1
        # alongside core_add@@V2) -- consumer_resolves_via_provider must
        # check every one of that provider's own entries, not just the
        # first providers_for() happens to return. Picking only the first
        # (a non-default V1 entry) would test it against a consumer
        # explicitly requiring V2 and wrongly conclude no match, even
        # though the same provider's V2 entry does match.
        libcore_meta = ElfMetadata(
            soname="libcore.so.1",
            needed=[],
            symbols=[
                ElfSymbol(
                    name="core_add",
                    visibility="default",
                    version="V1",
                    is_default=False,
                ),
                ElfSymbol(
                    name="core_add",
                    visibility="default",
                    version="V2",
                    is_default=True,
                ),
            ],
            imports=[],
        )
        libalgo_meta = _meta(
            soname="libalgo.so.1",
            needed=["libcore.so.1"],
            imports=["core_add"],
            import_versions={"core_add": "V2"},
        )
        new = _snapshot({"libcore.so": libcore_meta, "libalgo.so": libalgo_meta})
        diff_libcore = _diff(
            "libcore.so",
            Change(
                kind=ChangeKind.CALLING_CONVENTION_CHANGED,
                symbol="core_add",
                description="cdecl->fastcall",
            ),
        )
        result = compare_bundle(new, new, [diff_libcore])
        findings = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED
        ]
        assert len(findings) == 1
        assert findings[0].consumer_library == "libalgo.so"

    def test_declines_to_promote_when_the_provider_is_ambiguous(self) -> None:
        # G38 stabilization (Codex review, fresh evidence, beyond the
        # reachability fix above): when a consumer directly NEEDs TWO
        # DSOs that both export a matching (unversioned/default)
        # definition of the same bare symbol, this model has no notion of
        # real ELF symbol-search order (DT_NEEDED / global-scope
        # precedence) to say which one the consumer's unversioned
        # reference actually binds to -- attributing a signature change on
        # either one to that consumer would be a guess. Both candidates
        # must decline (ambiguous), not just avoid attributing to the
        # unreachable-sibling case the earlier fix covers.
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["core_add"]),
                "libalt.so": _meta(soname="libalt.so.1", exports=["core_add"]),
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libcore.so.1", "libalt.so.1"],
                    imports=["core_add"],
                ),
            }
        )
        diff_libcore = _diff(
            "libcore.so",
            Change(
                kind=ChangeKind.CALLING_CONVENTION_CHANGED,
                symbol="core_add",
                description="cdecl->fastcall",
            ),
        )
        result = compare_bundle(new, new, [diff_libcore])
        assert not any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED
            for f in result.bundle_findings
        )

    def test_does_not_promote_a_not_evaluated_finding(self) -> None:
        # G38 stabilization (Codex review, fresh evidence): under
        # `compare --contract ...`, a finding outside the selected
        # contract's scope is stamped `compatibility_evaluation_status=
        # NOT_EVALUATED` and stays in `diff.changes`, but is excluded from
        # the per-library verdict/exit code (ADR-049). Promoting it to a
        # bundle-level BREAKING finding would contradict that already-
        # scored result.
        from abicheck.contract_relevance_types import CompatibilityEvaluationStatus

        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["core_add"]),
                "libalgo.so": _meta(
                    soname="libalgo.so.1", needed=["libcore.so.1"], imports=["core_add"]
                ),
            }
        )
        change = Change(
            kind=ChangeKind.CALLING_CONVENTION_CHANGED,
            symbol="core_add",
            description="cdecl->fastcall",
            compatibility_evaluation_status=CompatibilityEvaluationStatus.NOT_EVALUATED,
        )
        diff_libcore = DiffResult(
            old_version="old",
            new_version="new",
            library="libcore.so",
            changes=[change],
            verdict=Verdict.NO_CHANGE,
        )
        result = compare_bundle(new, new, [diff_libcore])
        assert not any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED
            for f in result.bundle_findings
        )
        # An unstamped finding (no --contract in effect) is unaffected.
        diff_libcore_unstamped = _diff(
            "libcore.so",
            Change(
                kind=ChangeKind.CALLING_CONVENTION_CHANGED,
                symbol="core_add",
                description="cdecl->fastcall",
            ),
        )
        result_unstamped = compare_bundle(new, new, [diff_libcore_unstamped])
        assert any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED
            for f in result_unstamped.bundle_findings
        )

    def test_reachable_cache_is_not_recomputed_on_a_hit(self, monkeypatch) -> None:
        # G38 stabilization (Codex review, fresh evidence): the earlier
        # `reachable_cache.setdefault(lib, _reachable_intra_libraries(...))`
        # form evaluates the BFS unconditionally regardless of cache hit
        # (Python evaluates setdefault's default-value argument eagerly),
        # defeating the point of caching. Two changes against the same
        # provider, both reaching the same consumer, must trigger the BFS
        # at most once per distinct library.
        new = _snapshot(
            {
                "libcore.so": _meta(
                    soname="libcore.so.1", exports=["core_add", "core_sub"]
                ),
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libcore.so.1"],
                    imports=["core_add", "core_sub"],
                ),
            }
        )
        diff_libcore = _diff(
            "libcore.so",
            Change(
                kind=ChangeKind.CALLING_CONVENTION_CHANGED,
                symbol="core_add",
                description="cdecl->fastcall",
            ),
            Change(
                kind=ChangeKind.CALLING_CONVENTION_CHANGED,
                symbol="core_sub",
                description="cdecl->fastcall",
            ),
        )

        import abicheck.bundle_resolution_reachability as reachability_mod

        call_count = 0
        real_reachable = reachability_mod.reachable_intra_libraries

        def _counting_reachable(snapshot, root):
            nonlocal call_count
            call_count += 1
            return real_reachable(snapshot, root)

        monkeypatch.setattr(
            reachability_mod, "reachable_intra_libraries", _counting_reachable
        )

        result = compare_bundle(new, new, [diff_libcore])
        assert (
            len(
                [
                    f
                    for f in result.bundle_findings
                    if f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED
                ]
            )
            == 2
        )
        # One BFS per distinct library, not per (change, consumer) pair.
        assert call_count == 1

    def test_plugin_abi_policy_suppresses_calling_convention_promotion(self) -> None:
        # G38 stabilization (Codex review, fresh evidence): CALLING_
        # CONVENTION_CHANGED is COMPATIBLE under the plugin_abi policy
        # (change_registry.py's own policy_overrides), but the original
        # fix promoted it to an unconditionally-BREAKING
        # BUNDLE_INTRA_DEP_SIGNATURE_CHANGED regardless of policy --
        # defeating the exact override compute_verdict(policy="plugin_abi")
        # already honors for the originating per-library finding.
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["core_add"]),
                "libalgo.so": _meta(
                    soname="libalgo.so.1", needed=["libcore.so.1"], imports=["core_add"]
                ),
            }
        )
        diff_libcore = _diff(
            "libcore.so",
            Change(
                kind=ChangeKind.CALLING_CONVENTION_CHANGED,
                symbol="core_add",
                description="cdecl->fastcall",
            ),
        )
        result = compare_bundle(new, new, [diff_libcore], policy="plugin_abi")
        assert not any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED
            for f in result.bundle_findings
        )
        # The default (strict_abi) policy still promotes -- this isn't a
        # blanket regression of the Phase 5 fix, just policy-sensitive.
        result_strict = compare_bundle(new, new, [diff_libcore])
        assert any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED
            for f in result_strict.bundle_findings
        )

    def test_does_not_promote_when_consumer_reaches_a_different_provider(self) -> None:
        # G38 stabilization (CodeRabbit review, fresh evidence): libcore.so
        # and libalt.so both export core_add; libalgo.so needs only
        # libcore.so (never libalt.so). libalt.so's own signature change
        # must not attribute a promoted finding to libalgo.so -- that
        # consumer never actually resolves core_add against libalt.so.
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["core_add"]),
                "libalt.so": _meta(soname="libalt.so.1", exports=["core_add"]),
                "libalgo.so": _meta(
                    soname="libalgo.so.1", needed=["libcore.so.1"], imports=["core_add"]
                ),
            }
        )
        diff_libalt = _diff(
            "libalt.so",
            Change(
                kind=ChangeKind.CALLING_CONVENTION_CHANGED,
                symbol="core_add",
                description="cdecl->fastcall",
            ),
        )
        result = compare_bundle(new, new, [diff_libalt])
        assert not any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED
            for f in result.bundle_findings
        )

    def test_promotes_only_to_the_consumer_reaching_the_changed_provider(
        self,
    ) -> None:
        # Sibling of the negative case above: when a consumer genuinely
        # NEEDs the provider whose signature changed, promotion still
        # fires for that consumer, even with an unrelated same-named
        # export sitting elsewhere in the bundle.
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["core_add"]),
                "libalt.so": _meta(soname="libalt.so.1", exports=["core_add"]),
                "libalgo.so": _meta(
                    soname="libalgo.so.1", needed=["libcore.so.1"], imports=["core_add"]
                ),
            }
        )
        diff_libcore = _diff(
            "libcore.so",
            Change(
                kind=ChangeKind.CALLING_CONVENTION_CHANGED,
                symbol="core_add",
                description="cdecl->fastcall",
            ),
        )
        result = compare_bundle(new, new, [diff_libcore])
        findings = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED
        ]
        assert len(findings) == 1
        assert findings[0].consumer_library == "libalgo.so"
        assert findings[0].provider_library == "libcore.so"

    def test_honors_policy_file_override_before_promotion(self) -> None:
        # G38 stabilization (Codex review, fresh evidence): a custom
        # PolicyFile override demoting a promotable kind is resolved
        # through a separate path from a named base policy
        # (PolicyFile.compute_verdict, not Change.effective_verdict), so
        # policy_kind_sets(policy) alone can't see it -- promotion must
        # consult the originating diff's own policy_file too.
        from abicheck.policy_file import PolicyFile

        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["core_add"]),
                "libalgo.so": _meta(
                    soname="libalgo.so.1", needed=["libcore.so.1"], imports=["core_add"]
                ),
            }
        )
        policy_file = PolicyFile(
            overrides={ChangeKind.FUNC_VARIADIC_ADDED: Verdict.COMPATIBLE}
        )
        change = Change(
            kind=ChangeKind.FUNC_VARIADIC_ADDED,
            symbol="core_add",
            description="gained ...",
        )
        diff_libcore = DiffResult(
            old_version="old",
            new_version="new",
            library="libcore.so",
            changes=[change],
            verdict=Verdict.COMPATIBLE,
            policy_file=policy_file,
        )
        result = compare_bundle(new, new, [diff_libcore])
        assert not any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED
            for f in result.bundle_findings
        )
        # Without the override, the same kind promotes under strict_abi.
        diff_libcore_no_override = DiffResult(
            old_version="old",
            new_version="new",
            library="libcore.so",
            changes=[change],
            verdict=Verdict.BREAKING,
        )
        result_no_override = compare_bundle(new, new, [diff_libcore_no_override])
        assert any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED
            for f in result_no_override.bundle_findings
        )


# ---------------------------------------------------------------------------
# bundle_provider_changed
# ---------------------------------------------------------------------------


class TestProviderChanged:
    def test_detects_symbol_migration(self) -> None:
        old = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["util_x"]),
                "libutil.so": _meta(soname="libutil.so.1"),
            }
        )
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1"),
                "libutil.so": _meta(soname="libutil.so.1", exports=["util_x"]),
            }
        )
        diffs = [
            _diff(
                "libcore.so",
                Change(
                    kind=ChangeKind.FUNC_REMOVED,
                    symbol="util_x",
                    description="",
                ),
            ),
            _diff(
                "libutil.so",
                Change(
                    kind=ChangeKind.FUNC_ADDED,
                    symbol="util_x",
                    description="",
                ),
                verdict=Verdict.COMPATIBLE,
            ),
        ]
        result = compare_bundle(old, new, diffs)
        provider_findings = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_PROVIDER_CHANGED
        ]
        assert len(provider_findings) == 1
        assert provider_findings[0].symbol == "util_x"
        assert provider_findings[0].old_value == "libcore.so"
        assert provider_findings[0].new_value == "libutil.so"

    def test_no_finding_when_provider_unchanged(self) -> None:
        # func_removed in libcore + func_added in libcore (same lib) is
        # NOT a provider migration.
        new = _snapshot({"libcore.so": _meta(soname="libcore.so.1", exports=["x"])})
        diffs = [
            _diff(
                "libcore.so",
                Change(kind=ChangeKind.FUNC_REMOVED, symbol="y", description=""),
                Change(kind=ChangeKind.FUNC_ADDED, symbol="y", description=""),
            ),
        ]
        result = compare_bundle(new, new, diffs)
        assert not any(
            f.kind == ChangeKind.BUNDLE_PROVIDER_CHANGED for f in result.bundle_findings
        )


# ---------------------------------------------------------------------------
# bundle_library_removed / bundle_library_added
# ---------------------------------------------------------------------------


class TestLibraryStructural:
    def test_added_library_emits_addition(self) -> None:
        old = _snapshot({"libcore.so": _meta(soname="libcore.so.1")})
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1"),
                "libnew.so": _meta(soname="libnew.so.1"),
            }
        )
        result = compare_bundle(old, new, [])
        added = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_LIBRARY_ADDED
        ]
        assert len(added) == 1
        assert added[0].symbol == "libnew.so"

    def test_removed_library_emits_finding_only_with_consumers(self) -> None:
        # If no sibling imported the removed library's symbols, the
        # bundle layer stays silent — the existing --fail-on-removed-library
        # flow is responsible.
        old = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["x"]),
                "libstandalone.so": _meta(soname="libstandalone.so.1", exports=["y"]),
            }
        )
        new = _snapshot({"libcore.so": _meta(soname="libcore.so.1", exports=["x"])})
        result = compare_bundle(old, new, [])
        assert not any(
            f.kind == ChangeKind.BUNDLE_LIBRARY_REMOVED for f in result.bundle_findings
        )

    def test_removed_library_with_intra_consumer_fires(self) -> None:
        old = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["util_x"]),
                "libalgo.so": _meta(
                    soname="libalgo.so.1", needed=["libcore.so.1"], imports=["util_x"]
                ),
            }
        )
        new = _snapshot(
            {
                "libalgo.so": _meta(
                    soname="libalgo.so.1", needed=["libcore.so.1"], imports=["util_x"]
                ),
            }
        )
        result = compare_bundle(old, new, [])
        removed = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_LIBRARY_REMOVED
        ]
        assert len(removed) == 1
        assert removed[0].symbol == "libcore.so"
        assert "libalgo.so" in removed[0].affected_libraries


# ---------------------------------------------------------------------------
# bundle_intra_type_changed (cross-DSO type drift)
# ---------------------------------------------------------------------------


class TestIntraTypeChanged:
    def test_type_change_visible_in_sibling_emits_finding(self) -> None:
        # libcore defines DataCollection; libalgo's mangled symbol embeds
        # the type name (template instantiation). When libcore's diff
        # reports type_size_changed on DataCollection and a sibling
        # exports a symbol containing that name, the bundle layer emits
        # bundle_intra_type_changed.
        new = _snapshot(
            {
                "libcore.so": _meta(
                    soname="libcore.so.1",
                    exports=["DataCollection_ctor"],
                ),
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libcore.so.1"],
                    exports=["_Z3runP14DataCollection"],
                ),
            }
        )
        diff = _diff(
            "libcore.so",
            Change(
                kind=ChangeKind.TYPE_SIZE_CHANGED,
                symbol="DataCollection",
                description="sizeof changed",
            ),
        )
        result = compare_bundle(new, new, [diff])
        type_findings = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_TYPE_CHANGED
        ]
        assert len(type_findings) == 1
        assert type_findings[0].symbol == "DataCollection"
        assert type_findings[0].consumer_library == "libalgo.so"
        assert type_findings[0].provider_library == "libcore.so"

    def test_dedupe_multiple_low_level_changes_same_type(self) -> None:
        # A single type can produce several low-level diffs (size +
        # alignment + field_removed); the bundle layer must collapse
        # those into one cross-DSO finding per (consumer, provider, type).
        new = _snapshot(
            {
                "libcore.so": _meta(
                    soname="libcore.so.1",
                    exports=["DataCollection_ctor"],
                ),
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libcore.so.1"],
                    exports=["_Z3runP14DataCollection"],
                ),
            }
        )
        diff = _diff(
            "libcore.so",
            Change(
                kind=ChangeKind.TYPE_SIZE_CHANGED,
                symbol="DataCollection",
                description="",
            ),
            Change(
                kind=ChangeKind.TYPE_ALIGNMENT_CHANGED,
                symbol="DataCollection",
                description="",
            ),
            Change(
                kind=ChangeKind.TYPE_FIELD_REMOVED,
                symbol="DataCollection",
                description="",
            ),
        )
        result = compare_bundle(new, new, [diff])
        type_findings = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_TYPE_CHANGED
        ]
        assert len(type_findings) == 1


# ---------------------------------------------------------------------------
# A3 (ADR-027 D3.2) — reachability-filtered cross-DSO type change
# ---------------------------------------------------------------------------


class TestIntraTypeReachability:
    def _bundle_with_consumer(self, consumer_meta: ElfMetadata) -> BundleSnapshot:
        return _snapshot(
            {
                "libcore.so": _meta(
                    soname="libcore.so.1",
                    exports=["DataCollection_ctor"],
                ),
                "libalgo.so": consumer_meta,
            }
        )

    def _core_diff(self) -> DiffResult:
        return _diff(
            "libcore.so",
            Change(
                kind=ChangeKind.TYPE_SIZE_CHANGED,
                symbol="DataCollection",
                description="sizeof changed",
            ),
        )

    def test_public_consumer_use_stays_breaking(self) -> None:
        # libalgo exports a symbol embedding the changed type → on its public
        # surface → full-confidence cross-DSO break (not demoted).
        consumer = ElfMetadata(
            soname="libalgo.so.1",
            needed=["libcore.so.1"],
            symbols=[ElfSymbol(name="_Z3runP14DataCollection", visibility="default")],
        )
        result = compare_bundle(
            self._bundle_with_consumer(consumer),
            self._bundle_with_consumer(consumer),
            [self._core_diff()],
        )
        f = next(
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_TYPE_CHANGED
        )
        assert f.effective_verdict is None
        assert result.bundle_verdict == Verdict.BREAKING

    def test_internal_only_consumer_use_demoted(self) -> None:
        # libalgo references the type only via a hidden (non-exported) symbol →
        # the change cannot reach its public ABI surface → demoted to risk,
        # disclosed (never dropped), so the bundle verdict is not BREAKING.
        consumer = ElfMetadata(
            soname="libalgo.so.1",
            needed=["libcore.so.1"],
            symbols=[ElfSymbol(name="_Z3runP14DataCollection", visibility="hidden")],
        )
        result = compare_bundle(
            self._bundle_with_consumer(consumer),
            self._bundle_with_consumer(consumer),
            [self._core_diff()],
        )
        f = next(
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_TYPE_CHANGED
        )
        assert f.effective_verdict == Verdict.COMPATIBLE_WITH_RISK
        assert f.modulation_reason == "consumer-internal-use"
        assert f.modulation_rule == "bundle-reachability"
        # The demotion reaches the bundle verdict (it lowers via to_change and
        # classifies through effective_category): risk, not a hard break.
        assert result.bundle_verdict == Verdict.COMPATIBLE_WITH_RISK
        # Disclosed, never dropped.
        assert f in result.bundle_findings

    def test_internal_only_demotion_not_a_junit_failure(self) -> None:
        # A risk-demoted BUNDLE_INTRA_TYPE_CHANGED must follow its EFFECTIVE risk
        # category in JUnit, not the original breaking kind's severity — so by
        # default it is NOT a <failure> (Codex review).
        from abicheck.junit_report import _is_failure

        demoted = Change(
            kind=ChangeKind.BUNDLE_INTRA_TYPE_CHANGED,
            symbol="DataCollection",
            description="internal-only cross-DSO change",
            effective_verdict=Verdict.COMPATIBLE_WITH_RISK,
            modulation_reason="consumer-internal-use",
        )
        result = DiffResult(old_version="1.0", new_version="2.0", library="lib")
        assert not _is_failure(demoted, result, result._effective_kind_sets())


# ---------------------------------------------------------------------------
# bundle_intra_dep_resolved_to_different_version (gnu.version_d drift)
# ---------------------------------------------------------------------------


class TestVersionDrift:
    def test_default_version_drift_emits_finding(self) -> None:
        # core_fn is exported in old at GLIBCXX_3.4.20, in new at
        # GLIBCXX_3.4.30; libalgo imports it. Bundle layer flags the
        # version drift as COMPATIBLE_WITH_RISK.
        old = _snapshot(
            {
                "libcore.so": _meta(
                    soname="libcore.so.1",
                    exports=["core_fn"],
                    export_versions={"core_fn": "GLIBCXX_3.4.20"},
                ),
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libcore.so.1"],
                    imports=["core_fn"],
                ),
            }
        )
        new = _snapshot(
            {
                "libcore.so": _meta(
                    soname="libcore.so.1",
                    exports=["core_fn"],
                    export_versions={"core_fn": "GLIBCXX_3.4.30"},
                ),
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libcore.so.1"],
                    imports=["core_fn"],
                ),
            }
        )
        result = compare_bundle(old, new, [])
        drift = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_DEP_VERSION_DRIFT
        ]
        assert len(drift) == 1
        assert drift[0].old_value == "GLIBCXX_3.4.20"
        assert drift[0].new_value == "GLIBCXX_3.4.30"
        assert "libalgo.so" in drift[0].affected_libraries


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


class TestManifest:
    def test_promised_symbol_missing_is_breaking(self) -> None:
        manifest = InstantiationManifest(
            entries=(
                ManifestEntry(
                    symbol="promised_a", library="libcore.so", optional_provider=False
                ),
                ManifestEntry(
                    symbol="promised_b", library=None, optional_provider=True
                ),
            )
        )
        old = _snapshot(
            {
                "libcore.so": _meta(
                    soname="libcore.so.1", exports=["promised_a", "promised_b"]
                ),
            }
        )
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["promised_a"]),
            }
        )
        result = compare_bundle(old, new, [], manifest=manifest)
        kinds = [f.kind for f in result.bundle_findings]
        assert ChangeKind.BUNDLE_MANIFEST_INSTANTIATION_REMOVED in kinds
        missing = next(
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_MANIFEST_INSTANTIATION_REMOVED
        )
        assert missing.symbol == "promised_b"

    def test_wrong_provider_when_required(self) -> None:
        manifest = InstantiationManifest(
            entries=(
                ManifestEntry(
                    symbol="x", library="libcore.so", optional_provider=False
                ),
            )
        )
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1"),
                "libother.so": _meta(soname="libother.so.1", exports=["x"]),
            }
        )
        result = compare_bundle(new, new, [], manifest=manifest)
        wrong = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_MANIFEST_INSTANTIATION_REMOVED
        ]
        assert len(wrong) == 1
        assert "libother.so" in (wrong[0].new_value or "")

    def test_optional_provider_accepts_any_sibling(self) -> None:
        manifest = InstantiationManifest(
            entries=(ManifestEntry(symbol="x", library=None, optional_provider=True),)
        )
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1"),
                "libother.so": _meta(soname="libother.so.1", exports=["x"]),
            }
        )
        result = compare_bundle(new, new, [], manifest=manifest)
        assert not any(
            f.kind == ChangeKind.BUNDLE_MANIFEST_INSTANTIATION_REMOVED
            for f in result.bundle_findings
        )

    def test_load_manifest_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "m.yaml"
        path.write_text(
            "version: 1\n"
            "provides:\n"
            "  - symbol: train_v2\n"
            "    library: libfoo.so.1\n"
            "    optional_provider: false\n",
        )
        m = load_manifest(path)
        assert len(m.entries) == 1
        assert m.entries[0].symbol == "train_v2"
        assert m.entries[0].library == "libfoo.so.1"
        assert m.entries[0].optional_provider is False

    def test_load_manifest_json(self, tmp_path: Path) -> None:
        path = tmp_path / "m.json"
        path.write_text(
            '{"version": 1, "provides": [{"symbol": "x", "library": "libfoo.so.1"}]}',
        )
        m = load_manifest(path)
        assert m.entries[0].symbol == "x"
        assert m.entries[0].optional_provider is True  # default

    def test_load_manifest_rejects_missing_provides(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("version: 1\n")
        with pytest.raises(ValueError, match="missing top-level 'provides:'"):
            load_manifest(path)

    def test_load_manifest_rejects_provides_as_dict(self, tmp_path: Path) -> None:
        # `provides: {}` passes the existence check but is not a list —
        # must raise a clear error rather than a confusing per-entry error.
        path = tmp_path / "bad_dict.yaml"
        path.write_text("version: 1\nprovides: {}\n")
        with pytest.raises(ValueError, match="missing top-level 'provides:'"):
            load_manifest(path)

    def test_load_manifest_rejects_provides_as_string(self, tmp_path: Path) -> None:
        # `provides: "foo"` is likewise not a list.
        path = tmp_path / "bad_str.json"
        path.write_text('{"version": 1, "provides": "foo"}')
        with pytest.raises(ValueError, match="missing top-level 'provides:'"):
            load_manifest(path)

    def test_load_manifest_valid_list_still_loads(self, tmp_path: Path) -> None:
        # Regression guard: a well-formed manifest with a list value for
        # `provides` must continue to load without error.
        path = tmp_path / "ok.json"
        path.write_text(
            '{"version": 1, "provides": ['
            '{"symbol": "ok_func", "library": "libfoo.so.1"}'
            "]}",
        )
        m = load_manifest(path)
        assert len(m.entries) == 1
        assert m.entries[0].symbol == "ok_func"

    def test_load_manifest_rejects_string_optional_provider(
        self, tmp_path: Path
    ) -> None:
        # YAML quote-ifies bool-looking strings; users hand-editing
        # `optional_provider: "false"` (string) would silently get
        # parsed as truthy by bool() — validate strictly instead.
        path = tmp_path / "stringy.json"
        path.write_text(
            '{"version": 1, "provides": ['
            '{"symbol": "x", "library": "lib.so.1", "optional_provider": "false"}'
            "]}",
        )
        with pytest.raises(ValueError, match="optional_provider.*must be a boolean"):
            load_manifest(path)

    def test_load_manifest_rejects_int_optional_provider(self, tmp_path: Path) -> None:
        path = tmp_path / "inty.json"
        path.write_text(
            '{"version": 1, "provides": [{"symbol": "x", "optional_provider": 1}]}',
        )
        with pytest.raises(ValueError, match="optional_provider.*must be a boolean"):
            load_manifest(path)

    def test_load_manifest_pattern_form(self, tmp_path: Path) -> None:
        path = tmp_path / "patterns.yaml"
        path.write_text(
            "version: 1\n"
            "provides:\n"
            '  - pattern: "oneapi::dal::train_ops<*>*"\n'
            "    library: libonedal_core.so.1\n"
            "    optional_provider: false\n",
        )
        m = load_manifest(path)
        assert len(m.entries) == 1
        assert m.entries[0].pattern == "oneapi::dal::train_ops<*>*"
        assert m.entries[0].kind() == "pattern"

    def test_load_manifest_template_form(self, tmp_path: Path) -> None:
        path = tmp_path / "templates.yaml"
        path.write_text(
            "version: 1\n"
            "provides:\n"
            "  - template: oneapi::dal::train_ops\n"
            "    instantiations:\n"
            '      - {Float: float,  Method: "method::dense", Task: "task::train"}\n'
            '      - {Float: double, Method: "method::dense", Task: "task::train"}\n',
        )
        m = load_manifest(path)
        assert m.entries[0].template == "oneapi::dal::train_ops"
        assert len(m.entries[0].instantiations) == 2
        assert m.entries[0].instantiations[0]["Float"] == "float"
        assert m.entries[0].kind() == "template"

    def test_load_manifest_rejects_multiple_shape_keys(self, tmp_path: Path) -> None:
        path = tmp_path / "mixed.yaml"
        path.write_text(
            'version: 1\nprovides:\n  - symbol: foo\n    pattern: "foo*"\n',
        )
        with pytest.raises(ValueError, match="conflicting fields"):
            load_manifest(path)

    def test_load_manifest_rejects_missing_shape_key(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.yaml"
        path.write_text(
            "version: 1\nprovides:\n  - library: libfoo.so.1\n",
        )
        with pytest.raises(ValueError, match="must have one of 'symbol'"):
            load_manifest(path)

    def test_load_manifest_template_needs_instantiations(self, tmp_path: Path) -> None:
        path = tmp_path / "no-insts.yaml"
        path.write_text(
            "version: 1\nprovides:\n  - template: oneapi::dal::train_ops\n",
        )
        with pytest.raises(ValueError, match="non-empty 'instantiations:'"):
            load_manifest(path)


# ---------------------------------------------------------------------------
# Pattern and template matching against the bundle
# ---------------------------------------------------------------------------


class TestManifestPatternMatching:
    def test_pattern_matches_mangled_extern_c_symbols(self) -> None:
        # extern "C" symbols aren't demangled (demangle returns None for
        # them); the matcher falls back to the mangled name. This means
        # patterns work uniformly for C and C++ symbols.
        new = _snapshot(
            {
                "libcore.so": _meta(
                    soname="libcore.so.1",
                    exports=[
                        "onedal_train_float_dense",
                        "onedal_train_float_sparse",
                        "onedal_predict_float_dense",
                    ],
                ),
            }
        )
        manifest = InstantiationManifest(
            entries=(ManifestEntry(pattern="onedal_train_*", optional_provider=True),)
        )
        result = compare_bundle(new, new, [], manifest=manifest)
        assert not any(
            f.kind == ChangeKind.BUNDLE_MANIFEST_INSTANTIATION_REMOVED
            for f in result.bundle_findings
        )

    def test_pattern_with_no_match_emits_removed(self) -> None:
        new = _snapshot({"libcore.so": _meta(soname="libcore.so.1")})
        manifest = InstantiationManifest(
            entries=(ManifestEntry(pattern="onedal_train_*", optional_provider=True),)
        )
        result = compare_bundle(new, new, [], manifest=manifest)
        removed = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_MANIFEST_INSTANTIATION_REMOVED
        ]
        assert len(removed) == 1
        assert removed[0].symbol == "onedal_train_*"

    def test_template_form_matches_demangled_substring(self) -> None:
        # The expanded form "ns::T<arg1, arg2>" is checked as substring
        # against the demangled name. For extern "C" symbols (no
        # demangling), the matcher falls back to the mangled name; we
        # set up symbol names that contain the substring so the test
        # doesn't depend on cxxfilt availability.
        new = _snapshot(
            {
                "libcore.so": _meta(
                    soname="libcore.so.1",
                    exports=[
                        "ns::T<float, dense>_ctor",  # carries the expanded form
                        "ns::T<double, sparse>_ctor",
                    ],
                ),
            }
        )
        manifest = InstantiationManifest(
            entries=(
                ManifestEntry(
                    template="ns::T",
                    instantiations=({"P1": "float", "P2": "dense"},),
                    optional_provider=True,
                ),
                ManifestEntry(
                    template="ns::T",
                    instantiations=({"P1": "int", "P2": "dense"},),  # not exported
                    optional_provider=True,
                ),
            )
        )
        result = compare_bundle(new, new, [], manifest=manifest)
        removed = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_MANIFEST_INSTANTIATION_REMOVED
        ]
        # First entry must NOT fire (float,dense is present);
        # second entry MUST fire (int,dense is not).
        assert len(removed) == 1
        assert "int" in removed[0].description
        assert "dense" in removed[0].description

    def test_template_partial_instantiation_match_within_one_entry(self) -> None:
        # Regression for CodeRabbit feedback: a single template entry
        # with multiple instantiations must check each independently.
        # Previously the matcher pooled all expansions and declared the
        # entry satisfied iff *any* matched — masking partial regressions
        # where, say, two of four promised instantiations were dropped.
        new = _snapshot(
            {
                "libcore.so": _meta(
                    soname="libcore.so.1",
                    exports=[
                        "ns::T<float, dense>_ctor",
                        "ns::T<double, dense>_ctor",
                        # <float, sparse> and <double, sparse> NOT exported
                    ],
                ),
            }
        )
        manifest = InstantiationManifest(
            entries=(
                ManifestEntry(
                    template="ns::T",
                    instantiations=(
                        {"P1": "float", "P2": "dense"},  # exported
                        {"P1": "float", "P2": "sparse"},  # MISSING
                        {"P1": "double", "P2": "dense"},  # exported
                        {"P1": "double", "P2": "sparse"},  # MISSING
                    ),
                    optional_provider=True,
                ),
            )
        )
        result = compare_bundle(new, new, [], manifest=manifest)
        removed = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_MANIFEST_INSTANTIATION_REMOVED
        ]
        # Exactly two findings, one for each missing instantiation.
        assert len(removed) == 2
        missing_symbols = {f.symbol for f in removed}
        assert "ns::T<float, sparse>" in missing_symbols
        assert "ns::T<double, sparse>" in missing_symbols
        # Present instantiations must NOT have generated a finding.
        assert all("ns::T<float, dense>" not in f.symbol for f in removed)

    def test_demangle_invoked_once_per_symbol_across_many_targets(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Performance regression guard: _detect_manifest_drift should
        # build the demangle index once per snapshot and reuse it
        # across every target — *not* re-demangle the whole bundle for
        # each instantiation. The naïve implementation would call
        # demangle() N_symbols × N_targets times; here we assert it's
        # exactly N_symbols × 2 snapshots (old + new).
        call_count = [0]

        # Wrap demangle to count calls. Monkeypatch the import in
        # _build_demangled_index by patching the module-level demangle
        # if it's imported inside the function.
        import abicheck.demangle as demangle_mod

        original_demangle = demangle_mod.demangle

        def counting_demangle(name: str) -> str | None:
            call_count[0] += 1
            return original_demangle(name)

        monkeypatch.setattr(demangle_mod, "demangle", counting_demangle)

        new = _snapshot(
            {
                "libcore.so": _meta(
                    soname="libcore.so.1",
                    exports=[
                        "ns::T<float, dense>_ctor",
                        "ns::T<float, sparse>_ctor",
                        "ns::T<double, dense>_ctor",
                        "ns::T<double, sparse>_ctor",
                        "unrelated_symbol_1",
                        "unrelated_symbol_2",
                    ],
                ),
            }
        )
        n_symbols = sum(len(m.symbols) for m in new.metadata.values())

        # Manifest with many targets — naïve scaling would multiply.
        manifest = InstantiationManifest(
            entries=(
                ManifestEntry(
                    template="ns::T",
                    instantiations=tuple(
                        {"P1": p1, "P2": p2}
                        for p1 in ("float", "double", "int", "long")
                        for p2 in ("dense", "sparse", "csr", "csc")
                    ),  # 16 targets
                    optional_provider=True,
                ),
            )
        )
        compare_bundle(new, new, [], manifest=manifest)
        # Expected: one full pass per snapshot (old + new), each
        # producing n_symbols demangle calls. No per-target rescans.
        assert call_count[0] == 2 * n_symbols, (
            f"demangle called {call_count[0]} times; expected exactly "
            f"{2 * n_symbols} (one pass each over old + new snapshot)"
        )

    def test_required_provider_matches_soname(self) -> None:
        # Manifest format documents both filename keys (libcore.so) and
        # SONAMEs (libcore.so.1) for the `library:` field. The bundle
        # layer must accept either; if the manifest names libcore.so.1
        # (a SONAME) and the candidate provider's SONAME matches, that's
        # a hit, no spurious BUNDLE_MANIFEST_INSTANTIATION_REMOVED.
        manifest = InstantiationManifest(
            entries=(
                ManifestEntry(
                    symbol="train_v2",
                    library="libcore.so.1",  # SONAME, not filename key
                    optional_provider=False,
                ),
            )
        )
        new = _snapshot(
            {
                "libcore.so": _meta(
                    soname="libcore.so.1",
                    exports=["train_v2"],
                ),
            }
        )
        result = compare_bundle(new, new, [], manifest=manifest)
        assert not any(
            f.kind == ChangeKind.BUNDLE_MANIFEST_INSTANTIATION_REMOVED
            for f in result.bundle_findings
        )

    def test_new_promised_symbol_emits_addition(self) -> None:
        # Symbol present in new manifest, absent from old bundle exports
        # but present in new bundle. Bundle layer emits
        # BUNDLE_MANIFEST_INSTANTIATION_ADDED.
        manifest = InstantiationManifest(
            entries=(
                ManifestEntry(symbol="new_train", library=None, optional_provider=True),
            )
        )
        old = _snapshot({"libcore.so": _meta(soname="libcore.so.1")})
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["new_train"]),
            }
        )
        result = compare_bundle(old, new, [], manifest=manifest)
        added = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_MANIFEST_INSTANTIATION_ADDED
        ]
        assert len(added) == 1
        assert added[0].symbol == "new_train"


# ---------------------------------------------------------------------------
# system_providers allow-list (--bundle-system-providers flag)
# ---------------------------------------------------------------------------


class TestSystemProvidersAllowList:
    def test_user_extended_providers_suppresses_finding(self) -> None:
        # A consumer imports an out-of-bundle symbol; DT_NEEDED includes
        # a sibling AND a user-supplied external lib (libcustom.so.1).
        # Built-in heuristic doesn't know libcustom; without the
        # --bundle-system-providers extension the symbol fires.
        # With it, the symbol is treated as system-provided and the
        # finding is suppressed.
        new = _snapshot(
            {
                "libfoo.so": _meta(
                    soname="libfoo.so.1",
                    needed=["libcore.so.1", "libcustom.so.1"],
                    imports=["__cxa_atexit"],  # known system symbol
                ),
                "libcore.so": _meta(soname="libcore.so.1", exports=["dummy"]),
            }
        )
        # Even without the extension, __cxa_atexit is on the default
        # symbol allow-list and the finding is suppressed.
        baseline = compare_bundle(new, new, per_library_results=[])
        with_extras = compare_bundle(
            new,
            new,
            per_library_results=[],
            system_providers=["libcustom.so.1"],
        )
        # Sanity: neither path should report __cxa_atexit as missing.
        for r in (baseline, with_extras):
            assert not any(
                f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
                and f.symbol == "__cxa_atexit"
                for f in r.bundle_findings
            )


# ---------------------------------------------------------------------------
# Audit-mode (no old side) bundle finding — ADR-056, scan --artifact-set
# ---------------------------------------------------------------------------


class TestUnresolvedIntraDependency:
    """`_detect_unresolved_intra_dependency` — the audit-mode sibling of
    `_detect_intra_dep_removed` (no old side, single resolution graph)."""

    def _detect(
        self, snapshot: BundleSnapshot, system_providers: set[str] | None = None
    ):
        from abicheck.bundle import _detect_unresolved_intra_dependency

        return _detect_unresolved_intra_dependency(snapshot, system_providers or set())

    def test_detects_missing_provider(self) -> None:
        new = _snapshot(
            {
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    imports=["mystery_symbol"],
                ),
            }
        )
        findings = self._detect(new)
        assert len(findings) == 1
        assert findings[0].kind == ChangeKind.BUNDLE_UNRESOLVED_INTRA_DEPENDENCY
        assert findings[0].symbol == "mystery_symbol"
        assert findings[0].consumer_library == "libalgo.so"

    def test_resolved_import_no_finding(self) -> None:
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["core_add"]),
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libcore.so.1"],
                    imports=["core_add"],
                ),
            }
        )
        assert self._detect(new) == []

    def test_skips_weak_import(self) -> None:
        from abicheck.elf_metadata import ElfImport, SymbolBinding

        meta = _meta(soname="libplugin.so.1")
        meta.imports.append(ElfImport(name="optional_hook", binding=SymbolBinding.WEAK))
        new = _snapshot({"libplugin.so": meta})
        assert self._detect(new) == []

    def test_version_mismatch_produces_finding(self) -> None:
        # Consumer requires foo@V2; the only intra-set provider exports foo@V1.
        # A real, load-time-unresolvable mismatch (P1 regression).
        new = _snapshot(
            {
                "libcore.so": _meta(
                    soname="libcore.so.1",
                    exports=["foo"],
                    export_versions={"foo": "V1"},
                ),
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libcore.so.1"],
                    imports=["foo"],
                    import_versions={"foo": "V2"},
                ),
            }
        )
        findings = self._detect(new)
        assert any(
            f.kind == ChangeKind.BUNDLE_UNRESOLVED_INTRA_DEPENDENCY
            and f.symbol == "foo"
            for f in findings
        )

    def test_unversioned_import_not_resolved_by_nondefault_only_export(
        self,
    ) -> None:
        # P2 regression (Codex review): a provider exporting a symbol only
        # as a non-default versioned definition (foo@V1, not foo@@V1)
        # cannot satisfy an unversioned consumer reference -- the dynamic
        # linker requires a *default* definition for that. ProviderEntry
        # previously dropped ElfSymbol.is_default entirely, so any
        # reachable provider of the bare symbol name resolved an
        # unversioned import regardless of default-ness.
        provider_meta = _meta(soname="libcore.so.1")
        provider_meta.symbols.append(
            ElfSymbol(name="foo", visibility="default", version="V1", is_default=False)
        )
        new = _snapshot(
            {
                "libcore.so": provider_meta,
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libcore.so.1"],
                    imports=["foo"],
                ),
            }
        )
        findings = self._detect(new)
        assert any(
            f.kind == ChangeKind.BUNDLE_UNRESOLVED_INTRA_DEPENDENCY
            and f.symbol == "foo"
            for f in findings
        )

    def test_version_soname_target_matches_but_version_differs(self) -> None:
        # Consumer's verneed pins the exact provider soname (liba.so.1) AND
        # requires foo@V2 from it; liba.so is reachable and does export foo,
        # but only at V1. The version_soname branch must still check
        # p.version == consumer.version, not just p.library == target_lib
        # (P1 regression: the soname-precise match alone let a wrong-version
        # export on the *correct* library read as resolved).
        new = _snapshot(
            {
                "liba.so": _meta(
                    soname="liba.so.1",
                    exports=["foo"],
                    export_versions={"foo": "V1"},
                ),
                "libconsumer.so": _meta(
                    soname="libconsumer.so.1",
                    needed=["liba.so.1"],
                    imports=["foo"],
                    import_versions={"foo": "V2"},
                    import_version_sonames={"foo": "liba.so.1"},
                ),
            }
        )
        findings = self._detect(new)
        assert any(
            f.kind == ChangeKind.BUNDLE_UNRESOLVED_INTRA_DEPENDENCY
            and f.symbol == "foo"
            for f in findings
        )

    def test_same_label_different_provider_produces_finding(self) -> None:
        # Consumer's verneed targets liba.so for foo@V1; liba.so no longer
        # exports it, but unrelated sibling libb.so also exports foo@V1.
        # Label-only matching would wrongly accept libb.so.
        new = _snapshot(
            {
                "liba.so": _meta(soname="liba.so.1"),  # dropped foo
                "libb.so": _meta(
                    soname="libb.so.1",
                    exports=["foo"],
                    export_versions={"foo": "V1"},
                ),
                "libconsumer.so": _meta(
                    soname="libconsumer.so.1",
                    needed=["liba.so.1", "libb.so.1"],
                    imports=["foo"],
                    import_versions={"foo": "V1"},
                    import_version_sonames={"foo": "liba.so.1"},
                ),
            }
        )
        findings = self._detect(new)
        assert any(
            f.kind == ChangeKind.BUNDLE_UNRESOLVED_INTRA_DEPENDENCY
            and f.symbol == "foo"
            for f in findings
        )

    def test_mixed_intra_and_external_consumer_not_suppressed(self) -> None:
        # P1 soundness fix: a consumer depending on both an intra-set
        # library (which stopped exporting the symbol) and an ordinary
        # external one (libc.so.6) must still produce the finding — the
        # coarse allow-list suppression only applies to a purely-external
        # consumer.
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1"),  # stopped exporting
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libcore.so.1", "libc.so.6"],
                    imports=["core_symbol"],
                ),
            }
        )
        from abicheck.bundle import DEFAULT_SYSTEM_PROVIDERS

        findings = self._detect(new, set(DEFAULT_SYSTEM_PROVIDERS))
        assert any(
            f.kind == ChangeKind.BUNDLE_UNRESOLVED_INTRA_DEPENDENCY
            and f.symbol == "core_symbol"
            for f in findings
        )

    def test_purely_external_consumer_suppressed_with_allow_list(self) -> None:
        new = _snapshot(
            {
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libvendor.so.1"],
                    imports=["vendor_init"],
                ),
            }
        )
        without_allow_list = self._detect(new)
        assert any(f.symbol == "vendor_init" for f in without_allow_list)

        with_allow_list = self._detect(new, {"libvendor.so.1"})
        assert not any(f.symbol == "vendor_init" for f in with_allow_list)

    def test_unversioned_zero_edges_not_suppressed(self) -> None:
        # Vacuous-all([])-guard regression: a consumer with zero non-intra
        # DT_NEEDED edges must not be suppressed merely because all() over
        # an empty list is vacuously True.
        new = _snapshot(
            {
                "libalgo.so": _meta(soname="libalgo.so.1", imports=["orphan_symbol"]),
            }
        )
        findings = self._detect(new, {"anything"})
        assert any(f.symbol == "orphan_symbol" for f in findings)

    def test_unreachable_provider_still_produces_finding(self) -> None:
        # libconsumer imports foo but has no DT_NEEDED path (direct or
        # transitive) to unrelated sibling libplugin — merely including
        # libplugin in the set must not count as resolving the import.
        new = _snapshot(
            {
                "libconsumer.so": _meta(soname="libconsumer.so.1", imports=["foo"]),
                "libplugin.so": _meta(soname="libplugin.so.1", exports=["foo"]),
            }
        )
        findings = self._detect(new)
        assert any(f.symbol == "foo" for f in findings)

    def test_reachable_provider_via_transitive_dependency(self) -> None:
        # libalgo -> libcore -> libbase; libbase provides the symbol.
        new = _snapshot(
            {
                "libbase.so": _meta(soname="libbase.so.1", exports=["base_fn"]),
                "libcore.so": _meta(soname="libcore.so.1", needed=["libbase.so.1"]),
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libcore.so.1"],
                    imports=["base_fn"],
                ),
            }
        )
        assert self._detect(new) == []


class TestAuditBundleDuplicateSoname:
    """P2 regression (Codex review): two distinct set members sharing the
    same DT_SONAME make provider resolution ambiguous --
    provider_library_for_soname() (first-metadata-match) and
    _compute_resolution_graph()'s reverse-soname map (last-match-wins)
    disagreed on which library a shared soname resolves to, so the same
    DT_NEEDED edge could be classified against the wrong provider and
    produce a false bundle_unresolved_intra_dependency finding.
    audit_bundle() now rejects the ambiguity outright instead of guessing.
    """

    def test_rejects_duplicate_soname(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import abicheck.bundle as bundle_mod
        from abicheck.bundle import ArtifactSetError, audit_bundle

        libraries = {
            "liba.so": Path("liba.so"),
            "libb.so": Path("libb.so"),
        }

        def _fake_snapshot(libs):
            return _snapshot(
                {
                    "liba.so": _meta(soname="libshared.so.1"),
                    "libb.so": _meta(soname="libshared.so.1", exports=["foo"]),
                }
            )

        monkeypatch.setattr(bundle_mod, "build_bundle_snapshot", _fake_snapshot)
        with pytest.raises(ArtifactSetError, match="ambiguous duplicate SONAME"):
            audit_bundle(libraries)

    def test_distinct_sonames_not_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.bundle as bundle_mod
        from abicheck.bundle import audit_bundle

        libraries = {
            "liba.so": Path("liba.so"),
            "libb.so": Path("libb.so"),
        }

        def _fake_snapshot(libs):
            return _snapshot(
                {
                    "liba.so": _meta(soname="liba.so.1"),
                    "libb.so": _meta(soname="libb.so.1"),
                }
            )

        monkeypatch.setattr(bundle_mod, "build_bundle_snapshot", _fake_snapshot)
        result = audit_bundle(libraries)
        assert result.findings == []


class TestBuildBundleSnapshotDeadlineCheckpoint:
    """P2 regression (Codex review): build_bundle_snapshot()'s per-library
    parsing loop must call deadline.check() between members so a slow or
    pathological ELF set aborts as soon as an active deadline.deadline_scope
    expires, rather than only being detectable by an elapsed-time check
    after the whole snapshot finishes building.
    """

    def test_raises_when_deadline_already_expired(self) -> None:
        from abicheck import deadline
        from abicheck.bundle import build_bundle_snapshot

        with deadline.deadline_scope(-1):
            with pytest.raises(deadline.DeadlineExceeded):
                build_bundle_snapshot(
                    {"a.so": Path("/fake/a.so"), "b.so": Path("/fake/b.so")}
                )

    def test_no_deadline_is_a_no_op(self, tmp_path: Path) -> None:
        from abicheck.bundle import build_bundle_snapshot

        json_file = tmp_path / "libnotelf.so"
        json_file.write_text('{"library": "fake", "version": "1"}')
        # No deadline_scope active -- must behave exactly as before.
        snap = build_bundle_snapshot({"libnotelf.so": json_file})
        assert snap.libraries == {}


class TestReachableIntraLibrariesSymlinkAlias:
    """P2 regression (Codex review): _reachable_intra_libraries() BFS used
    provider_library_for_soname()'s independent name/soname/filename-stem
    heuristic to resolve an intra-bundle DT_NEEDED edge, which doesn't know
    about a resolved-through-symlink real filename --
    _compute_resolution_graph() classified that edge as intra correctly
    (using its own soname_to_name map, which the resolved-basename fix
    populates), but the BFS resolving the *same* edge disagreed, so a
    provider discovered via a differently-named symlink alias
    (``aaa.so -> libreal.so.1``, no DT_SONAME) was never marked reachable,
    producing a false bundle_unresolved_intra_dependency finding for a
    genuinely resolved import.
    """

    def test_provider_reachable_via_symlink_alias_real_filename(
        self, tmp_path: Path
    ) -> None:
        from abicheck.bundle import (
            _compute_resolution_graph,
            _detect_unresolved_intra_dependency,
        )

        real = tmp_path / "libreal.so.1"
        real.write_bytes(b"")
        link = tmp_path / "aaa.so"  # arbitrarily named, sorts before the target
        link.symlink_to(real)

        libraries = {
            "aaa.so": link,
            "libconsumer.so": tmp_path / "libconsumer.so",
        }
        metadata = {
            "aaa.so": _meta(soname="", exports=["foo"]),  # no DT_SONAME
            "libconsumer.so": _meta(
                soname="libconsumer.so",
                needed=["libreal.so.1"],
                imports=["foo"],
            ),
        }
        graph = _compute_resolution_graph(libraries, metadata)
        # Sanity: the edge really is classified as intra (pre-existing fix).
        assert graph.intra_needed["libconsumer.so"] == ["libreal.so.1"]

        snapshot = BundleSnapshot(
            root=tmp_path, libraries=libraries, metadata=metadata, resolution=graph
        )
        findings = _detect_unresolved_intra_dependency(snapshot, set())
        assert findings == []

    def test_version_mismatch_not_misclassified_as_external_via_symlink_alias(
        self, tmp_path: Path
    ) -> None:
        # P2 regression (Codex review): _import_is_external()'s
        # version_soname path called BundleSnapshot.is_intra_bundle_provider()
        # (the same independent heuristic just fixed above), which doesn't
        # resolve a provider retained through a differently-named symlink
        # alias with no DT_SONAME -- misclassifying it as "external" and
        # suppressing a genuine, real version-mismatch finding (consumer
        # needs foo@V2, the reachable provider only exports foo@V1).
        from abicheck.bundle import (
            _compute_resolution_graph,
            _detect_unresolved_intra_dependency,
        )

        real = tmp_path / "libreal.so.1"
        real.write_bytes(b"")
        link = tmp_path / "aaa.so"
        link.symlink_to(real)

        libraries = {
            "aaa.so": link,
            "libconsumer.so": tmp_path / "libconsumer.so",
        }
        metadata = {
            "aaa.so": _meta(soname="", exports=["foo"], export_versions={"foo": "V1"}),
            "libconsumer.so": _meta(
                soname="libconsumer.so",
                needed=["libreal.so.1"],
                imports=["foo"],
                import_versions={"foo": "V2"},
                import_version_sonames={"foo": "libreal.so.1"},
            ),
        }
        graph = _compute_resolution_graph(libraries, metadata)
        snapshot = BundleSnapshot(
            root=tmp_path, libraries=libraries, metadata=metadata, resolution=graph
        )
        findings = _detect_unresolved_intra_dependency(snapshot, set())
        assert any(
            f.kind == ChangeKind.BUNDLE_UNRESOLVED_INTRA_DEPENDENCY
            and f.symbol == "foo"
            for f in findings
        )

    @pytest.mark.skipif(
        sys.platform == "win32", reason="hard links behave differently on Windows"
    )
    def test_hard_link_alias_not_misclassified_as_unresolved(
        self, tmp_path: Path
    ) -> None:
        # P2 regression (Codex review, fresh evidence after the prior
        # hard-link dedup finding): discover_artifact_set() dedupes
        # candidate paths on filesystem identity and keeps only one
        # representative path per inode -- so when a provider has multiple
        # hard-linked names (e.g. "libfoo.so.1" and "libfoo.so.1.0.0") and a
        # consumer's DT_NEEDED names the alias that was *not* kept as the
        # representative, _compute_resolution_graph()'s soname_to_name map
        # previously had no entry for it at all: the provider read as
        # unreachable and the audit emitted a false
        # bundle_unresolved_intra_dependency. Inode dedup correctly counts
        # one binary but must still index every loader-visible alias.
        from abicheck.bundle import _detect_unresolved_intra_dependency

        representative = tmp_path / "libfoo.so.1"
        _write_elf_shared_object_stub(representative)
        alias = tmp_path / "libfoo.so.1.0.0"
        os.link(representative, alias)

        # The representative path kept by discover_artifact_set's dedup is
        # "libfoo.so.1" (first-seen); the consumer's DT_NEEDED names the
        # *other* hard-linked alias, "libfoo.so.1.0.0", which discovery
        # discarded entirely.
        libraries = {
            "libfoo.so.1": representative,
            "libconsumer.so": tmp_path / "libconsumer.so",
        }
        metadata = {
            "libfoo.so.1": _meta(soname="", exports=["foo"]),
            "libconsumer.so": _meta(
                soname="libconsumer.so",
                needed=["libfoo.so.1.0.0"],
                imports=["foo"],
            ),
        }
        graph = _compute_resolution_graph(libraries, metadata)
        snapshot = BundleSnapshot(
            root=tmp_path, libraries=libraries, metadata=metadata, resolution=graph
        )
        findings = _detect_unresolved_intra_dependency(snapshot, set())
        assert findings == []


class TestArtifactSetDiscovery:
    def test_rejects_colliding_explicit_paths(self, tmp_path: Path) -> None:
        from abicheck.bundle import ArtifactSetError, discover_artifact_set

        d1 = tmp_path / "dir1"
        d2 = tmp_path / "dir2"
        d1.mkdir()
        d2.mkdir()
        p1 = d1 / "libfoo.so"
        p2 = d2 / "libfoo.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)
        with pytest.raises(ArtifactSetError, match="colliding library identities"):
            discover_artifact_set([p1, p2], explicit=True)

    @pytest.mark.skipif(
        sys.platform == "win32", reason="symlinks need admin on Windows"
    )
    def test_dedupes_symlink_alias(self, tmp_path: Path) -> None:
        from abicheck.bundle import discover_artifact_set

        real = tmp_path / "libfoo.so.1"
        _write_elf_shared_object_stub(real)
        link = tmp_path / "libfoo.so"
        link.symlink_to(real)
        result = discover_artifact_set([real, link], explicit=False)
        assert len(result) == 1

    @pytest.mark.skipif(
        sys.platform == "win32", reason="hard links behave differently on Windows"
    )
    def test_dedupes_hard_link_alias(self, tmp_path: Path) -> None:
        # P2 regression (Codex review): Path.resolve() only follows
        # symlinks, not hard links -- two hard-linked aliases of the same
        # DSO (a real, if unusual, library-directory layout) previously
        # survived discovery as distinct members instead of being
        # deduplicated the same way a symlink alias already is.
        from abicheck.bundle import discover_artifact_set

        real = tmp_path / "libfoo.so.1"
        _write_elf_shared_object_stub(real)
        alias = tmp_path / "libfoo.so"
        try:
            os.link(real, alias)
        except OSError:
            pytest.skip("hard links unsupported in this environment")
        result = discover_artifact_set([real, alias], explicit=False)
        assert len(result) == 1

    def test_rejects_unsupported_explicit_member(self, tmp_path: Path) -> None:
        from abicheck.bundle import ArtifactSetError, discover_artifact_set

        not_elf = tmp_path / "readme.txt"
        not_elf.write_text("not a library")
        with pytest.raises(ArtifactSetError):
            discover_artifact_set([not_elf], explicit=True)

    def test_rejects_non_shared_object_elf_explicit_member(
        self, tmp_path: Path
    ) -> None:
        # P2 regression (Codex review): an explicitly-named ELF file with the
        # right magic bytes but the wrong e_type (an executable, relocatable
        # object, or core file, not ET_DYN) must still be rejected -- the
        # explicit-list form is not laxer than directory discovery, which
        # already restricts itself to real shared objects.
        from abicheck.bundle import ArtifactSetError, discover_artifact_set

        good = tmp_path / "libgood.so"
        _write_elf_shared_object_stub(good)
        executable = tmp_path / "not_a_library"
        data = bytearray(64)
        data[0:4] = b"\x7fELF"
        data[4] = 2
        data[5] = 1
        import struct as _struct

        _struct.pack_into("<H", data, 16, 2)  # e_type = ET_EXEC
        executable.write_bytes(bytes(data))
        with pytest.raises(ArtifactSetError, match="non-ELF-shared-object"):
            discover_artifact_set([good, executable], explicit=True)


# ---------------------------------------------------------------------------
# Verdict aggregation
# ---------------------------------------------------------------------------


class TestVerdictAggregation:
    def test_bundle_verdict_promotes_aggregate(self) -> None:
        # All per-library diffs are NO_CHANGE; bundle finding alone forces BREAKING.
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["x"]),
                "libalgo.so": _meta(
                    soname="libalgo.so.1",
                    needed=["libcore.so.1"],
                    imports=["x", "missing_sym"],
                ),
            }
        )
        result = compare_bundle(new, new, [])
        # The bundle layer should flag missing_sym as removed.
        assert result.bundle_verdict == Verdict.BREAKING
        assert result.verdict == Verdict.BREAKING

    def test_aggregate_takes_worst_of_per_lib_and_bundle(self) -> None:
        new = _snapshot({"libcore.so": _meta(soname="libcore.so.1")})
        # Per-library: BREAKING; bundle: NO_CHANGE.
        diff = _diff(
            "libcore.so",
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="x", description=""),
            verdict=Verdict.BREAKING,
        )
        result = compare_bundle(new, new, [diff])
        assert result.bundle_verdict == Verdict.NO_CHANGE
        assert result.per_library_verdict == Verdict.BREAKING
        assert result.verdict == Verdict.BREAKING


# ---------------------------------------------------------------------------
# Non-ELF inputs
# ---------------------------------------------------------------------------


class TestNonElfInputs:
    def test_skips_non_elf_files_silently(self, tmp_path: Path) -> None:
        # Should not raise, should not produce findings.
        from abicheck.bundle import build_bundle_snapshot

        json_file = tmp_path / "libnotelf.so"
        json_file.write_text('{"library": "fake", "version": "1"}')
        snap = build_bundle_snapshot({"libnotelf.so": json_file})
        assert snap.libraries == {}
        assert snap.metadata == {}

    def test_path_looks_like_elf_handles_missing(self, tmp_path: Path) -> None:
        from abicheck.bundle import _path_looks_like_elf

        # Non-existent path — OSError → False, no raise.
        assert _path_looks_like_elf(tmp_path / "does-not-exist.so") is False

    def test_path_looks_like_elf_accepts_magic(self, tmp_path: Path) -> None:
        from abicheck.bundle import _path_looks_like_elf

        p = tmp_path / "fake.so"
        p.write_bytes(b"\x7fELF" + b"\0" * 12)
        assert _path_looks_like_elf(p) is True

    def test_build_bundle_snapshot_with_real_elf(self) -> None:
        # Construct a minimal ELF using elftools' write APIs is heavy;
        # instead reuse a known-good system .so. This exercises the real
        # parse_elf_metadata path in build_bundle_snapshot (otherwise
        # bypassed by the in-memory _snapshot helper used elsewhere).
        from abicheck.bundle import build_bundle_snapshot

        candidate = None
        for p in (
            "/lib/x86_64-linux-gnu/libc.so.6",
            "/lib64/libc.so.6",
            "/usr/lib/libc.so.6",
            "/usr/lib/x86_64-linux-gnu/libc.so.6",
        ):
            if Path(p).is_file():
                candidate = Path(p)
                break
        if candidate is None:
            pytest.skip("no system libc available for ELF round-trip")
        snap = build_bundle_snapshot({"libc.so.6": candidate})
        assert "libc.so.6" in snap.metadata
        assert len(snap.resolution.provides) > 0


class TestBuildBundleSnapshotFromMetadata:
    """build_bundle_snapshot_from_metadata() -- the split-out primitive
    behind build_bundle_snapshot() that lets a caller holding already-parsed
    ElfMetadata (e.g. AbiSnapshot.elf from a real dump) build a real
    BundleSnapshot without any binary on disk."""

    def test_matches_build_bundle_snapshot_for_the_same_real_elf(self) -> None:
        # The wrapper and the primitive it now delegates to must agree
        # exactly for the identical real input -- this is the split's own
        # equivalence contract, not just "doesn't crash".
        from abicheck.bundle import (
            build_bundle_snapshot,
            build_bundle_snapshot_from_metadata,
        )
        from abicheck.elf_metadata import parse_elf_metadata

        candidate = None
        for p in (
            "/lib/x86_64-linux-gnu/libc.so.6",
            "/lib64/libc.so.6",
            "/usr/lib/libc.so.6",
            "/usr/lib/x86_64-linux-gnu/libc.so.6",
        ):
            if Path(p).is_file():
                candidate = Path(p)
                break
        if candidate is None:
            pytest.skip("no system libc available for ELF round-trip")

        via_path = build_bundle_snapshot({"libc.so.6": candidate})
        meta = parse_elf_metadata(candidate)
        assert meta is not None
        via_metadata = build_bundle_snapshot_from_metadata(
            {"libc.so.6": meta}, paths={"libc.so.6": candidate}
        )

        assert via_metadata.root == via_path.root
        assert via_metadata.libraries == via_path.libraries
        assert via_metadata.metadata.keys() == via_path.metadata.keys()
        assert via_metadata.resolution == via_path.resolution

    def test_drops_empty_metadata_the_same_way_the_wrapper_does(self) -> None:
        from abicheck.bundle import build_bundle_snapshot_from_metadata

        empty = _meta()  # no soname, symbols, imports, or needed
        real = _meta(soname="libfoo.so.1", exports=["foo"])
        snap = build_bundle_snapshot_from_metadata(
            {"empty.so": empty, "libfoo.so.1": real}
        )
        assert set(snap.metadata) == {"libfoo.so.1"}
        assert set(snap.libraries) == {"libfoo.so.1"}

    def test_drops_none_metadata_entries(self) -> None:
        from abicheck.bundle import build_bundle_snapshot_from_metadata

        snap = build_bundle_snapshot_from_metadata({"a.so": None})  # type: ignore[dict-item]
        assert snap.metadata == {}
        assert snap.libraries == {}

    def test_synthesizes_a_path_from_the_name_when_paths_is_omitted(self) -> None:
        # No real file on disk anywhere -- the whole point of this
        # function -- so a caller that never supplies `paths` still gets a
        # usable BundleSnapshot.libraries value (its .name is what
        # _detect_soname_skew's own fallback reads).
        from abicheck.bundle import build_bundle_snapshot_from_metadata

        meta = _meta(soname="libfoo.so.1", exports=["foo"])
        snap = build_bundle_snapshot_from_metadata({"libfoo.so.1": meta})
        assert snap.libraries["libfoo.so.1"] == Path("libfoo.so.1")
        assert snap.libraries["libfoo.so.1"].name == "libfoo.so.1"

    def test_explicit_root_overrides_the_derived_default(self) -> None:
        from abicheck.bundle import build_bundle_snapshot_from_metadata

        meta = _meta(soname="libfoo.so.1", exports=["foo"])
        snap = build_bundle_snapshot_from_metadata(
            {"libfoo.so.1": meta}, root=Path("/explicit/root")
        )
        assert snap.root == Path("/explicit/root")

    def test_resolution_graph_matches_direct_construction(self) -> None:
        # Cross-checks the primitive's resolution-graph output against
        # _compute_resolution_graph() called directly (the same helper
        # _snapshot() in this file's own fixtures uses) for a real
        # provider/consumer pair.
        from abicheck.bundle import (
            _compute_resolution_graph,
            build_bundle_snapshot_from_metadata,
        )

        provider = _meta(soname="libprovider.so.1", exports=["do_thing"])
        consumer = _meta(needed=["libprovider.so.1"], imports=["do_thing"])
        metadata = {"libprovider.so.1": provider, "libconsumer.so": consumer}

        snap = build_bundle_snapshot_from_metadata(metadata)
        expected_graph = _compute_resolution_graph(
            {name: Path(name) for name in metadata}, metadata
        )
        assert snap.resolution == expected_graph

    def test_compare_bundle_end_to_end_over_metadata_only_snapshots(self) -> None:
        # The actual point of this primitive: a real ADR-023 cross-DSO
        # finding (a provider's export disappearing) computed entirely
        # from ElfMetadata, no binaries involved on either side.
        from abicheck.bundle import build_bundle_snapshot_from_metadata, compare_bundle
        from abicheck.checker_policy import ChangeKind

        old_provider = _meta(soname="libprovider.so.1", exports=["do_thing"])
        new_provider = _meta(soname="libprovider.so.1", exports=[])
        consumer = _meta(needed=["libprovider.so.1"], imports=["do_thing"])

        old_snap = build_bundle_snapshot_from_metadata(
            {"libprovider.so.1": old_provider, "libconsumer.so": consumer}
        )
        new_snap = build_bundle_snapshot_from_metadata(
            {"libprovider.so.1": new_provider, "libconsumer.so": consumer}
        )
        result = compare_bundle(old_snap, new_snap, per_library_results=[])
        kinds = {f.kind for f in result.bundle_findings}
        assert ChangeKind.BUNDLE_INTRA_DEP_REMOVED in kinds

    def test_resolution_is_independent_of_ambient_filesystem_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Without paths=, the synthesized Path(library_name) is a relative
        # path with no real file behind it -- .resolve() would silently
        # resolve it against the process's own current working directory,
        # and a hard-link-alias scan would walk whatever that resolves to.
        # An ambient libfoo.so -> libfoo.so.1 symlink sitting in cwd must
        # not change which DT_NEEDED edges resolve as intra-bundle (Codex
        # review, fresh evidence).
        from abicheck.bundle import build_bundle_snapshot_from_metadata

        # Plant a same-named symlink/file in a throwaway cwd -- if the
        # resolution graph were probing the filesystem, this would be
        # exactly the kind of ambient state that could change its output.
        (tmp_path / "libprovider.so.1").write_bytes(b"not-real-elf")
        (tmp_path / "libprovider.so").symlink_to("libprovider.so.1")
        monkeypatch.chdir(tmp_path)

        provider = _meta(soname="libprovider.so.1", exports=["do_thing"])
        consumer = _meta(needed=["libprovider.so.1"], imports=["do_thing"])
        snap = build_bundle_snapshot_from_metadata(
            {"libprovider.so.1": provider, "libconsumer.so": consumer}
        )
        # soname_to_name must contain only what the metadata itself
        # established (the soname, the canonical key, and the bare
        # filename) -- never an alias recovered by scanning cwd.
        assert snap.resolution.soname_to_name.get("libprovider.so.1") == (
            "libprovider.so.1"
        )
        assert "libprovider.so" not in snap.resolution.soname_to_name

    def test_build_bundle_snapshot_still_probes_the_filesystem(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # build_bundle_snapshot() -- the real-path wrapper, handed live
        # binaries on disk -- delegates to
        # build_bundle_snapshot_from_metadata(), whose own default is now
        # probe_filesystem=False (the fix above). Without explicitly
        # threading probe_filesystem=True through that delegation, the
        # real-path wrapper would silently lose its pre-existing
        # filesystem alias-probing (symlink targets, hard-link aliases)
        # too -- a second-round regression in the first-round fix (Codex
        # review, fresh evidence).
        import abicheck.bundle as bundle_mod

        captured: dict[str, object] = {}
        real_impl = bundle_mod.build_bundle_snapshot_from_metadata

        def _capturing(*args, **kwargs):  # type: ignore[no-untyped-def]
            captured.update(kwargs)
            return real_impl(*args, **kwargs)

        monkeypatch.setattr(
            bundle_mod, "build_bundle_snapshot_from_metadata", _capturing
        )

        _write_elf_shared_object_stub(tmp_path / "libfoo.so")
        bundle_mod.build_bundle_snapshot({"libfoo.so": tmp_path / "libfoo.so"})

        assert captured.get("probe_filesystem") is True


# ---------------------------------------------------------------------------
# BundleSnapshot.is_intra_bundle_provider
# ---------------------------------------------------------------------------


class TestIsIntraBundleProvider:
    def test_matches_filename(self) -> None:
        snap = _snapshot({"libcore.so": _meta(soname="libcore.so.1")})
        assert snap.is_intra_bundle_provider("libcore.so") is True

    def test_matches_soname(self) -> None:
        snap = _snapshot({"libcore.so": _meta(soname="libcore.so.1")})
        assert snap.is_intra_bundle_provider("libcore.so.1") is True

    def test_matches_filename_stem_against_soname(self) -> None:
        # Lookup "libcore.so.1" hits a key "libcore.so" via stem fallback.
        snap = _snapshot({"libcore.so": _meta(soname="")})
        assert snap.is_intra_bundle_provider("libcore.so.1") is True

    def test_matches_soname_stem_against_filename(self) -> None:
        snap = _snapshot({"libcore.so.1": _meta(soname="")})
        assert snap.is_intra_bundle_provider("libcore.so") is True

    def test_no_match_returns_false(self) -> None:
        snap = _snapshot({"libcore.so": _meta(soname="libcore.so.1")})
        assert snap.is_intra_bundle_provider("libother.so") is False

    def test_library_names_property(self) -> None:
        snap = _snapshot(
            {
                "libb.so": _meta(soname="libb.so.1"),
                "liba.so": _meta(soname="liba.so.1"),
            }
        )
        assert snap.library_names == ["liba.so", "libb.so"]


# ---------------------------------------------------------------------------
# BundleFinding.to_change lowering
# ---------------------------------------------------------------------------


class TestBundleFindingToChange:
    def test_lowering_with_both_consumer_and_provider(self) -> None:
        from abicheck.bundle import BundleFinding

        f = BundleFinding(
            kind=ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED,
            symbol="core_add",
            description="signature changed",
            consumer_library="libalgo.so",
            provider_library="libcore.so",
        )
        ch = f.to_change()
        assert ch.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED
        assert "libalgo.so" in ch.description
        assert "libcore.so" in ch.description

    def test_lowering_provider_only(self) -> None:
        from abicheck.bundle import BundleFinding

        f = BundleFinding(
            kind=ChangeKind.BUNDLE_LIBRARY_REMOVED,
            symbol="libcore.so",
            description="lib removed",
            provider_library="libcore.so",
        )
        ch = f.to_change()
        assert "libcore.so" in ch.description

    def test_lowering_consumer_only(self) -> None:
        from abicheck.bundle import BundleFinding

        f = BundleFinding(
            kind=ChangeKind.BUNDLE_INTRA_DEP_REMOVED,
            symbol="core_mul",
            description="missing",
            consumer_library="libalgo.so",
        )
        ch = f.to_change()
        assert "libalgo.so" in ch.description

    def test_lowering_neither(self) -> None:
        from abicheck.bundle import BundleFinding

        f = BundleFinding(
            kind=ChangeKind.BUNDLE_LIBRARY_ADDED,
            symbol="libnew.so",
            description="added",
        )
        ch = f.to_change()
        assert ch.description == "added"


# ---------------------------------------------------------------------------
# End-to-end compare-release with bundle analysis enabled
# ---------------------------------------------------------------------------


def _build_tiny_so(release_dir: Path, name: str, src: str) -> Path:
    """Compile *src* into ``release_dir/name`` (a .so file).

    Sources are kept in a *sibling* directory next to release_dir so the
    discover_shared_libraries walk inside the release scan does not pick
    them up as ELF candidates.  Skips the calling test if gcc is
    unavailable on the runner.
    """
    import shutil
    import subprocess

    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("gcc unavailable; cannot build bundle E2E fixture")
    src_dir = release_dir.parent / f"{release_dir.name}.sources"
    src_dir.mkdir(exist_ok=True)
    src_path = src_dir / f"{name}.c"
    src_path.write_text(src)
    out = release_dir / name
    soname = name.split(".so")[0] + ".so.1"
    res = subprocess.run(
        [
            gcc,
            "-shared",
            "-fPIC",
            "-g",
            "-O0",
            str(src_path),
            "-o",
            str(out),
            f"-Wl,-soname,{soname}",
        ],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        pytest.fail(f"gcc failed for {name}: {res.stderr}")
    return out


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="Uses GNU ld flags (-Wl,-soname, -Wl,--no-as-needed); "
    "Mach-O ld and link.exe don't accept them. Bundle analysis "
    "itself is ELF/Linux-only per ADR-018 / ADR-023.",
)
@pytest.mark.integration
class TestCompareReleaseBundleE2E:
    """Exercise compare-release end-to-end with the bundle layer enabled.

    These tests compile tiny C .so files at runtime so the CLI's bundle
    wiring (in abicheck/cli.py) is actually covered by tests — the
    in-memory unit tests above bypass the CLI surface and the ELF
    parsing path.
    """

    def test_compare_release_emits_bundle_findings_by_default(
        self, tmp_path: Path
    ) -> None:
        # libcore drops core_mul between old and new; libalgo still
        # imports it. Bundle layer must catch this; the CLI must surface
        # the bundle_verdict and bundle_findings in JSON output.
        import json as _json

        from click.testing import CliRunner

        from abicheck.cli import main

        old = tmp_path / "old"
        new = tmp_path / "new"
        old.mkdir()
        new.mkdir()
        _build_tiny_so(
            old,
            "libcore.so",
            "int core_add(int a, int b){return a+b;}\n"
            "int core_mul(int a, int b){return a*b;}\n",
        )
        _build_tiny_so(
            new,
            "libcore.so",
            "int core_add(int a, int b){return a+b;}\n",  # core_mul removed
        )
        # libalgo: byte-identical in old and new, still imports core_mul.
        algo_src = (
            "extern int core_add(int,int);\n"
            "extern int core_mul(int,int);\n"
            "int algo_sum(int lo, int hi){int s=0;for(int i=lo;i<=hi;++i)s=core_add(s,i);return s;}\n"
            "int algo_square(int x){return core_mul(x,x);}\n"
        )
        for side in (old, new):
            src_dir = side.parent / f"{side.name}.sources"
            src_dir.mkdir(exist_ok=True)
            src_file = src_dir / "libalgo.c"
            src_file.write_text(algo_src)
            import shutil as _shutil
            import subprocess as _sub

            gcc = _shutil.which("gcc")
            if gcc is None:
                pytest.skip("gcc unavailable; cannot build bundle E2E fixture")
            _sub.run(
                [
                    gcc,
                    "-shared",
                    "-fPIC",
                    "-g",
                    "-O0",
                    str(src_file),
                    "-o",
                    str(side / "libalgo.so"),
                    "-L",
                    str(side),
                    "-Wl,--no-as-needed",
                    "-lcore",
                    "-Wl,-soname,libalgo.so.1",
                ],
                check=True,
                capture_output=True,
            )

        result = CliRunner().invoke(
            main,
            ["compare", str(old), str(new), "--format", "json"],
        )
        # Bundle BREAKING → exit 4.
        assert result.exit_code == 4, result.output
        data = _json.loads(result.stdout)
        assert data["bundle_verdict"] == "BREAKING"
        kinds = {f["kind"] for f in data["bundle_findings"]}
        assert "bundle_intra_dep_removed" in kinds
        # The consumer attribution must point at libalgo.so.
        intra = next(
            f
            for f in data["bundle_findings"]
            if f["kind"] == "bundle_intra_dep_removed"
        )
        assert intra["consumer_library"] == "libalgo.so"
        assert intra["symbol"] == "core_mul"

    def test_compare_product_directories_matches_the_cli_result(
        self, tmp_path: Path
    ) -> None:
        # The exact same scenario as test_compare_release_emits_bundle_
        # findings_by_default above, but driven through the plain library
        # function (abicheck.product_baseline.compare_product_directories)
        # instead of the CLI -- proving it reproduces the identical
        # per-library and bundle-level result with no Click, no subprocess,
        # no directory-mode `compare` invocation.
        import shutil
        import subprocess

        from abicheck.product_baseline import compare_product_directories

        old = tmp_path / "old"
        new = tmp_path / "new"
        old.mkdir()
        new.mkdir()
        _build_tiny_so(
            old,
            "libcore.so",
            "int core_add(int a, int b){return a+b;}\n"
            "int core_mul(int a, int b){return a*b;}\n",
        )
        _build_tiny_so(
            new,
            "libcore.so",
            "int core_add(int a, int b){return a+b;}\n",  # core_mul removed
        )
        algo_src = (
            "extern int core_add(int,int);\n"
            "extern int core_mul(int,int);\n"
            "int algo_sum(int lo, int hi){int s=0;for(int i=lo;i<=hi;++i)s=core_add(s,i);return s;}\n"
            "int algo_square(int x){return core_mul(x,x);}\n"
        )
        gcc = shutil.which("gcc")
        if gcc is None:
            pytest.skip("gcc unavailable; cannot build bundle E2E fixture")
        for side in (old, new):
            src_dir = side.parent / f"{side.name}.sources"
            src_dir.mkdir(exist_ok=True)
            src_file = src_dir / "libalgo.c"
            src_file.write_text(algo_src)
            subprocess.run(
                [
                    gcc,
                    "-shared",
                    "-fPIC",
                    "-g",
                    "-O0",
                    str(src_file),
                    "-o",
                    str(side / "libalgo.so"),
                    "-L",
                    str(side),
                    "-Wl,--no-as-needed",
                    "-lcore",
                    "-Wl,-soname,libalgo.so.1",
                ],
                check=True,
                capture_output=True,
            )

        result = compare_product_directories(old, new)

        assert result.bundle_verdict == Verdict.BREAKING
        kinds = {f.kind for f in result.bundle_findings}
        assert ChangeKind.BUNDLE_INTRA_DEP_REMOVED in kinds
        intra = next(
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
        )
        assert intra.consumer_library == "libalgo.so"
        assert intra.symbol == "core_mul"
        # The per-library pass itself must have caught the removal too --
        # this is what diff_by_library indexes to attribute the bundle
        # finding to its provider.
        core_result = next(r for r in result.per_library if "libcore" in r.library)
        assert any(c.kind == ChangeKind.FUNC_REMOVED for c in core_result.changes)

    def test_compare_product_directories_handles_parallel_soname_majors(
        self, tmp_path: Path
    ) -> None:
        # A product intentionally shipping two SONAME majors side by side
        # (libfoo.so.1 and libfoo.so.2, both real, both present) is not an
        # ambiguity: bundle.discover_artifact_set() canonicalizes both to
        # "libfoo.so" and raises ArtifactSetError, which meant compare_
        # product_directories() couldn't compare such a product at all
        # (Codex review, fresh evidence). Each major must be discovered
        # and compared independently.
        from abicheck.product_baseline import compare_product_directories

        old = tmp_path / "old"
        new = tmp_path / "new"
        old.mkdir()
        new.mkdir()
        _build_tiny_so(old, "libfoo.so.1", "int foo1(void){return 1;}\n")
        _build_tiny_so(old, "libfoo.so.2", "int foo2(void){return 2;}\n")
        _build_tiny_so(new, "libfoo.so.1", "int foo1(void){return 1;}\n")
        # libfoo.so.2 drops foo2 -- a real, detectable per-library break.
        _build_tiny_so(new, "libfoo.so.2", "int foo2_renamed(void){return 2;}\n")

        result = compare_product_directories(old, new)

        by_library = {Path(r.library).name: r for r in result.per_library}
        assert set(by_library) == {"libfoo.so.1", "libfoo.so.2"}
        assert by_library["libfoo.so.1"].verdict == Verdict.NO_CHANGE
        assert any(
            c.kind == ChangeKind.FUNC_REMOVED for c in by_library["libfoo.so.2"].changes
        )

    def test_compare_release_no_bundle_analysis_opts_out(self, tmp_path: Path) -> None:
        # --no-bundle-analysis must suppress bundle output and report only
        # per-library results.
        import json as _json

        from click.testing import CliRunner

        from abicheck.cli import main

        old = tmp_path / "old"
        new = tmp_path / "new"
        old.mkdir()
        new.mkdir()
        _build_tiny_so(
            old, "libfoo.so", "int foo(void){return 1;}\nint bar(void){return 2;}\n"
        )
        _build_tiny_so(new, "libfoo.so", "int foo(void){return 1;}\n")

        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old),
                str(new),
                "--no-bundle-analysis",
                "--format",
                "json",
            ],
        )
        data = _json.loads(result.stdout)
        # bundle_verdict / bundle_findings must NOT be present.
        assert "bundle_verdict" not in data
        assert "bundle_findings" not in data

    def test_compare_release_with_manifest_emits_manifest_finding(
        self,
        tmp_path: Path,
    ) -> None:
        # Manifest lists `bar` as a promise; new bundle drops it.
        import json as _json

        from click.testing import CliRunner

        from abicheck.cli import main

        old = tmp_path / "old"
        new = tmp_path / "new"
        old.mkdir()
        new.mkdir()
        _build_tiny_so(
            old, "libfoo.so", "int foo(void){return 1;}\nint bar(void){return 2;}\n"
        )
        _build_tiny_so(new, "libfoo.so", "int foo(void){return 1;}\n")

        manifest = tmp_path / "manifest.yaml"
        manifest.write_text(
            "version: 1\n"
            "provides:\n"
            "  - symbol: foo\n"
            "    library: libfoo.so.1\n"
            "    optional_provider: false\n"
            "  - symbol: bar\n"
            "    library: libfoo.so.1\n"
            "    optional_provider: false\n",
        )

        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old),
                str(new),
                "--manifest",
                str(manifest),
                "--format",
                "json",
            ],
        )
        data = _json.loads(result.stdout)
        kinds = {f["kind"] for f in data["bundle_findings"]}
        assert "bundle_manifest_instantiation_removed" in kinds

    def test_compare_release_markdown_shows_bundle_section(
        self,
        tmp_path: Path,
    ) -> None:
        # Bundle finding must show up in the markdown summary output.
        from click.testing import CliRunner

        from abicheck.cli import main

        old = tmp_path / "old"
        new = tmp_path / "new"
        old.mkdir()
        new.mkdir()
        _build_tiny_so(
            old,
            "libcore.so",
            "int core_add(int a, int b){return a+b;}\n"
            "int core_mul(int a, int b){return a*b;}\n",
        )
        _build_tiny_so(
            new,
            "libcore.so",
            "int core_add(int a, int b){return a+b;}\n",
        )
        algo_src = (
            "extern int core_mul(int,int);\n"
            "int algo_square(int x){return core_mul(x,x);}\n"
        )
        for side in (old, new):
            src_dir = side.parent / f"{side.name}.sources"
            src_dir.mkdir(exist_ok=True)
            src_file = src_dir / "libalgo.c"
            src_file.write_text(algo_src)
            import shutil as _shutil
            import subprocess as _sub

            gcc = _shutil.which("gcc")
            if gcc is None:
                pytest.skip("gcc unavailable; cannot build bundle E2E fixture")
            _sub.run(
                [
                    gcc,
                    "-shared",
                    "-fPIC",
                    "-g",
                    "-O0",
                    str(src_file),
                    "-o",
                    str(side / "libalgo.so"),
                    "-L",
                    str(side),
                    "-Wl,--no-as-needed",
                    "-lcore",
                    "-Wl,-soname,libalgo.so.1",
                ],
                check=True,
                capture_output=True,
            )

        result = CliRunner().invoke(
            main,
            ["compare", str(old), str(new), "--bundle-cohort", "lib"],
        )
        assert "Bundle (Cross-Library) Findings" in result.stdout
        assert "bundle_intra_dep_removed" in result.stdout

    def _build_versioned_so(
        self,
        release_dir: Path,
        src: Path,
        soname: str,
    ) -> None:
        """Compile *src* into ``release_dir`` with an explicit ``-soname``.

        The output filename matches the soname (e.g. ``libfoo.so.2``) so the
        cohort detector sees the on-disk versioned name. Skips on missing gcc.
        """
        import shutil
        import subprocess

        gcc = shutil.which("gcc")
        if gcc is None:
            pytest.skip("gcc unavailable; cannot build bundle E2E fixture")
        out = release_dir / soname
        res = subprocess.run(
            [
                gcc,
                "-shared",
                "-fPIC",
                "-g",
                "-O0",
                str(src),
                "-o",
                str(out),
                f"-Wl,-soname,{soname}",
            ],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            pytest.fail(f"gcc failed for {soname}: {res.stderr}")

    def test_compare_release_emits_soname_skew_for_case84(
        self,
        tmp_path: Path,
    ) -> None:
        # Reproduce examples/case84_bundle_soname_skew end-to-end: core+dpc
        # bump SONAME .so.1 -> .so.2 while thread (deliberately) lags at .so.1.
        # Each library passes its own per-library check; the cohort invariant
        # fails. compare-release must surface BUNDLE_SONAME_SKEW and BREAK.
        import json as _json

        from click.testing import CliRunner

        from abicheck.cli import main

        case_dir = example_catalog.case_dir("case84_bundle_soname_skew")
        old = tmp_path / "v1"
        new = tmp_path / "v2"
        old.mkdir()
        new.mkdir()
        # v1: all three at .so.1
        self._build_versioned_so(old, case_dir / "onedal_core.c", "libonedal_core.so.1")
        self._build_versioned_so(
            old, case_dir / "onedal_thread.c", "libonedal_thread.so.1"
        )
        self._build_versioned_so(old, case_dir / "onedal_dpc.c", "libonedal_dpc.so.1")
        # v2: core + dpc bumped to .so.2, thread lags at .so.1
        self._build_versioned_so(new, case_dir / "onedal_core.c", "libonedal_core.so.2")
        self._build_versioned_so(
            new, case_dir / "onedal_thread.c", "libonedal_thread.so.1"
        )
        self._build_versioned_so(new, case_dir / "onedal_dpc.c", "libonedal_dpc.so.2")

        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old),
                str(new),
                "--format",
                "json",
                "--bundle-cohort",
                "libonedal_",
            ],
        )
        # Bundle BREAKING → exit 4 (matches ground_truth.json case84 == BREAKING).
        assert result.exit_code == 4, result.output
        data = _json.loads(result.stdout)
        assert data["bundle_verdict"] == "BREAKING"
        kinds = {f["kind"] for f in data["bundle_findings"]}
        assert "bundle_soname_skew" in kinds
        skew = next(
            f for f in data["bundle_findings"] if f["kind"] == "bundle_soname_skew"
        )
        # The lagging member must be attributed.
        assert any("libonedal_thread" in lib for lib in skew["affected_libraries"])

    def test_compare_release_lockstep_bump_has_no_skew(
        self,
        tmp_path: Path,
    ) -> None:
        # Negative control: when the whole cohort bumps in lockstep there is
        # no skew finding (the detector must not fire on a clean release).
        import json as _json

        from click.testing import CliRunner

        from abicheck.cli import main

        case_dir = example_catalog.case_dir("case84_bundle_soname_skew")
        old = tmp_path / "v1"
        new = tmp_path / "v2"
        old.mkdir()
        new.mkdir()
        self._build_versioned_so(old, case_dir / "onedal_core.c", "libonedal_core.so.1")
        self._build_versioned_so(
            old, case_dir / "onedal_thread.c", "libonedal_thread.so.1"
        )
        # v2: BOTH bump to .so.2 — lockstep, no skew.
        self._build_versioned_so(new, case_dir / "onedal_core.c", "libonedal_core.so.2")
        self._build_versioned_so(
            new, case_dir / "onedal_thread.c", "libonedal_thread.so.2"
        )

        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old),
                str(new),
                "--format",
                "json",
                "--bundle-cohort",
                "libonedal_",
            ],
        )
        data = _json.loads(result.stdout)
        kinds = {f["kind"] for f in data.get("bundle_findings", [])}
        assert "bundle_soname_skew" not in kinds

    def test_compare_release_skew_is_opt_in(self, tmp_path: Path) -> None:
        # Without --bundle-cohort the skew check never runs: the case84 skew
        # layout must produce NO bundle_soname_skew finding (opt-in default).
        import json as _json

        from click.testing import CliRunner

        from abicheck.cli import main

        case_dir = example_catalog.case_dir("case84_bundle_soname_skew")
        old = tmp_path / "v1"
        new = tmp_path / "v2"
        old.mkdir()
        new.mkdir()
        self._build_versioned_so(old, case_dir / "onedal_core.c", "libonedal_core.so.1")
        self._build_versioned_so(
            old, case_dir / "onedal_thread.c", "libonedal_thread.so.1"
        )
        self._build_versioned_so(new, case_dir / "onedal_core.c", "libonedal_core.so.2")
        self._build_versioned_so(
            new, case_dir / "onedal_thread.c", "libonedal_thread.so.1"
        )

        result = CliRunner().invoke(
            main,
            ["compare", str(old), str(new), "--format", "json"],
        )
        data = _json.loads(result.stdout)
        kinds = {f["kind"] for f in data.get("bundle_findings", [])}
        assert "bundle_soname_skew" not in kinds


# ---------------------------------------------------------------------------
# Cohort-scoped SONAME skew logic (pure, no compiler / no disk)
# ---------------------------------------------------------------------------


class TestSonameSkewCohortScoping:
    """Unit tests for the opt-in `_soname_skew_findings` / `_detect_soname_skew`.

    Skew is only evaluated within explicitly declared cohorts (prefixes). With
    no declared cohort nothing is emitted — independent libraries are never
    inferred to be co-versioned from their filenames.
    """

    @staticmethod
    def _member(library: str, major: int):
        from abicheck.diff_cpp_patterns import BundleMember

        return BundleMember(library=library, soname=library, soname_major=major)

    def test_no_cohort_declared_emits_nothing(self) -> None:
        # The opt-in default: even a real skew layout produces no finding when
        # no cohort prefix is declared.
        from abicheck.bundle import _soname_skew_findings

        old = [
            self._member("libonedal_core.so.1", 1),
            self._member("libonedal_thread.so.1", 1),
        ]
        new = [
            self._member("libonedal_core.so.2", 2),
            self._member("libonedal_thread.so.1", 1),  # laggard
        ]
        assert _soname_skew_findings(old, new, []) == []

    def test_skew_within_declared_cohort_is_flagged(self) -> None:
        from abicheck.bundle import _soname_skew_findings

        old = [
            self._member("libonedal_core.so.1", 1),
            self._member("libonedal_thread.so.1", 1),
            self._member("libonedal_dpc.so.1", 1),
        ]
        new = [
            self._member("libonedal_core.so.2", 2),
            self._member("libonedal_thread.so.1", 1),  # laggard
            self._member("libonedal_dpc.so.2", 2),
        ]
        findings = _soname_skew_findings(old, new, ["libonedal_"])
        assert len(findings) == 1
        assert findings[0].kind == ChangeKind.BUNDLE_SONAME_SKEW
        assert any("libonedal_thread" in lib for lib in findings[0].affected_libraries)

    def test_independent_libraries_outside_cohort_are_not_flagged(self) -> None:
        # The reviewer's case: libfoo_core bumps while libfoo_plugin stays.
        # Declaring only the libfoo_core cohort must not drag libfoo_plugin in,
        # and declaring nothing emits nothing.
        from abicheck.bundle import _soname_skew_findings

        old = [
            self._member("libfoo_core.so.1", 1),
            self._member("libfoo_plugin.so.1", 1),
        ]
        new = [
            self._member("libfoo_core.so.2", 2),
            self._member("libfoo_plugin.so.1", 1),  # independent, unchanged
        ]
        assert _soname_skew_findings(old, new, []) == []
        # A cohort that matches only the (single) bumped library: no skew,
        # because there is no lagging sibling inside that declared cohort.
        assert _soname_skew_findings(old, new, ["libfoo_core"]) == []

    def test_lockstep_bump_within_cohort_is_clean(self) -> None:
        from abicheck.bundle import _soname_skew_findings

        old = [
            self._member("libonedal_core.so.1", 1),
            self._member("libonedal_thread.so.1", 1),
        ]
        new = [
            self._member("libonedal_core.so.2", 2),
            self._member("libonedal_thread.so.2", 2),
        ]
        assert _soname_skew_findings(old, new, ["libonedal_"]) == []

    def test_blank_cohort_prefix_is_rejected(self) -> None:
        # An empty/whitespace prefix (e.g. --bundle-cohort "" from an unset
        # var) must NOT degrade into "compare every DSO": independent libfoo
        # bumping while libbar stays must stay clean.
        from abicheck.bundle import _soname_skew_findings

        old = [self._member("libfoo.so.1", 1), self._member("libbar.so.1", 1)]
        new = [self._member("libfoo.so.2", 2), self._member("libbar.so.1", 1)]
        assert _soname_skew_findings(old, new, [""]) == []
        assert _soname_skew_findings(old, new, ["  "]) == []
        # A blank mixed with a real cohort still honours the real one only.
        assert _soname_skew_findings(old, new, ["", "libqux_"]) == []

    def test_detect_skew_requires_cohort_and_uses_snapshot_libraries(self) -> None:
        # P2 regression: members come from snapshot.libraries/.metadata (so a
        # cohort split across directories is still caught), and the check is
        # opt-in (no cohort → nothing).
        from abicheck.bundle import _detect_soname_skew

        def _snap(core_soname: str, thread_soname: str) -> BundleSnapshot:
            libs = {
                "libonedal_core.so": Path("/rel/lib64") / core_soname,
                "libonedal_thread.so": Path("/rel/lib32") / thread_soname,
            }
            meta = {
                "libonedal_core.so": _meta(soname=core_soname, exports=["c"]),
                "libonedal_thread.so": _meta(soname=thread_soname, exports=["t"]),
            }
            return BundleSnapshot(
                root=Path("/rel/lib64"),  # only one dir; the other must still count
                libraries=libs,
                metadata=meta,
                resolution=_compute_resolution_graph(libs, meta),
            )

        old = _snap("libonedal_core.so.1", "libonedal_thread.so.1")
        new = _snap("libonedal_core.so.2", "libonedal_thread.so.1")  # thread lags
        assert _detect_soname_skew(old, new, None) == []
        findings = _detect_soname_skew(old, new, ["libonedal_"])
        assert [f.kind for f in findings] == [ChangeKind.BUNDLE_SONAME_SKEW]

    def test_vendor_hashed_soname_and_filename_still_pairs_and_flags_skew(
        self,
    ) -> None:
        # G9 remaining half: auditwheel/delocate rewrite BOTH the filename and
        # the embedded DT_SONAME with a content-derived hash that changes on
        # every rebuild — e.g. libonedal_core-a1b2c3d4.so.1 ->
        # libonedal_core-e5f6a7b8.so.2. Cohort matching (keyed on the
        # strip_vendor_hash-normalized filename) already tolerated the
        # filename half; this proves the SONAME half is normalized too
        # (BundleMember.soname carries the canonical, hash-free SONAME) and
        # a real lagging-sibling skew still surfaces despite every hash in
        # sight changing between old and new.
        from abicheck.bundle import _detect_soname_skew

        def _snap(core: str, thread: str) -> BundleSnapshot:
            libs = {
                "libonedal_core.so": Path("/rel/lib64") / core,
                "libonedal_thread.so": Path("/rel/lib64") / thread,
            }
            meta = {
                "libonedal_core.so": _meta(soname=core, exports=["c"]),
                "libonedal_thread.so": _meta(soname=thread, exports=["t"]),
            }
            return BundleSnapshot(
                root=Path("/rel/lib64"),
                libraries=libs,
                metadata=meta,
                resolution=_compute_resolution_graph(libs, meta),
            )

        old = _snap("libonedal_core-a1b2c3d4.so.1", "libonedal_thread-1a1b2c2d.so.1")
        new = _snap(
            # core bumps major AND gets a fresh hash; thread lags but also
            # gets a fresh hash (a hash-only rebuild, not a real SONAME change).
            "libonedal_core-e5f6a7b8.so.2",
            "libonedal_thread-9e9f8a8b.so.1",
        )
        findings = _detect_soname_skew(old, new, ["libonedal_"])
        assert [f.kind for f in findings] == [ChangeKind.BUNDLE_SONAME_SKEW]
        assert "libonedal_thread" in findings[0].affected_libraries[0]

    def test_hashed_versioned_dylib_major_extracted_after_stripping_hash(
        self,
    ) -> None:
        # Codex review: a hashed *versioned* dylib install name has the
        # content hash BETWEEN the major and the extension —
        # libonedal_core.2-a1b2c3.dylib — unlike the .so case above where the
        # hash sits before the trailing .so.N and never interferes with
        # _extract_soname_major's end-anchored regex. Extracting the major
        # from the raw (unstripped) string fails here, dropping the member
        # from the cohort entirely instead of pairing and flagging skew.
        from abicheck.bundle import _detect_soname_skew

        def _snap(core: str, thread: str) -> BundleSnapshot:
            libs = {
                "libonedal_core.dylib": Path("/rel/lib") / core,
                "libonedal_thread.dylib": Path("/rel/lib") / thread,
            }
            meta = {
                "libonedal_core.dylib": _meta(soname=core, exports=["c"]),
                "libonedal_thread.dylib": _meta(soname=thread, exports=["t"]),
            }
            return BundleSnapshot(
                root=Path("/rel/lib"),
                libraries=libs,
                metadata=meta,
                resolution=_compute_resolution_graph(libs, meta),
            )

        old = _snap("libonedal_core.1-a1b2c3.dylib", "libonedal_thread.1-1a1b2c.dylib")
        new = _snap(
            # core bumps major AND gets a fresh hash; thread lags but also
            # gets a fresh hash (a hash-only rebuild, not a real SONAME change).
            "libonedal_core.2-e5f6a7.dylib",
            "libonedal_thread.1-9e9f8a.dylib",
        )
        findings = _detect_soname_skew(old, new, ["libonedal_"])
        assert [f.kind for f in findings] == [ChangeKind.BUNDLE_SONAME_SKEW]
        assert "libonedal_thread" in findings[0].affected_libraries[0]
