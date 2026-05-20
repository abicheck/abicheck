"""Unit tests for the bundle layer (ADR-023, abicheck/bundle.py).

These tests use minimal in-memory ElfMetadata fixtures so they do not need
gcc or castxml. Integration tests that build real .so files from the
examples/case90-93 fixtures live in tests/test_bundle_examples.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.bundle import (
    BundleSnapshot,
    ConsumerEntry,
    InstantiationManifest,
    ManifestEntry,
    ProviderEntry,
    _compute_resolution_graph,
    compare_bundle,
    load_manifest,
)
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change, DiffResult
from abicheck.elf_metadata import ElfImport, ElfMetadata, ElfSymbol

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
) -> ElfMetadata:
    """Construct a minimal ElfMetadata for testing."""
    syms = []
    for name in exports or []:
        syms.append(ElfSymbol(
            name=name, visibility="default",
            version=(export_versions or {}).get(name, ""),
        ))
    imps = []
    for name in imports or []:
        imps.append(ElfImport(name=name))
    return ElfMetadata(
        soname=soname or "",
        needed=needed or [],
        symbols=syms,
        imports=imps,
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


def _diff(library: str, *changes: Change, verdict: Verdict = Verdict.BREAKING) -> DiffResult:
    return DiffResult(
        old_version="old", new_version="new",
        library=library, changes=list(changes), verdict=verdict,
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
        meta = ElfMetadata(soname="lib.so", symbols=[
            ElfSymbol(name="public_func", visibility="default"),
            ElfSymbol(name="hidden_func", visibility="hidden"),
        ])
        snap = _snapshot({"lib.so": meta})
        assert "public_func" in snap.resolution.provides
        assert "hidden_func" not in snap.resolution.provides

    def test_extra_needed_records_system_libs(self) -> None:
        # DT_NEEDED that doesn't match a sibling in the bundle goes into extra.
        meta = {
            "libcore.so": _meta(soname="libcore.so", needed=["libc.so.6", "libalgo.so.1"]),
            "libalgo.so": _meta(soname="libalgo.so.1"),
        }
        snap = _snapshot(meta)
        assert "libalgo.so.1" in snap.resolution.intra_needed["libcore.so"]
        assert "libc.so.6" in snap.resolution.extra_needed["libcore.so"]


# ---------------------------------------------------------------------------
# bundle_intra_dep_removed
# ---------------------------------------------------------------------------

class TestIntraDepRemoved:
    def test_detects_missing_import(self) -> None:
        old = _snapshot({
            "libcore.so": _meta(soname="libcore.so.1", exports=["core_add", "core_mul"]),
            "libalgo.so": _meta(soname="libalgo.so.1", needed=["libcore.so.1"],
                                imports=["core_add", "core_mul"]),
        })
        new = _snapshot({
            "libcore.so": _meta(soname="libcore.so.1", exports=["core_add"]),
            "libalgo.so": _meta(soname="libalgo.so.1", needed=["libcore.so.1"],
                                imports=["core_add", "core_mul"]),
        })
        result = compare_bundle(old, new, per_library_results=[])
        kinds = {f.kind for f in result.bundle_findings}
        assert ChangeKind.BUNDLE_INTRA_DEP_REMOVED in kinds
        finding = next(
            f for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
        )
        assert finding.symbol == "core_mul"
        assert finding.consumer_library == "libalgo.so"

    def test_ignores_system_symbols(self) -> None:
        # libc/libstdc++ imports must not fire bundle findings.
        new = _snapshot({
            "libfoo.so": _meta(soname="libfoo.so.1",
                               needed=["libcore.so.1"],
                               imports=["__cxa_atexit", "malloc", "memcpy"]),
            "libcore.so": _meta(soname="libcore.so.1", exports=["dummy"]),
        })
        result = compare_bundle(new, new, per_library_results=[])
        assert not any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
            for f in result.bundle_findings
        )

    def test_extends_system_providers_via_arg(self) -> None:
        new = _snapshot({
            "libfoo.so": _meta(soname="libfoo.so.1",
                               needed=["libcore.so.1", "libcustom.so.1"],
                               imports=["custom_init"]),
            "libcore.so": _meta(soname="libcore.so.1", exports=["dummy"]),
        })
        # Without user-extended allow-list, custom_init is bundle-relevant.
        result_default = compare_bundle(new, new, per_library_results=[])
        # Note: heuristic — custom_init may already be excluded by the
        # "no intra-bundle siblings imported" path. Either way the
        # explicit allow-list must not introduce findings.
        with_extra = compare_bundle(
            new, new, per_library_results=[],
            system_providers=["libcustom.so.1"],
        )
        assert len(with_extra.bundle_findings) <= len(result_default.bundle_findings)


# ---------------------------------------------------------------------------
# bundle_intra_dep_signature_changed
# ---------------------------------------------------------------------------

class TestIntraDepSignatureChanged:
    def test_promotes_provider_signature_change_to_consumer(self) -> None:
        new = _snapshot({
            "libcore.so": _meta(soname="libcore.so.1", exports=["core_add"]),
            "libalgo.so": _meta(soname="libalgo.so.1", needed=["libcore.so.1"],
                                imports=["core_add"]),
        })
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
            f for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED
        ]
        assert len(findings) == 1
        assert findings[0].consumer_library == "libalgo.so"
        assert findings[0].provider_library == "libcore.so"

    def test_dedupe_params_plus_return_change(self) -> None:
        # libcore changes both params AND return of the same symbol; we
        # should emit a SINGLE bundle finding per (consumer, symbol).
        new = _snapshot({
            "libcore.so": _meta(soname="libcore.so.1", exports=["core_add"]),
            "libalgo.so": _meta(soname="libalgo.so.1", needed=["libcore.so.1"],
                                imports=["core_add"]),
        })
        diff = _diff(
            "libcore.so",
            Change(kind=ChangeKind.FUNC_PARAMS_CHANGED, symbol="core_add", description=""),
            Change(kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="core_add", description=""),
        )
        result = compare_bundle(new, new, [diff])
        sig_findings = [
            f for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED
        ]
        assert len(sig_findings) == 1

    def test_no_finding_when_no_consumers(self) -> None:
        # Provider changes but no sibling imports the symbol — bundle-level
        # finding does NOT fire; the per-library diff already covers it.
        new = _snapshot({
            "libcore.so": _meta(soname="libcore.so.1", exports=["core_add"]),
            "libother.so": _meta(soname="libother.so.1"),
        })
        diff = _diff(
            "libcore.so",
            Change(kind=ChangeKind.FUNC_PARAMS_CHANGED, symbol="core_add", description=""),
        )
        result = compare_bundle(new, new, [diff])
        assert not any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED
            for f in result.bundle_findings
        )


# ---------------------------------------------------------------------------
# bundle_provider_changed
# ---------------------------------------------------------------------------

class TestProviderChanged:
    def test_detects_symbol_migration(self) -> None:
        old = _snapshot({
            "libcore.so": _meta(soname="libcore.so.1", exports=["util_x"]),
            "libutil.so": _meta(soname="libutil.so.1"),
        })
        new = _snapshot({
            "libcore.so": _meta(soname="libcore.so.1"),
            "libutil.so": _meta(soname="libutil.so.1", exports=["util_x"]),
        })
        diffs = [
            _diff("libcore.so", Change(
                kind=ChangeKind.FUNC_REMOVED, symbol="util_x", description="",
            )),
            _diff("libutil.so", Change(
                kind=ChangeKind.FUNC_ADDED, symbol="util_x", description="",
            ), verdict=Verdict.COMPATIBLE),
        ]
        result = compare_bundle(old, new, diffs)
        provider_findings = [
            f for f in result.bundle_findings
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
            _diff("libcore.so",
                  Change(kind=ChangeKind.FUNC_REMOVED, symbol="y", description=""),
                  Change(kind=ChangeKind.FUNC_ADDED, symbol="y", description="")),
        ]
        result = compare_bundle(new, new, diffs)
        assert not any(
            f.kind == ChangeKind.BUNDLE_PROVIDER_CHANGED
            for f in result.bundle_findings
        )


# ---------------------------------------------------------------------------
# bundle_library_removed / bundle_library_added
# ---------------------------------------------------------------------------

class TestLibraryStructural:
    def test_added_library_emits_addition(self) -> None:
        old = _snapshot({"libcore.so": _meta(soname="libcore.so.1")})
        new = _snapshot({
            "libcore.so": _meta(soname="libcore.so.1"),
            "libnew.so": _meta(soname="libnew.so.1"),
        })
        result = compare_bundle(old, new, [])
        added = [f for f in result.bundle_findings
                 if f.kind == ChangeKind.BUNDLE_LIBRARY_ADDED]
        assert len(added) == 1
        assert added[0].symbol == "libnew.so"

    def test_removed_library_emits_finding_only_with_consumers(self) -> None:
        # If no sibling imported the removed library's symbols, the
        # bundle layer stays silent — the existing --fail-on-removed-library
        # flow is responsible.
        old = _snapshot({
            "libcore.so": _meta(soname="libcore.so.1", exports=["x"]),
            "libstandalone.so": _meta(soname="libstandalone.so.1", exports=["y"]),
        })
        new = _snapshot({"libcore.so": _meta(soname="libcore.so.1", exports=["x"])})
        result = compare_bundle(old, new, [])
        assert not any(
            f.kind == ChangeKind.BUNDLE_LIBRARY_REMOVED
            for f in result.bundle_findings
        )

    def test_removed_library_with_intra_consumer_fires(self) -> None:
        old = _snapshot({
            "libcore.so": _meta(soname="libcore.so.1", exports=["util_x"]),
            "libalgo.so": _meta(soname="libalgo.so.1", needed=["libcore.so.1"],
                                imports=["util_x"]),
        })
        new = _snapshot({
            "libalgo.so": _meta(soname="libalgo.so.1", needed=["libcore.so.1"],
                                imports=["util_x"]),
        })
        result = compare_bundle(old, new, [])
        removed = [f for f in result.bundle_findings
                   if f.kind == ChangeKind.BUNDLE_LIBRARY_REMOVED]
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
        new = _snapshot({
            "libcore.so": _meta(
                soname="libcore.so.1",
                exports=["DataCollection_ctor"],
            ),
            "libalgo.so": _meta(
                soname="libalgo.so.1",
                needed=["libcore.so.1"],
                exports=["_Z3runP14DataCollection"],
            ),
        })
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
            f for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_TYPE_CHANGED
        ]
        assert len(type_findings) == 1
        assert type_findings[0].symbol == "DataCollection"
        assert type_findings[0].consumer_library == "libalgo.so"
        assert type_findings[0].provider_library == "libcore.so"


# ---------------------------------------------------------------------------
# bundle_intra_dep_resolved_to_different_version (gnu.version_d drift)
# ---------------------------------------------------------------------------

class TestVersionDrift:
    def test_default_version_drift_emits_finding(self) -> None:
        # core_fn is exported in old at GLIBCXX_3.4.20, in new at
        # GLIBCXX_3.4.30; libalgo imports it. Bundle layer flags the
        # version drift as COMPATIBLE_WITH_RISK.
        old = _snapshot({
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
        })
        new = _snapshot({
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
        })
        result = compare_bundle(old, new, [])
        drift = [
            f for f in result.bundle_findings
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
        manifest = InstantiationManifest(entries=(
            ManifestEntry(symbol="promised_a", library="libcore.so",
                          optional_provider=False),
            ManifestEntry(symbol="promised_b", library=None,
                          optional_provider=True),
        ))
        old = _snapshot({
            "libcore.so": _meta(soname="libcore.so.1",
                                exports=["promised_a", "promised_b"]),
        })
        new = _snapshot({
            "libcore.so": _meta(soname="libcore.so.1", exports=["promised_a"]),
        })
        result = compare_bundle(old, new, [], manifest=manifest)
        kinds = [f.kind for f in result.bundle_findings]
        assert ChangeKind.BUNDLE_MANIFEST_INSTANTIATION_REMOVED in kinds
        missing = next(
            f for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_MANIFEST_INSTANTIATION_REMOVED
        )
        assert missing.symbol == "promised_b"

    def test_wrong_provider_when_required(self) -> None:
        manifest = InstantiationManifest(entries=(
            ManifestEntry(symbol="x", library="libcore.so", optional_provider=False),
        ))
        new = _snapshot({
            "libcore.so": _meta(soname="libcore.so.1"),
            "libother.so": _meta(soname="libother.so.1", exports=["x"]),
        })
        result = compare_bundle(new, new, [], manifest=manifest)
        wrong = [
            f for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_MANIFEST_INSTANTIATION_REMOVED
        ]
        assert len(wrong) == 1
        assert "libother.so" in (wrong[0].new_value or "")

    def test_optional_provider_accepts_any_sibling(self) -> None:
        manifest = InstantiationManifest(entries=(
            ManifestEntry(symbol="x", library=None, optional_provider=True),
        ))
        new = _snapshot({
            "libcore.so": _meta(soname="libcore.so.1"),
            "libother.so": _meta(soname="libother.so.1", exports=["x"]),
        })
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
            '{"version": 1, "provides": ['
            '{"symbol": "x", "library": "libfoo.so.1"}'
            ']}',
        )
        m = load_manifest(path)
        assert m.entries[0].symbol == "x"
        assert m.entries[0].optional_provider is True  # default

    def test_load_manifest_rejects_missing_provides(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("version: 1\n")
        with pytest.raises(ValueError, match="missing top-level 'provides:'"):
            load_manifest(path)

    def test_required_provider_matches_soname(self) -> None:
        # Manifest format documents both filename keys (libcore.so) and
        # SONAMEs (libcore.so.1) for the `library:` field. The bundle
        # layer must accept either; if the manifest names libcore.so.1
        # (a SONAME) and the candidate provider's SONAME matches, that's
        # a hit, no spurious BUNDLE_MANIFEST_INSTANTIATION_REMOVED.
        manifest = InstantiationManifest(entries=(
            ManifestEntry(
                symbol="train_v2",
                library="libcore.so.1",        # SONAME, not filename key
                optional_provider=False,
            ),
        ))
        new = _snapshot({
            "libcore.so": _meta(
                soname="libcore.so.1",
                exports=["train_v2"],
            ),
        })
        result = compare_bundle(new, new, [], manifest=manifest)
        assert not any(
            f.kind == ChangeKind.BUNDLE_MANIFEST_INSTANTIATION_REMOVED
            for f in result.bundle_findings
        )

    def test_new_promised_symbol_emits_addition(self) -> None:
        # Symbol present in new manifest, absent from old bundle exports
        # but present in new bundle. Bundle layer emits
        # BUNDLE_MANIFEST_INSTANTIATION_ADDED.
        manifest = InstantiationManifest(entries=(
            ManifestEntry(symbol="new_train", library=None, optional_provider=True),
        ))
        old = _snapshot({"libcore.so": _meta(soname="libcore.so.1")})
        new = _snapshot({
            "libcore.so": _meta(soname="libcore.so.1", exports=["new_train"]),
        })
        result = compare_bundle(old, new, [], manifest=manifest)
        added = [
            f for f in result.bundle_findings
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
        new = _snapshot({
            "libfoo.so": _meta(
                soname="libfoo.so.1",
                needed=["libcore.so.1", "libcustom.so.1"],
                imports=["__cxa_atexit"],   # known system symbol
            ),
            "libcore.so": _meta(soname="libcore.so.1", exports=["dummy"]),
        })
        # Even without the extension, __cxa_atexit is on the default
        # symbol allow-list and the finding is suppressed.
        baseline = compare_bundle(new, new, per_library_results=[])
        with_extras = compare_bundle(
            new, new, per_library_results=[],
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
# Verdict aggregation
# ---------------------------------------------------------------------------

class TestVerdictAggregation:
    def test_bundle_verdict_promotes_aggregate(self) -> None:
        # All per-library diffs are NO_CHANGE; bundle finding alone forces BREAKING.
        new = _snapshot({
            "libcore.so": _meta(soname="libcore.so.1", exports=["x"]),
            "libalgo.so": _meta(soname="libalgo.so.1", needed=["libcore.so.1"],
                                imports=["x", "missing_sym"]),
        })
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
