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
from abicheck.bundle_manifest import (
    InstantiationManifest,
    ManifestEntry,
    manifest_from_dict,
)
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


# ---------------------------------------------------------------------------
# Malformed manifest data (Codex/CodeRabbit review: reject, don't silently
# discard the manifest promises a corrupt facts file claims to carry)
# ---------------------------------------------------------------------------


class TestManifestFromDictValidation:
    def test_missing_provides_key_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="provides"):
            manifest_from_dict({})

    def test_non_list_provides_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="provides"):
            manifest_from_dict({"provides": "not-a-list"})

    def test_non_dict_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="provides"):
            manifest_from_dict([])

    def test_empty_manifest_field_is_not_silently_dropped(self) -> None:
        """A stored `"manifest": {}` (present but malformed, missing its
        own `provides:` key) must raise -- not be treated the same as
        `"manifest": null` (genuinely absent)."""
        import pytest

        facts_dict = bundle_facts_to_dict(
            capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        )
        facts_dict["manifest"] = {}
        with pytest.raises(ValueError, match="provides"):
            bundle_facts_from_dict(facts_dict)

    def test_null_manifest_field_round_trips_to_none(self) -> None:
        facts_dict = bundle_facts_to_dict(
            capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        )
        facts_dict["manifest"] = None
        assert bundle_facts_from_dict(facts_dict).manifest is None


# ---------------------------------------------------------------------------
# write_bundle_facts_out (the CLI producer helper) -- usage-error contract
# ---------------------------------------------------------------------------


class TestWriteBundleFactsOut:
    def _diff_pairs(self) -> list[tuple[DiffResult, AbiSnapshot]]:
        snapshots = _per_library_snapshots(_old_metadata())
        return [
            (_diff(name, verdict=Verdict.COMPATIBLE), snap)
            for name, snap in snapshots.items()
        ]

    def test_writes_a_loadable_facts_file(self, tmp_path: Path) -> None:
        from abicheck.cli_compare_release_helpers import write_bundle_facts_out

        out = tmp_path / "old.bundlefacts.json"
        write_bundle_facts_out(out, self._diff_pairs(), None)

        loaded = load_bundle_facts(out)
        assert set(loaded.per_library_snapshots) == {"libcore.so", "libalgo.so"}
        assert loaded.manifest is None

    def test_malformed_manifest_raises_usage_error(self, tmp_path: Path) -> None:
        import click
        import pytest

        from abicheck.cli_compare_release_helpers import write_bundle_facts_out

        bad_manifest = tmp_path / "bad.yaml"
        bad_manifest.write_text("provides: not-a-list\n")
        out = tmp_path / "old.bundlefacts.json"

        with pytest.raises(click.UsageError, match="bundle-facts-out"):
            write_bundle_facts_out(out, self._diff_pairs(), bad_manifest)
        assert not out.exists()

    def test_unwritable_output_path_raises_usage_error(self, tmp_path: Path) -> None:
        import click
        import pytest

        from abicheck.cli_compare_release_helpers import write_bundle_facts_out

        # save_bundle_facts()/write_snapshot_bytes() auto-creates missing
        # parent directories, so the reliable way to force a real OSError is
        # a parent path component that already exists as a *file* --
        # Path.mkdir(parents=True) then raises NotADirectoryError (an OSError
        # subclass) trying to descend through it.
        blocking_file = tmp_path / "blocking"
        blocking_file.write_text("not a directory")
        out = blocking_file / "old.bundlefacts.json"

        with pytest.raises(click.UsageError, match="bundle-facts-out"):
            write_bundle_facts_out(out, self._diff_pairs(), None)
