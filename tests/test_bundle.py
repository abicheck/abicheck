"""Unit tests for the bundle layer (ADR-023, abicheck/bundle.py).

These tests use minimal in-memory ElfMetadata fixtures so they do not need
gcc or castxml. Integration tests that build real .so files from the
examples/case90-93 fixtures live in tests/test_bundle_examples.py.
"""
from __future__ import annotations

import sys
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

    def test_fires_when_dt_needed_was_stripped(self) -> None:
        # Regression for the CodeRabbit feedback: previously the bundle
        # layer short-circuited when consumer.intra_needed was empty,
        # which hid the case where a build refactor removed BOTH the
        # only sibling provider *and* the DT_NEEDED edge that pointed at
        # it. The unresolved import remains in .dynsym; the bundle layer
        # must still flag it (the system-symbol allow-list separately
        # filters out genuinely-external imports).
        new = _snapshot({
            "libcore.so": _meta(soname="libcore.so.1"),  # provider gone
            "libalgo.so": _meta(
                soname="libalgo.so.1",
                needed=[],                        # DT_NEEDED stripped too
                imports=["onedal_internal_op"],   # not a system symbol
            ),
        })
        result = compare_bundle(new, new, per_library_results=[])
        intra_removed = [
            f for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
        ]
        assert len(intra_removed) == 1
        assert intra_removed[0].symbol == "onedal_internal_op"
        assert intra_removed[0].consumer_library == "libalgo.so"


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

    def test_dedupe_multiple_low_level_changes_same_type(self) -> None:
        # A single type can produce several low-level diffs (size +
        # alignment + field_removed); the bundle layer must collapse
        # those into one cross-DSO finding per (consumer, provider, type).
        new = _snapshot({
            "libcore.so": _meta(
                soname="libcore.so.1", exports=["DataCollection_ctor"],
            ),
            "libalgo.so": _meta(
                soname="libalgo.so.1", needed=["libcore.so.1"],
                exports=["_Z3runP14DataCollection"],
            ),
        })
        diff = _diff(
            "libcore.so",
            Change(kind=ChangeKind.TYPE_SIZE_CHANGED,
                   symbol="DataCollection", description=""),
            Change(kind=ChangeKind.TYPE_ALIGNMENT_CHANGED,
                   symbol="DataCollection", description=""),
            Change(kind=ChangeKind.TYPE_FIELD_REMOVED,
                   symbol="DataCollection", description=""),
        )
        result = compare_bundle(new, new, [diff])
        type_findings = [
            f for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_TYPE_CHANGED
        ]
        assert len(type_findings) == 1


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
            ']}',
        )
        m = load_manifest(path)
        assert len(m.entries) == 1
        assert m.entries[0].symbol == "ok_func"

    def test_load_manifest_rejects_string_optional_provider(self, tmp_path: Path) -> None:
        # YAML quote-ifies bool-looking strings; users hand-editing
        # `optional_provider: "false"` (string) would silently get
        # parsed as truthy by bool() — validate strictly instead.
        path = tmp_path / "stringy.json"
        path.write_text(
            '{"version": 1, "provides": ['
            '{"symbol": "x", "library": "lib.so.1", "optional_provider": "false"}'
            ']}',
        )
        with pytest.raises(ValueError, match="optional_provider.*must be a boolean"):
            load_manifest(path)

    def test_load_manifest_rejects_int_optional_provider(self, tmp_path: Path) -> None:
        path = tmp_path / "inty.json"
        path.write_text(
            '{"version": 1, "provides": ['
            '{"symbol": "x", "optional_provider": 1}'
            ']}',
        )
        with pytest.raises(ValueError, match="optional_provider.*must be a boolean"):
            load_manifest(path)

    def test_load_manifest_pattern_form(self, tmp_path: Path) -> None:
        path = tmp_path / "patterns.yaml"
        path.write_text(
            "version: 1\n"
            "provides:\n"
            "  - pattern: \"oneapi::dal::train_ops<*>*\"\n"
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
            "      - {Float: float,  Method: \"method::dense\", Task: \"task::train\"}\n"
            "      - {Float: double, Method: \"method::dense\", Task: \"task::train\"}\n",
        )
        m = load_manifest(path)
        assert m.entries[0].template == "oneapi::dal::train_ops"
        assert len(m.entries[0].instantiations) == 2
        assert m.entries[0].instantiations[0]["Float"] == "float"
        assert m.entries[0].kind() == "template"

    def test_load_manifest_rejects_multiple_shape_keys(self, tmp_path: Path) -> None:
        path = tmp_path / "mixed.yaml"
        path.write_text(
            "version: 1\n"
            "provides:\n"
            "  - symbol: foo\n"
            "    pattern: \"foo*\"\n",
        )
        with pytest.raises(ValueError, match="conflicting fields"):
            load_manifest(path)

    def test_load_manifest_rejects_missing_shape_key(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.yaml"
        path.write_text(
            "version: 1\n"
            "provides:\n"
            "  - library: libfoo.so.1\n",
        )
        with pytest.raises(ValueError, match="must have one of 'symbol'"):
            load_manifest(path)

    def test_load_manifest_template_needs_instantiations(self, tmp_path: Path) -> None:
        path = tmp_path / "no-insts.yaml"
        path.write_text(
            "version: 1\n"
            "provides:\n"
            "  - template: oneapi::dal::train_ops\n",
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
        new = _snapshot({
            "libcore.so": _meta(
                soname="libcore.so.1",
                exports=["onedal_train_float_dense", "onedal_train_float_sparse",
                         "onedal_predict_float_dense"],
            ),
        })
        manifest = InstantiationManifest(entries=(
            ManifestEntry(pattern="onedal_train_*", optional_provider=True),
        ))
        result = compare_bundle(new, new, [], manifest=manifest)
        assert not any(
            f.kind == ChangeKind.BUNDLE_MANIFEST_INSTANTIATION_REMOVED
            for f in result.bundle_findings
        )

    def test_pattern_with_no_match_emits_removed(self) -> None:
        new = _snapshot({"libcore.so": _meta(soname="libcore.so.1")})
        manifest = InstantiationManifest(entries=(
            ManifestEntry(pattern="onedal_train_*", optional_provider=True),
        ))
        result = compare_bundle(new, new, [], manifest=manifest)
        removed = [
            f for f in result.bundle_findings
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
        new = _snapshot({
            "libcore.so": _meta(
                soname="libcore.so.1",
                exports=[
                    "ns::T<float, dense>_ctor",   # carries the expanded form
                    "ns::T<double, sparse>_ctor",
                ],
            ),
        })
        manifest = InstantiationManifest(entries=(
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
        ))
        result = compare_bundle(new, new, [], manifest=manifest)
        removed = [
            f for f in result.bundle_findings
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
        new = _snapshot({
            "libcore.so": _meta(
                soname="libcore.so.1",
                exports=[
                    "ns::T<float, dense>_ctor",
                    "ns::T<double, dense>_ctor",
                    # <float, sparse> and <double, sparse> NOT exported
                ],
            ),
        })
        manifest = InstantiationManifest(entries=(
            ManifestEntry(
                template="ns::T",
                instantiations=(
                    {"P1": "float", "P2": "dense"},   # exported
                    {"P1": "float", "P2": "sparse"},  # MISSING
                    {"P1": "double", "P2": "dense"},  # exported
                    {"P1": "double", "P2": "sparse"}, # MISSING
                ),
                optional_provider=True,
            ),
        ))
        result = compare_bundle(new, new, [], manifest=manifest)
        removed = [
            f for f in result.bundle_findings
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
        self, monkeypatch: pytest.MonkeyPatch,
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

        new = _snapshot({
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
        })
        n_symbols = sum(len(m.symbols) for m in new.metadata.values())

        # Manifest with many targets — naïve scaling would multiply.
        manifest = InstantiationManifest(entries=(
            ManifestEntry(
                template="ns::T",
                instantiations=tuple(
                    {"P1": p1, "P2": p2}
                    for p1 in ("float", "double", "int", "long")
                    for p2 in ("dense", "sparse", "csr", "csc")
                ),  # 16 targets
                optional_provider=True,
            ),
        ))
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
        snap = _snapshot({
            "libb.so": _meta(soname="libb.so.1"),
            "liba.so": _meta(soname="liba.so.1"),
        })
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
        [gcc, "-shared", "-fPIC", "-g", "-O0", str(src_path),
         "-o", str(out), f"-Wl,-soname,{soname}"],
        capture_output=True, text=True,
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
class TestCompareReleaseBundleE2E:
    """Exercise compare-release end-to-end with the bundle layer enabled.

    These tests compile tiny C .so files at runtime so the CLI's bundle
    wiring (in abicheck/cli.py) is actually covered by tests — the
    in-memory unit tests above bypass the CLI surface and the ELF
    parsing path.
    """

    def test_compare_release_emits_bundle_findings(self, tmp_path: Path) -> None:
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
            old, "libcore.so",
            "int core_add(int a, int b){return a+b;}\n"
            "int core_mul(int a, int b){return a*b;}\n",
        )
        _build_tiny_so(
            new, "libcore.so",
            "int core_add(int a, int b){return a+b;}\n",   # core_mul removed
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
                [gcc, "-shared", "-fPIC", "-g", "-O0", str(src_file),
                 "-o", str(side / "libalgo.so"),
                 "-L", str(side), "-Wl,--no-as-needed", "-lcore",
                 "-Wl,-soname,libalgo.so.1"],
                check=True, capture_output=True,
            )

        result = CliRunner().invoke(
            main,
            ["compare-release", str(old), str(new), "--format", "json"],
        )
        # Bundle BREAKING → exit 4.
        assert result.exit_code == 4, result.output
        data = _json.loads(result.stdout)
        assert data["bundle_verdict"] == "BREAKING"
        kinds = {f["kind"] for f in data["bundle_findings"]}
        assert "bundle_intra_dep_removed" in kinds
        # The consumer attribution must point at libalgo.so.
        intra = next(
            f for f in data["bundle_findings"]
            if f["kind"] == "bundle_intra_dep_removed"
        )
        assert intra["consumer_library"] == "libalgo.so"
        assert intra["symbol"] == "core_mul"

    def test_compare_release_no_bundle_analysis_opts_out(self, tmp_path: Path) -> None:
        # Same broken bundle as above; --no-bundle-analysis must
        # suppress bundle findings and report only per-library results.
        import json as _json

        from click.testing import CliRunner

        from abicheck.cli import main

        old = tmp_path / "old"
        new = tmp_path / "new"
        old.mkdir()
        new.mkdir()
        _build_tiny_so(old, "libfoo.so", "int foo(void){return 1;}\nint bar(void){return 2;}\n")
        _build_tiny_so(new, "libfoo.so", "int foo(void){return 1;}\n")

        result = CliRunner().invoke(
            main,
            ["compare-release", str(old), str(new),
             "--no-bundle-analysis", "--format", "json"],
        )
        data = _json.loads(result.stdout)
        # bundle_verdict / bundle_findings must NOT be present.
        assert "bundle_verdict" not in data
        assert "bundle_findings" not in data

    def test_compare_release_with_manifest_emits_manifest_finding(
        self, tmp_path: Path,
    ) -> None:
        # Manifest lists `bar` as a promise; new bundle drops it.
        import json as _json

        from click.testing import CliRunner

        from abicheck.cli import main

        old = tmp_path / "old"
        new = tmp_path / "new"
        old.mkdir()
        new.mkdir()
        _build_tiny_so(old, "libfoo.so", "int foo(void){return 1;}\nint bar(void){return 2;}\n")
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
            ["compare-release", str(old), str(new),
             "--manifest", str(manifest), "--format", "json"],
        )
        data = _json.loads(result.stdout)
        kinds = {f["kind"] for f in data["bundle_findings"]}
        assert "bundle_manifest_instantiation_removed" in kinds

    def test_compare_release_markdown_shows_bundle_section(
        self, tmp_path: Path,
    ) -> None:
        # Bundle finding must show up in the markdown summary output.
        from click.testing import CliRunner

        from abicheck.cli import main

        old = tmp_path / "old"
        new = tmp_path / "new"
        old.mkdir()
        new.mkdir()
        _build_tiny_so(
            old, "libcore.so",
            "int core_add(int a, int b){return a+b;}\n"
            "int core_mul(int a, int b){return a*b;}\n",
        )
        _build_tiny_so(
            new, "libcore.so",
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
                [gcc, "-shared", "-fPIC", "-g", "-O0", str(src_file),
                 "-o", str(side / "libalgo.so"),
                 "-L", str(side), "-Wl,--no-as-needed", "-lcore",
                 "-Wl,-soname,libalgo.so.1"],
                check=True, capture_output=True,
            )

        result = CliRunner().invoke(
            main, ["compare-release", str(old), str(new)],
        )
        assert "Bundle (Cross-Library) Findings" in result.stdout
        assert "bundle_intra_dep_removed" in result.stdout

    def _build_versioned_so(
        self, release_dir: Path, src: Path, soname: str,
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
            [gcc, "-shared", "-fPIC", "-g", "-O0", str(src),
             "-o", str(out), f"-Wl,-soname,{soname}"],
            capture_output=True, text=True,
        )
        if res.returncode != 0:
            pytest.fail(f"gcc failed for {soname}: {res.stderr}")

    def test_compare_release_emits_soname_skew_for_case84(
        self, tmp_path: Path,
    ) -> None:
        # Reproduce examples/case84_bundle_soname_skew end-to-end: core+dpc
        # bump SONAME .so.1 -> .so.2 while thread (deliberately) lags at .so.1.
        # Each library passes its own per-library check; the cohort invariant
        # fails. compare-release must surface BUNDLE_SONAME_SKEW and BREAK.
        import json as _json

        from click.testing import CliRunner

        from abicheck.cli import main

        case_dir = (
            Path(__file__).parent.parent
            / "examples" / "case84_bundle_soname_skew"
        )
        old = tmp_path / "v1"
        new = tmp_path / "v2"
        old.mkdir()
        new.mkdir()
        # v1: all three at .so.1
        self._build_versioned_so(old, case_dir / "onedal_core.c", "libonedal_core.so.1")
        self._build_versioned_so(old, case_dir / "onedal_thread.c", "libonedal_thread.so.1")
        self._build_versioned_so(old, case_dir / "onedal_dpc.c", "libonedal_dpc.so.1")
        # v2: core + dpc bumped to .so.2, thread lags at .so.1
        self._build_versioned_so(new, case_dir / "onedal_core.c", "libonedal_core.so.2")
        self._build_versioned_so(new, case_dir / "onedal_thread.c", "libonedal_thread.so.1")
        self._build_versioned_so(new, case_dir / "onedal_dpc.c", "libonedal_dpc.so.2")

        result = CliRunner().invoke(
            main,
            ["compare-release", str(old), str(new), "--format", "json",
             "--bundle-cohort", "libonedal_"],
        )
        # Bundle BREAKING → exit 4 (matches ground_truth.json case84 == BREAKING).
        assert result.exit_code == 4, result.output
        data = _json.loads(result.stdout)
        assert data["bundle_verdict"] == "BREAKING"
        kinds = {f["kind"] for f in data["bundle_findings"]}
        assert "bundle_soname_skew" in kinds
        skew = next(
            f for f in data["bundle_findings"]
            if f["kind"] == "bundle_soname_skew"
        )
        # The lagging member must be attributed.
        assert any("libonedal_thread" in lib for lib in skew["affected_libraries"])

    def test_compare_release_lockstep_bump_has_no_skew(
        self, tmp_path: Path,
    ) -> None:
        # Negative control: when the whole cohort bumps in lockstep there is
        # no skew finding (the detector must not fire on a clean release).
        import json as _json

        from click.testing import CliRunner

        from abicheck.cli import main

        case_dir = (
            Path(__file__).parent.parent
            / "examples" / "case84_bundle_soname_skew"
        )
        old = tmp_path / "v1"
        new = tmp_path / "v2"
        old.mkdir()
        new.mkdir()
        self._build_versioned_so(old, case_dir / "onedal_core.c", "libonedal_core.so.1")
        self._build_versioned_so(old, case_dir / "onedal_thread.c", "libonedal_thread.so.1")
        # v2: BOTH bump to .so.2 — lockstep, no skew.
        self._build_versioned_so(new, case_dir / "onedal_core.c", "libonedal_core.so.2")
        self._build_versioned_so(new, case_dir / "onedal_thread.c", "libonedal_thread.so.2")

        result = CliRunner().invoke(
            main,
            ["compare-release", str(old), str(new), "--format", "json",
             "--bundle-cohort", "libonedal_"],
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

        case_dir = (
            Path(__file__).parent.parent
            / "examples" / "case84_bundle_soname_skew"
        )
        old = tmp_path / "v1"
        new = tmp_path / "v2"
        old.mkdir()
        new.mkdir()
        self._build_versioned_so(old, case_dir / "onedal_core.c", "libonedal_core.so.1")
        self._build_versioned_so(old, case_dir / "onedal_thread.c", "libonedal_thread.so.1")
        self._build_versioned_so(new, case_dir / "onedal_core.c", "libonedal_core.so.2")
        self._build_versioned_so(new, case_dir / "onedal_thread.c", "libonedal_thread.so.1")

        result = CliRunner().invoke(
            main, ["compare-release", str(old), str(new), "--format", "json"],
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
        from abicheck.diff_onedal import BundleMember
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
