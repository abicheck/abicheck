"""Unit tests for persisted bundle facts (G38 Phase 2, ADR-023 amendment).

Mirrors ``tests/test_bundle.py``'s in-memory ``ElfMetadata`` fixture style
so these tests need no gcc/castxml and no real compiled binaries.
"""

from __future__ import annotations

from pathlib import Path

from abicheck.bundle import _compute_resolution_graph, compare_bundle
from abicheck.bundle_facts import (
    BUNDLE_FACTS_SCHEMA_VERSION,
    BundleFacts,
    bundle_snapshot_from_facts,
    capture_bundle_facts,
    compare_bundle_from_facts,
)
from abicheck.bundle_manifest import InstantiationManifest, ManifestEntry
from abicheck.bundle_models import BundleSnapshot
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change, DiffResult
from abicheck.elf_metadata import ElfImport, ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot
from abicheck.serialization import (
    bundle_facts_from_dict,
    bundle_facts_to_dict,
    load_bundle_facts,
    save_bundle_facts,
)

# ---------------------------------------------------------------------------
# Fixtures (mirrors tests/test_bundle.py's own helpers)
# ---------------------------------------------------------------------------


def _meta(
    *,
    soname: str = "",
    needed: list[str] | None = None,
    exports: list[str] | None = None,
    imports: list[str] | None = None,
) -> ElfMetadata:
    syms = [ElfSymbol(name=name, visibility="default") for name in exports or []]
    imps = [ElfImport(name=name) for name in imports or []]
    return ElfMetadata(
        soname=soname or "", needed=needed or [], symbols=syms, imports=imps
    )


def _snapshot(libraries: dict[str, ElfMetadata]) -> BundleSnapshot:
    libs = {name: Path(f"/fake/{name}") for name in libraries}
    graph = _compute_resolution_graph(libs, libraries)
    return BundleSnapshot(
        root=Path("/fake"), libraries=libs, metadata=libraries, resolution=graph
    )


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


def _old_metadata() -> dict[str, ElfMetadata]:
    return {
        "libcore.so": _meta(soname="libcore.so", exports=["core_mul", "core_add"]),
        "libalgo.so": _meta(
            soname="libalgo.so",
            needed=["libcore.so"],
            imports=["core_mul"],
        ),
    }


def _per_library_snapshots(metadata: dict[str, ElfMetadata]) -> dict[str, AbiSnapshot]:
    return {
        name: AbiSnapshot(library=name, version="old", elf=meta)
        for name, meta in metadata.items()
    }


# ---------------------------------------------------------------------------
# compare_bundle_from_facts parity with live compare_bundle()
# ---------------------------------------------------------------------------


class TestCompareBundleFromFactsParity:
    def test_intra_dep_removed_matches_live_compare(self) -> None:
        """The plan's mandatory acceptance test: a stored-facts comparison
        produces byte-identical bundle findings to a live-directory one, for
        the same underlying reproduction (core_mul removed, libalgo still
        imports it)."""
        old_metadata = _old_metadata()
        old_live = _snapshot(old_metadata)
        new_metadata = {
            "libcore.so": _meta(
                soname="libcore.so", exports=["core_add"]
            ),  # core_mul gone
            "libalgo.so": _meta(
                soname="libalgo.so",
                needed=["libcore.so"],
                imports=["core_mul"],
            ),
        }
        new_snapshot = _snapshot(new_metadata)
        per_lib_results = [
            _diff(
                "libcore.so",
                Change(
                    kind=ChangeKind.FUNC_REMOVED,
                    symbol="core_mul",
                    description="removed",
                ),
            ),
            _diff("libalgo.so", verdict=Verdict.COMPATIBLE),
        ]

        live_result = compare_bundle(old_live, new_snapshot, per_lib_results)

        facts = capture_bundle_facts(_per_library_snapshots(old_metadata))
        facts_result = compare_bundle_from_facts(facts, new_snapshot, per_lib_results)

        assert facts_result.bundle_findings == live_result.bundle_findings
        assert facts_result.bundle_verdict == live_result.bundle_verdict
        assert live_result.bundle_verdict == Verdict.BREAKING

    def test_signature_changed_matches_live_compare(self) -> None:
        """Same parity invariant for a diff-derived finding
        (bundle_intra_dep_signature_changed), not just a graph-native one."""
        old_metadata = _old_metadata()
        old_live = _snapshot(old_metadata)
        new_metadata = {
            "libcore.so": _meta(soname="libcore.so", exports=["core_mul", "core_add"]),
            "libalgo.so": _meta(
                soname="libalgo.so",
                needed=["libcore.so"],
                imports=["core_mul"],
            ),
        }
        new_snapshot = _snapshot(new_metadata)
        per_lib_results = [
            _diff(
                "libcore.so",
                Change(
                    kind=ChangeKind.FUNC_PARAMS_CHANGED,
                    symbol="core_mul",
                    description="params changed",
                ),
            ),
            _diff("libalgo.so", verdict=Verdict.COMPATIBLE),
        ]

        live_result = compare_bundle(old_live, new_snapshot, per_lib_results)
        facts = capture_bundle_facts(_per_library_snapshots(old_metadata))
        facts_result = compare_bundle_from_facts(facts, new_snapshot, per_lib_results)

        assert facts_result.bundle_findings == live_result.bundle_findings
        assert any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED
            for f in live_result.bundle_findings
        )

    def test_no_change_matches_live_compare(self) -> None:
        """An unchanged bundle produces the identical (empty) finding set
        from both entry points -- the negative control."""
        old_metadata = _old_metadata()
        old_live = _snapshot(old_metadata)
        new_snapshot = _snapshot(_old_metadata())
        per_lib_results = [
            _diff("libcore.so", verdict=Verdict.COMPATIBLE),
            _diff("libalgo.so", verdict=Verdict.COMPATIBLE),
        ]

        live_result = compare_bundle(old_live, new_snapshot, per_lib_results)
        facts = capture_bundle_facts(_per_library_snapshots(old_metadata))
        facts_result = compare_bundle_from_facts(facts, new_snapshot, per_lib_results)

        assert facts_result.bundle_findings == live_result.bundle_findings == []
        assert (
            facts_result.bundle_verdict
            == live_result.bundle_verdict
            == Verdict.NO_CHANGE
        )

    def test_manifest_carried_from_facts_when_not_overridden(self) -> None:
        manifest = InstantiationManifest(
            entries=(ManifestEntry(symbol="core_mul", optional_provider=False),)
        )
        old_metadata = _old_metadata()
        new_metadata = {
            "libcore.so": _meta(
                soname="libcore.so", exports=["core_add"]
            ),  # core_mul gone
            "libalgo.so": _meta(soname="libalgo.so"),
        }
        new_snapshot = _snapshot(new_metadata)
        facts = capture_bundle_facts(
            _per_library_snapshots(old_metadata), manifest=manifest
        )

        result = compare_bundle_from_facts(
            facts,
            new_snapshot,
            [
                _diff(
                    "libcore.so",
                    Change(
                        kind=ChangeKind.FUNC_REMOVED,
                        symbol="core_mul",
                        description="removed",
                    ),
                ),
                _diff("libalgo.so", verdict=Verdict.COMPATIBLE),
            ],
        )

        assert any(
            f.kind == ChangeKind.BUNDLE_MANIFEST_INSTANTIATION_REMOVED
            for f in result.bundle_findings
        )

    def test_explicit_manifest_overrides_facts_manifest(self) -> None:
        facts_manifest = InstantiationManifest(
            entries=(ManifestEntry(symbol="never_checked", optional_provider=True),)
        )
        old_metadata = _old_metadata()
        new_snapshot = _snapshot(_old_metadata())
        facts = capture_bundle_facts(
            _per_library_snapshots(old_metadata), manifest=facts_manifest
        )

        # An explicit manifest=None override should behave exactly like no
        # manifest at all (empty entries), not fall back to facts.manifest.
        empty_manifest = InstantiationManifest(entries=())
        result = compare_bundle_from_facts(
            facts,
            new_snapshot,
            [
                _diff("libcore.so", verdict=Verdict.COMPATIBLE),
                _diff("libalgo.so", verdict=Verdict.COMPATIBLE),
            ],
            manifest=empty_manifest,
        )
        assert result.bundle_findings == []


# ---------------------------------------------------------------------------
# bundle_snapshot_from_facts
# ---------------------------------------------------------------------------


class TestBundleSnapshotFromFacts:
    def test_drops_entries_with_no_elf_metadata(self) -> None:
        facts = BundleFacts(
            per_library_snapshots={
                "libcore.so": AbiSnapshot(
                    library="libcore.so", version="1.0", elf=_meta(soname="libcore.so")
                ),
                "headers_only.so": AbiSnapshot(
                    library="headers_only.so", version="1.0", elf=None
                ),
            }
        )
        snap = bundle_snapshot_from_facts(facts)
        assert set(snap.libraries) == {"libcore.so"}

    def test_reconstructed_snapshot_has_working_resolution_graph(self) -> None:
        old_metadata = _old_metadata()
        facts = capture_bundle_facts(_per_library_snapshots(old_metadata))
        snap = bundle_snapshot_from_facts(facts)
        assert snap.resolution.consumers_of("core_mul")
        assert {c.library for c in snap.resolution.consumers_of("core_mul")} == {
            "libalgo.so"
        }


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------


class TestBundleFactsSerialization:
    def test_to_dict_from_dict_round_trip(self) -> None:
        manifest = InstantiationManifest(
            entries=(
                ManifestEntry(symbol="core_mul", optional_provider=False),
                ManifestEntry(
                    template="ns::train_ops",
                    instantiations=({"Float": "float"},),
                    library="libcore.so",
                    optional_provider=True,
                ),
                ManifestEntry(pattern="ns::detail::*", optional_provider=True),
            )
        )
        facts = capture_bundle_facts(
            _per_library_snapshots(_old_metadata()),
            manifest=manifest,
            variant_fingerprint="cpu",
        )

        round_tripped = bundle_facts_from_dict(bundle_facts_to_dict(facts))

        assert round_tripped.schema_version == BUNDLE_FACTS_SCHEMA_VERSION
        assert round_tripped.variant_fingerprint == "cpu"
        assert set(round_tripped.per_library_snapshots) == {"libcore.so", "libalgo.so"}
        assert round_tripped.per_library_snapshots["libcore.so"].elf is not None
        assert (
            round_tripped.per_library_snapshots["libcore.so"].elf.soname == "libcore.so"
        )
        assert round_tripped.manifest is not None
        assert len(round_tripped.manifest.entries) == 3
        assert round_tripped.manifest.entries[0].symbol == "core_mul"
        assert round_tripped.manifest.entries[1].template == "ns::train_ops"
        assert round_tripped.manifest.entries[1].instantiations == ({"Float": "float"},)
        assert round_tripped.manifest.entries[2].pattern == "ns::detail::*"

    def test_no_manifest_round_trips_to_none(self) -> None:
        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        round_tripped = bundle_facts_from_dict(bundle_facts_to_dict(facts))
        assert round_tripped.manifest is None

    def test_save_load_file_round_trip(self, tmp_path: Path) -> None:
        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        out = tmp_path / "old.bundlefacts.json"
        save_bundle_facts(facts, out)
        loaded = load_bundle_facts(out)

        assert loaded.schema_version == facts.schema_version
        assert set(loaded.per_library_snapshots) == set(facts.per_library_snapshots)

    def test_save_load_file_round_trip_compressed(self, tmp_path: Path) -> None:
        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        out = tmp_path / "old.bundlefacts.json.gz"
        save_bundle_facts(facts, out)
        loaded = load_bundle_facts(out)
        assert set(loaded.per_library_snapshots) == set(facts.per_library_snapshots)

    def test_save_load_end_to_end_matches_live_compare(self, tmp_path: Path) -> None:
        """The full round trip: capture -> save -> load -> compare, still
        matching a live compare_bundle() on the identical underlying facts."""
        old_metadata = _old_metadata()
        old_live = _snapshot(old_metadata)
        new_metadata = {
            "libcore.so": _meta(soname="libcore.so", exports=["core_add"]),
            "libalgo.so": _meta(
                soname="libalgo.so",
                needed=["libcore.so"],
                imports=["core_mul"],
            ),
        }
        new_snapshot = _snapshot(new_metadata)
        per_lib_results = [
            _diff(
                "libcore.so",
                Change(
                    kind=ChangeKind.FUNC_REMOVED,
                    symbol="core_mul",
                    description="removed",
                ),
            ),
            _diff("libalgo.so", verdict=Verdict.COMPATIBLE),
        ]
        live_result = compare_bundle(old_live, new_snapshot, per_lib_results)

        facts = capture_bundle_facts(_per_library_snapshots(old_metadata))
        out = tmp_path / "old.bundlefacts.json"
        save_bundle_facts(facts, out)
        loaded_facts = load_bundle_facts(out)
        facts_result = compare_bundle_from_facts(
            loaded_facts, new_snapshot, per_lib_results
        )

        assert facts_result.bundle_findings == live_result.bundle_findings
