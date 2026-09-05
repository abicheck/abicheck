"""Unit tests for persisted bundle facts (G38 Phase 2, ADR-023 amendment).

Mirrors ``tests/test_bundle.py``'s in-memory ``ElfMetadata`` fixture style
so most of these tests need no gcc/castxml and no real compiled binaries --
one class at the bottom (``@pytest.mark.integration``) is the exception,
compiling a real tiny ``.so`` to prove the ``write_bundle_facts_out()``
fallback captures a real, DWARF-derived snapshot, not just bare
``ElfMetadata``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.bundle import _compute_resolution_graph, compare_bundle
from abicheck.bundle_facts import (
    BUNDLE_FACTS_BASE_SCHEMA_VERSION,
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
from abicheck.model import AbiSnapshot, Function, Visibility
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
# G38 stabilization Phase 12: live/stored Phase-4 parity.
#
# Before Phase 12, compare_bundle_from_facts() delegated only to
# compare_bundle() -- find_unverified_signature_findings() (the Phase 4
# C-boundary signature-evidence gate) was a separate companion the live
# `compare --release` CLI path called directly, so a stored-facts
# comparison never ran it at all. This is the mandatory acceptance test
# extended to that gap: a stored-old-vs-live-new comparison given both
# sides' signature evidence must produce the identical
# BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED findings a live analyze_bundle()
# call over the same underlying evidence would -- not just the graph-native
# findings compare_bundle() alone covers (already pinned by the parity
# class above, which predates Phase 12 and deliberately never passed any
# signature evidence at all).
# ---------------------------------------------------------------------------


def _elf_only_fn(symbol: str) -> Function:
    return Function(
        name=symbol, mangled=symbol, return_type="?", visibility=Visibility.ELF_ONLY
    )


class TestCompareBundleFromFactsPhase4Parity:
    def _bundle_metadata(self) -> dict[str, ElfMetadata]:
        return {
            "libcore.so": _meta(soname="libcore.so", exports=["core_fn"]),
            "libconsumer.so": _meta(
                soname="libconsumer.so", needed=["libcore.so"], imports=["core_fn"]
            ),
        }

    def _signature_evidence(self, version: str) -> dict[str, AbiSnapshot]:
        # elf_only_mode=True on both sides -- no header evidence at all for
        # this symbol, so find_unverified_signature_findings() cannot
        # confirm or deny the signature actually agrees between old and
        # new, which is exactly the "unverified" (not "no change", not a
        # confirmed BREAKING change) finding this gate exists to raise.
        return {
            "libcore.so": AbiSnapshot(
                library="libcore.so",
                version=version,
                functions=[_elf_only_fn("core_fn")],
                elf_only_mode=True,
            )
        }

    def test_signature_unverified_matches_live_analyze_bundle(self) -> None:
        from abicheck.bundle_analysis import analyze_bundle

        metadata = self._bundle_metadata()
        old_live = _snapshot(metadata)
        new_snapshot = _snapshot(metadata)  # unchanged at the ELF/graph level
        per_lib_results = [
            _diff("libcore.so", verdict=Verdict.NO_CHANGE),
            _diff("libconsumer.so", verdict=Verdict.NO_CHANGE),
        ]
        old_evidence = self._signature_evidence("old")
        new_evidence = self._signature_evidence("new")

        live_result = analyze_bundle(
            old_live,
            new_snapshot,
            per_lib_results,
            old_signature_evidence=old_evidence,
            new_signature_evidence=new_evidence,
        )

        # capture_bundle_facts() needs a full metadata-carrying snapshot map
        # (for bundle_snapshot_from_facts()'s own graph reconstruction), not
        # only the signature-evidence one -- but old_evidence's own
        # AbiSnapshot for libcore.so already carries no `.elf`, so build the
        # facts source from the real per-library snapshots (mirroring every
        # other test in this module) while reusing old_evidence as the
        # signature-evidence map passed alongside it, exactly the shape
        # `BundleFacts.per_library_snapshots` itself is (a real
        # `AbiSnapshot` map doubling as both).
        facts_snapshots = {
            "libcore.so": AbiSnapshot(
                library="libcore.so",
                version="old",
                elf=metadata["libcore.so"],
                functions=[_elf_only_fn("core_fn")],
                elf_only_mode=True,
            ),
            "libconsumer.so": AbiSnapshot(
                library="libconsumer.so",
                version="old",
                elf=metadata["libconsumer.so"],
            ),
        }
        facts = capture_bundle_facts(facts_snapshots)
        facts_result = compare_bundle_from_facts(
            facts,
            new_snapshot,
            per_lib_results,
            new_signature_evidence=new_evidence,
        )

        assert facts_result.bundle_findings == live_result.bundle_findings
        assert any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED
            for f in live_result.bundle_findings
        )

    def test_no_new_signature_evidence_means_phase4_does_not_run(self) -> None:
        """Omitting `new_signature_evidence` (today's only real caller
        shape -- G38 Phase 13's stored-facts CLI consumer doesn't exist
        yet) must behave exactly like every pre-Phase-12 caller: no
        BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED finding, even though the OLD
        side's own `per_library_snapshots` would otherwise be usable
        evidence."""
        metadata = self._bundle_metadata()
        new_snapshot = _snapshot(metadata)
        per_lib_results = [
            _diff("libcore.so", verdict=Verdict.NO_CHANGE),
            _diff("libconsumer.so", verdict=Verdict.NO_CHANGE),
        ]
        facts_snapshots = {
            "libcore.so": AbiSnapshot(
                library="libcore.so",
                version="old",
                elf=metadata["libcore.so"],
                functions=[_elf_only_fn("core_fn")],
                elf_only_mode=True,
            ),
            "libconsumer.so": AbiSnapshot(
                library="libconsumer.so", version="old", elf=metadata["libconsumer.so"]
            ),
        }
        facts = capture_bundle_facts(facts_snapshots)

        result = compare_bundle_from_facts(facts, new_snapshot, per_lib_results)

        assert not any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED
            for f in result.bundle_findings
        )


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

        assert round_tripped.schema_version == BUNDLE_FACTS_BASE_SCHEMA_VERSION
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
# Schema-version rejection (Codex review, fresh evidence): a container
# schema_version newer than this reader's own must be rejected outright.
# ---------------------------------------------------------------------------


class TestBundleFactsSchemaVersionRejection:
    def test_current_schema_version_round_trips(self) -> None:
        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        d = bundle_facts_to_dict(facts)  # clean: base version (ADR-065 D8)
        assert d["schema_version"] == BUNDLE_FACTS_BASE_SCHEMA_VERSION
        assert bundle_facts_from_dict(d).schema_version == d["schema_version"]

    def test_newer_schema_version_is_rejected(self) -> None:
        import pytest

        from abicheck.errors import IncompatibleSnapshotSchemaError

        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        d = bundle_facts_to_dict(facts)
        d["schema_version"] = BUNDLE_FACTS_SCHEMA_VERSION + 1

        with pytest.raises(IncompatibleSnapshotSchemaError, match="schema_version"):
            bundle_facts_from_dict(d)

    def test_missing_schema_version_defaults_to_current(self) -> None:
        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        d = bundle_facts_to_dict(facts)
        del d["schema_version"]
        assert bundle_facts_from_dict(d).schema_version == BUNDLE_FACTS_SCHEMA_VERSION


class TestBundleFactsRejectsMissingSnapshotMapping:
    """``per_library_snapshots`` is mandatory (Codex review, fresh evidence):
    a malformed or unrelated JSON object omitting it must not silently load
    as a valid, current-schema *empty* bundle -- that would let a later
    ``compare_bundle_from_facts()`` score every new library against an
    invented empty baseline instead of the caller ever finding out the
    input was invalid."""

    def test_missing_key_is_rejected(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="per_library_snapshots"):
            bundle_facts_from_dict({})

    def test_wrong_shaped_value_is_rejected(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="per_library_snapshots"):
            bundle_facts_from_dict({"per_library_snapshots": "not-a-mapping"})

    def test_empty_mapping_is_still_accepted(self) -> None:
        # An explicitly-empty mapping is a legitimate (if useless) bundle
        # facts document -- distinct from the key being absent entirely.
        facts = bundle_facts_from_dict({"per_library_snapshots": {}})
        assert facts.per_library_snapshots == {}


# ---------------------------------------------------------------------------
# Filesystem-alias capture/replay (Codex review, fresh evidence): a provider
# without a usable DT_SONAME whose real on-disk file is a symlink/hard-link
# alias must still resolve after a facts round trip, with no filesystem
# access at load time.
# ---------------------------------------------------------------------------


class TestBundleFactsFilesystemAliases:
    def test_capture_bundle_facts_records_symlink_target_alias(
        self, tmp_path: Path
    ) -> None:
        real_target = tmp_path / "libcore.so.1.0.0"
        real_target.write_bytes(b"")
        symlink_path = tmp_path / "libcore.so"
        symlink_path.symlink_to(real_target)

        facts = capture_bundle_facts(
            _per_library_snapshots(_old_metadata()),
            library_paths={"libcore.so": symlink_path},
        )

        assert "libcore.so.1.0.0" in facts.filesystem_aliases.get("libcore.so", ())

    def test_no_library_paths_means_no_aliases(self) -> None:
        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        assert facts.filesystem_aliases == {}

    def test_filesystem_aliases_round_trip_through_dict(self, tmp_path: Path) -> None:
        real_target = tmp_path / "libcore.so.1.0.0"
        real_target.write_bytes(b"")
        symlink_path = tmp_path / "libcore.so"
        symlink_path.symlink_to(real_target)

        facts = capture_bundle_facts(
            _per_library_snapshots(_old_metadata()),
            library_paths={"libcore.so": symlink_path},
        )
        round_tripped = bundle_facts_from_dict(bundle_facts_to_dict(facts))

        assert round_tripped.filesystem_aliases == facts.filesystem_aliases

    def test_reconstructed_bundle_snapshot_resolves_the_alias(
        self, tmp_path: Path
    ) -> None:
        """The end-to-end point of capturing aliases: a consumer's
        DT_NEEDED naming the real, versioned filename (not the symlink's
        own canonical key) must still resolve as intra-bundle once
        reconstructed from facts alone, with no filesystem access.
        """
        real_target = tmp_path / "libcore.so.1.0.0"
        real_target.write_bytes(b"")
        symlink_path = tmp_path / "libcore.so"
        symlink_path.symlink_to(real_target)

        metadata = {
            # No DT_SONAME -- the only way a sibling can name this
            # provider is via its on-disk filename or an alias of it.
            "libcore.so": _meta(exports=["core_mul"]),
            "libalgo.so": _meta(
                needed=["libcore.so.1.0.0"],  # names the real target, not the key
                imports=["core_mul"],
            ),
        }
        facts = capture_bundle_facts(
            _per_library_snapshots(metadata),
            library_paths={"libcore.so": symlink_path},
        )
        reconstructed = bundle_snapshot_from_facts(facts)

        # Resolved as intra-bundle (via the captured alias), not "extra"
        # (external/system) -- the classification _compute_resolution_graph
        # makes at reconstruction time using the persisted alias.
        assert "libcore.so.1.0.0" in reconstructed.resolution.intra_needed.get(
            "libalgo.so", []
        )
        assert "libcore.so.1.0.0" not in reconstructed.resolution.extra_needed.get(
            "libalgo.so", []
        )

    def test_alias_for_an_elf_less_provider_is_not_indexed(
        self, tmp_path: Path
    ) -> None:
        """A captured alias for a library whose own snapshot carries no ELF
        metadata (dropped by ``bundle_snapshot_from_facts()`` before this
        alias is ever consumed) must not resolve a consumer's DT_NEEDED to
        a bundle member that doesn't actually exist in the reconstructed
        snapshot -- a live ``build_bundle_snapshot()`` never has an alias
        for a provider it didn't parse in the first place (Codex review,
        fresh evidence).
        """
        real_target = tmp_path / "libcore.so.1.0.0"
        real_target.write_bytes(b"")
        symlink_path = tmp_path / "libcore.so"
        symlink_path.symlink_to(real_target)

        per_library_snapshots = {
            # No ELF metadata at all -- dropped by bundle_snapshot_from_facts().
            "libcore.so": AbiSnapshot(library="libcore.so", version="old", elf=None),
            "libalgo.so": AbiSnapshot(
                library="libalgo.so",
                version="old",
                elf=_meta(
                    needed=["libcore.so.1.0.0"],  # names the (dropped) alias
                    imports=["core_mul"],
                ),
            ),
        }
        facts = capture_bundle_facts(
            per_library_snapshots,
            library_paths={"libcore.so": symlink_path},
        )
        # The alias was captured despite libcore.so having no ELF metadata --
        # capture_bundle_facts() doesn't know yet that reconstruction will
        # drop it.
        assert "libcore.so.1.0.0" in facts.filesystem_aliases.get("libcore.so", ())

        reconstructed = bundle_snapshot_from_facts(facts)

        assert "libcore.so" not in reconstructed.metadata
        assert "libcore.so.1.0.0" not in reconstructed.resolution.intra_needed.get(
            "libalgo.so", []
        )
        assert "libcore.so.1.0.0" in reconstructed.resolution.extra_needed.get(
            "libalgo.so", []
        )


class TestBundleFactsRejectsMalformedFilesystemAliases:
    """A string value where a list is documented (Codex review, fresh
    evidence): ``tuple("libfoo.so.1")`` silently iterates characters
    (``"l"``, ``"i"``, ...) instead of raising, so a real alias quietly
    fails to resolve with no load-time error at all."""

    def test_string_valued_alias_list_is_rejected(self) -> None:
        facts_dict = bundle_facts_to_dict(
            capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        )
        facts_dict["filesystem_aliases"] = {"libcore.so": "libfoo.so.1"}
        with pytest.raises(ValueError, match="filesystem_aliases"):
            bundle_facts_from_dict(facts_dict)

    def test_non_string_element_is_rejected(self) -> None:
        facts_dict = bundle_facts_to_dict(
            capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        )
        facts_dict["filesystem_aliases"] = {"libcore.so": ["ok", 42]}
        with pytest.raises(ValueError, match="filesystem_aliases"):
            bundle_facts_from_dict(facts_dict)

    def test_non_mapping_container_is_rejected(self) -> None:
        facts_dict = bundle_facts_to_dict(
            capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        )
        facts_dict["filesystem_aliases"] = ["not", "a", "mapping"]
        with pytest.raises(ValueError, match="filesystem_aliases"):
            bundle_facts_from_dict(facts_dict)

    def test_well_formed_alias_list_still_round_trips(self) -> None:
        facts_dict = bundle_facts_to_dict(
            capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        )
        facts_dict["filesystem_aliases"] = {"libcore.so": ["libfoo.so.1", "libfoo.so"]}
        loaded = bundle_facts_from_dict(facts_dict)
        assert loaded.filesystem_aliases == {"libcore.so": ("libfoo.so.1", "libfoo.so")}


class TestBundleFactsRejectsMalformedLibraryFilenames:
    """A non-string value (Codex review, fresh evidence): a bare
    ``str(filename)`` coercion silently turns JSON ``null`` into the
    invented basename ``"None"`` instead of raising, letting a corrupt
    baseline derive a fabricated SONAME major on replay."""

    def test_null_valued_filename_is_rejected(self) -> None:
        facts_dict = bundle_facts_to_dict(
            capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        )
        facts_dict["library_filenames"] = {"libcore.so": None}
        with pytest.raises(ValueError, match="library_filenames"):
            bundle_facts_from_dict(facts_dict)

    def test_non_string_element_is_rejected(self) -> None:
        facts_dict = bundle_facts_to_dict(
            capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        )
        facts_dict["library_filenames"] = {"libcore.so": 42}
        with pytest.raises(ValueError, match="library_filenames"):
            bundle_facts_from_dict(facts_dict)

    def test_non_mapping_container_is_rejected(self) -> None:
        facts_dict = bundle_facts_to_dict(
            capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        )
        facts_dict["library_filenames"] = ["not", "a", "mapping"]
        with pytest.raises(ValueError, match="library_filenames"):
            bundle_facts_from_dict(facts_dict)

    def test_well_formed_filenames_still_round_trip(self) -> None:
        facts_dict = bundle_facts_to_dict(
            capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        )
        facts_dict["library_filenames"] = {"libcore.so": "libfoo_core.so.1"}
        loaded = bundle_facts_from_dict(facts_dict)
        assert loaded.library_filenames == {"libcore.so": "libfoo_core.so.1"}


class TestBundleFactsLibraryFilenames:
    """SONAME-skew replay needs the real on-disk filename, not the
    canonical key (Codex review, fresh evidence): a versioned DSO with no
    ``DT_SONAME`` is identified by ``bundle._detect_soname_skew``'s own
    ``path.name`` fallback, and without ``library_filenames``,
    ``bundle_snapshot_from_facts()`` synthesized ``Path(canonical_key)`` --
    ``"libfoo_core.so"``, never the real ``"libfoo_core.so.1"`` -- so no
    major was ever derivable from a reconstructed bundle.
    """

    def test_capture_bundle_facts_records_the_real_filename(
        self, tmp_path: Path
    ) -> None:
        real_path = tmp_path / "libfoo_core.so.1"
        real_path.write_bytes(b"")

        facts = capture_bundle_facts(
            _per_library_snapshots(_old_metadata()),
            library_paths={"libcore.so": real_path},
        )

        assert facts.library_filenames.get("libcore.so") == "libfoo_core.so.1"

    def test_records_the_resolved_target_for_a_symlinked_library(
        self, tmp_path: Path
    ) -> None:
        """A dev symlink (libfoo_core.so -> libfoo_core.so.1.0.0) must
        record the resolved target's basename, not the symlink's own,
        unversioned name -- else the major is undiscoverable from a
        reconstructed bundle the same way an unresolved canonical key was
        (Codex review, fresh evidence)."""
        real_target = tmp_path / "libfoo_core.so.1.0.0"
        real_target.write_bytes(b"")
        symlink_path = tmp_path / "libfoo_core.so"
        symlink_path.symlink_to(real_target)

        facts = capture_bundle_facts(
            _per_library_snapshots(_old_metadata()),
            library_paths={"libcore.so": symlink_path},
        )

        assert facts.library_filenames.get("libcore.so") == "libfoo_core.so.1.0.0"

    def test_no_library_paths_means_no_filenames(self) -> None:
        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        assert facts.library_filenames == {}

    def test_library_filenames_round_trip_through_dict(self, tmp_path: Path) -> None:
        real_path = tmp_path / "libfoo_core.so.1"
        real_path.write_bytes(b"")

        facts = capture_bundle_facts(
            _per_library_snapshots(_old_metadata()),
            library_paths={"libcore.so": real_path},
        )
        round_tripped = bundle_facts_from_dict(bundle_facts_to_dict(facts))

        assert round_tripped.library_filenames == facts.library_filenames

    def test_reconstructed_bundle_uses_the_real_filename_not_the_key(
        self, tmp_path: Path
    ) -> None:
        real_path = tmp_path / "libfoo_core.so.1"
        real_path.write_bytes(b"")

        facts = capture_bundle_facts(
            _per_library_snapshots(_old_metadata()),
            library_paths={"libcore.so": real_path},
        )
        reconstructed = bundle_snapshot_from_facts(facts)

        assert reconstructed.libraries["libcore.so"].name == "libfoo_core.so.1"

    def test_soname_skew_detected_end_to_end_from_a_stored_baseline(
        self, tmp_path: Path
    ) -> None:
        """The full point: a stored-baseline comparison must catch the
        identical `bundle_soname_skew` a live comparison would, for a
        cohort member with no DT_SONAME identified only by filename.
        """
        old_core_path = tmp_path / "libfoo_core.so.1"
        old_core_path.write_bytes(b"")
        old_metadata = {
            "libfoo_core.so": _meta(exports=["c"]),  # no DT_SONAME
            "libfoo_thread.so": _meta(soname="libfoo_thread.so.1", exports=["t"]),
        }
        facts = capture_bundle_facts(
            _per_library_snapshots(old_metadata),
            library_paths={
                "libfoo_core.so": old_core_path,
                "libfoo_thread.so": tmp_path / "libfoo_thread.so.1",
            },
        )
        # New release: core bumps its filename major to .so.2 (still no
        # DT_SONAME); thread lags at .so.1 -- a real skew within the cohort.
        new_metadata = {
            "libfoo_core.so": _meta(exports=["c"]),
            "libfoo_thread.so": _meta(soname="libfoo_thread.so.1", exports=["t"]),
        }
        from abicheck.bundle import build_bundle_snapshot_from_metadata

        new_bundle = build_bundle_snapshot_from_metadata(
            new_metadata,
            paths={
                "libfoo_core.so": Path("libfoo_core.so.2"),
                "libfoo_thread.so": Path("libfoo_thread.so.1"),
            },
        )

        result = compare_bundle_from_facts(facts, new_bundle, [], cohorts=["libfoo_"])
        kinds = {f.kind for f in result.bundle_findings}
        assert ChangeKind.BUNDLE_SONAME_SKEW in kinds

    def test_soname_skew_parity_with_a_symlinked_representative_path(
        self, tmp_path: Path
    ) -> None:
        """Live and reconstructed bundle analysis must agree, even when the
        OLD side's discovered representative path is an unversioned dev
        symlink (the common `libfoo_core.so -> libfoo_core.so.1.0.0` layout
        `discover_artifact_set()` keeps) -- resolving the symlink for the
        stored-facts side alone, without the identical resolve on the live
        `_detect_soname_skew()` fallback, would make a stored-baseline
        comparison report a skew a live comparison of the same on-disk
        layout would not (Codex review, fresh evidence).
        """
        from abicheck.bundle import (
            _detect_soname_skew,
            build_bundle_snapshot_from_metadata,
        )

        real_target = tmp_path / "libfoo_core.so.1"
        real_target.write_bytes(b"")
        symlink_path = tmp_path / "libfoo_core.so"
        symlink_path.symlink_to(real_target)

        old_metadata = {
            "libfoo_core.so": _meta(exports=["c"]),  # no DT_SONAME
            "libfoo_thread.so": _meta(soname="libfoo_thread.so.1", exports=["t"]),
        }
        new_metadata = {
            "libfoo_core.so": _meta(exports=["c"]),
            "libfoo_thread.so": _meta(soname="libfoo_thread.so.1", exports=["t"]),
        }
        new_bundle = build_bundle_snapshot_from_metadata(
            new_metadata,
            paths={
                "libfoo_core.so": Path("libfoo_core.so.2"),  # bumped
                "libfoo_thread.so": Path("libfoo_thread.so.1"),  # lagging
            },
        )

        # Live: the OLD side's representative path is the symlink itself,
        # exactly as directory discovery would keep it. probe_filesystem=True
        # marks this snapshot's paths as real, live filesystem paths (what
        # build_bundle_snapshot() itself always passes) -- required for
        # _detect_soname_skew()'s fallback to resolve the symlink at all
        # (see BundleSnapshot.filesystem_backed's own docstring).
        old_live = build_bundle_snapshot_from_metadata(
            old_metadata,
            paths={
                "libfoo_core.so": symlink_path,
                "libfoo_thread.so": tmp_path / "libfoo_thread.so.1",
            },
            probe_filesystem=True,
        )
        live_kinds = {
            f.kind for f in _detect_soname_skew(old_live, new_bundle, ["libfoo_"])
        }

        # Stored-facts: captured from the identical on-disk layout.
        facts = capture_bundle_facts(
            _per_library_snapshots(old_metadata),
            library_paths={
                "libfoo_core.so": symlink_path,
                "libfoo_thread.so": tmp_path / "libfoo_thread.so.1",
            },
        )
        reconstructed = bundle_snapshot_from_facts(facts)
        stored_kinds = {
            f.kind for f in _detect_soname_skew(reconstructed, new_bundle, ["libfoo_"])
        }

        assert stored_kinds == live_kinds == {ChangeKind.BUNDLE_SONAME_SKEW}

    def test_stored_replay_ignores_a_colliding_cwd_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A facts-reconstructed snapshot's paths are synthetic (bare
        ``Path(persisted_filename)``, no real file behind them) -- the
        persisted major must be used as-is, never re-resolved against the
        replay process's current working directory. Without this,
        an unrelated real symlink in CWD happening to share the persisted
        basename would silently substitute *its* target's major for the
        one actually captured, making stored replay depend on CWD (Codex
        review, fresh evidence).
        """
        capture_dir = tmp_path / "capture"
        capture_dir.mkdir()
        real_target = capture_dir / "libfoo_core.so.5"
        real_target.write_bytes(b"")
        symlink_path = capture_dir / "libfoo_core.so"
        symlink_path.symlink_to(real_target)

        old_metadata = {
            "libfoo_core.so": _meta(exports=["c"]),  # no DT_SONAME
            "libfoo_thread.so": _meta(soname="libfoo_thread.so.1", exports=["t"]),
        }
        facts = capture_bundle_facts(
            _per_library_snapshots(old_metadata),
            library_paths={
                "libfoo_core.so": symlink_path,
                "libfoo_thread.so": capture_dir / "libfoo_thread.so.1",
            },
        )
        assert facts.library_filenames["libfoo_core.so"] == "libfoo_core.so.5"

        # Replay happens in a *different* directory that coincidentally
        # holds a same-named symlink pointing at a *lower* major -- were the
        # persisted name re-resolved against this CWD, it would look like a
        # bump (2 -> 5) happened between old and new, when nothing changed.
        replay_cwd = tmp_path / "replay"
        replay_cwd.mkdir()
        colliding_target = replay_cwd / "libfoo_core.so.2"
        colliding_target.write_bytes(b"")
        (replay_cwd / "libfoo_core.so.5").symlink_to(colliding_target)
        monkeypatch.chdir(replay_cwd)

        # The NEW side is a real, live release directory (unrelated to
        # replay_cwd) at its own real, unchanged major -- probe_filesystem=
        # True marks it filesystem_backed, matching build_bundle_snapshot()'s
        # own real usage, so its own resolution is unaffected by the CWD
        # collision above.
        release_dir = tmp_path / "release"
        release_dir.mkdir()
        new_real_target = release_dir / "libfoo_core.so.5"
        new_real_target.write_bytes(b"")

        from abicheck.bundle import (
            _detect_soname_skew,
            build_bundle_snapshot_from_metadata,
        )

        new_metadata = {
            "libfoo_core.so": _meta(exports=["c"]),
            "libfoo_thread.so": _meta(soname="libfoo_thread.so.1", exports=["t"]),
        }
        new_bundle = build_bundle_snapshot_from_metadata(
            new_metadata,
            paths={
                "libfoo_core.so": new_real_target,  # real file, unchanged major
                "libfoo_thread.so": release_dir / "libfoo_thread.so.1",
            },
            probe_filesystem=True,
        )

        reconstructed = bundle_snapshot_from_facts(facts)

        # No skew: the persisted major (5) matches the new side's real major
        # (5), and libfoo_thread stayed at 1 on both sides. A CWD-dependent
        # re-resolution of the OLD side's synthetic path through the
        # colliding symlink above would read old major as 2, making
        # libfoo_core look like it bumped 2 -> 5 while libfoo_thread
        # stayed -- exactly the pattern detect_bundle_soname_skew() flags --
        # and spuriously report a skew that never happened.
        kinds = {
            f.kind for f in _detect_soname_skew(reconstructed, new_bundle, ["libfoo_"])
        }
        assert kinds == set()


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


class TestSaveBundleFactsPreservesTemplateInstantiationOrder:
    """``save_bundle_facts()`` must not reorder a manifest entry's own
    ``instantiations`` dict -- its iteration order *is* the C++ template
    argument order (``_expand_instantiations()``'s own documented contract),
    and a real ``json.dumps(..., sort_keys=True)`` (the pre-fix code)
    silently re-sorts it alphabetically, corrupting a valid contract into
    one that matches no real exported symbol (Codex review, fresh
    evidence).
    """

    def test_save_and_load_preserves_parameter_order(self, tmp_path: Path) -> None:
        # Alphabetical order ("Method" < "Precision") differs from the real
        # declared order here, so a sort_keys=True regression is caught.
        manifest = InstantiationManifest(
            entries=(
                ManifestEntry(
                    template="acme::train_ops",
                    instantiations=({"Precision": "float", "Method": "dense"},),
                ),
            )
        )
        facts = capture_bundle_facts(
            _per_library_snapshots(_old_metadata()), manifest=manifest
        )
        out = tmp_path / "facts.json"
        save_bundle_facts(facts, out)
        loaded = load_bundle_facts(out)

        entry = loaded.manifest.entries[0]
        assert entry.display_name() == "acme::train_ops<float, dense>"


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

    def _old_map(self, tmp_path: Path) -> dict[str, Path]:
        # Real (if empty) files -- service.resolve_input() raises on a
        # non-ELF file (no snapshot format matches), so the fallback loop's
        # own except-and-degrade-to-parse_elf_metadata path (which never
        # raises) is what actually gets exercised here, without a real
        # compiled .so.
        old_map: dict[str, Path] = {}
        for name in ("libcore.so", "libalgo.so"):
            p = tmp_path / name
            p.write_bytes(b"")
            old_map[name] = p
        return old_map

    def _facts_kwargs(self) -> dict[str, object]:
        """Default no-op ``resolve_stranded_library`` for
        ``write_bundle_facts_out()``'s one keyword-only parameter -- every
        test here cares about the fallback's *key*/*presence* behavior, not
        the resolved snapshot's own content, so this mirrors the real
        production closure (``cli_compare_release.py``'s
        ``_resolve_stranded_library``) with an empty header/include list,
        which makes ``service.resolve_input()`` fail closed for any
        non-trivial input, landing on the bare-``ElfMetadata`` degrade path.
        """

        def resolve_stranded_library(old_path: Path) -> AbiSnapshot:
            from abicheck.cli_resolve import _resolve_input
            from abicheck.elf_metadata import parse_elf_metadata

            try:
                return _resolve_input(
                    old_path,
                    [],
                    [],
                    "old",
                    "c++",
                    include_dependencies=True,
                )
            except Exception:
                return AbiSnapshot(
                    library=old_path.name,
                    version="",
                    elf=parse_elf_metadata(old_path),
                )

        return {"resolve_stranded_library": resolve_stranded_library}

    def test_writes_a_loadable_facts_file(self, tmp_path: Path) -> None:
        from abicheck.cli_compare_release_helpers import write_bundle_facts_out

        out = tmp_path / "old.bundlefacts.json"
        write_bundle_facts_out(
            out,
            self._diff_pairs(),
            None,
            self._old_map(tmp_path),
            **self._facts_kwargs(),
        )

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
            write_bundle_facts_out(
                out,
                self._diff_pairs(),
                bad_manifest,
                self._old_map(tmp_path),
                **self._facts_kwargs(),
            )
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
            write_bundle_facts_out(
                out,
                self._diff_pairs(),
                None,
                self._old_map(tmp_path),
                **self._facts_kwargs(),
            )

    def test_captures_unmatched_old_library(self, tmp_path: Path) -> None:
        """P1 (Codex review, fresh evidence): an old-only library (removed
        in the new release) has no entry in ``diff_pairs`` -- it must still
        be captured, via the ``old_map`` fallback, so a later
        ``compare_bundle_from_facts()`` call can still emit
        ``bundle_library_removed``/dependency-removal/version-resolution
        findings for it, the same way a live ``compare_bundle()`` run
        would.
        """
        from abicheck.cli_compare_release_helpers import write_bundle_facts_out

        old_map = self._old_map(tmp_path)
        removed_path = tmp_path / "libremoved.so"
        removed_path.write_bytes(b"")
        old_map["libremoved.so"] = removed_path

        out = tmp_path / "old.bundlefacts.json"
        write_bundle_facts_out(
            out, self._diff_pairs(), None, old_map, **self._facts_kwargs()
        )

        loaded = load_bundle_facts(out)
        assert set(loaded.per_library_snapshots) == {
            "libcore.so",
            "libalgo.so",
            "libremoved.so",
        }
        # A non-ELF file degrades to an empty (not absent) ElfMetadata --
        # the entry is still present, mirroring build_bundle_snapshot()'s
        # own "only promise what was actually captured" rule.
        assert loaded.per_library_snapshots["libremoved.so"].elf is not None

    def test_captures_a_matched_library_whose_compare_failed(
        self, tmp_path: Path
    ) -> None:
        """P1 (Codex review, fresh evidence): a library present in *both*
        releases (present in ``old_map``/``new_map`` alike) whose
        per-library compare returned ``ERROR``/``not_comparable`` has no
        entry in ``diff_pairs`` either -- ``_compare_release_libraries()``
        only stashes a ``(DiffResult, AbiSnapshot)`` pair for a
        *successful* compare. An earlier revision of this fix only
        back-filled ``removed_keys`` (an old-only library), missing this
        second, matched-but-failed case entirely -- a real old-release
        library was still silently dropped from the persisted baseline
        even though it wasn't "removed" in the release-matching sense at
        all. The fallback must cover *any* ``old_map`` key
        ``diff_pairs`` didn't capture, not just the ones the caller
        happens to also classify as removed.
        """
        from abicheck.cli_compare_release_helpers import write_bundle_facts_out

        old_map = self._old_map(tmp_path)
        failed_path = tmp_path / "libfailed.so"
        failed_path.write_bytes(b"")
        old_map["libfailed.so"] = failed_path  # matched in old_map/new_map,
        # but absent from diff_pairs (its own compare errored) -- new_map
        # itself is irrelevant to this producer, which only ever reads
        # old_map, so it's deliberately not constructed here.

        out = tmp_path / "old.bundlefacts.json"
        write_bundle_facts_out(
            out, self._diff_pairs(), None, old_map, **self._facts_kwargs()
        )

        loaded = load_bundle_facts(out)
        assert "libfailed.so" in loaded.per_library_snapshots
        assert loaded.per_library_snapshots["libfailed.so"].elf is not None

    def test_does_not_duplicate_an_already_matched_library(
        self, tmp_path: Path
    ) -> None:
        """A key already present in ``diff_pairs`` keeps its real
        per-library snapshot -- the ``old_map`` fallback loop must never
        overwrite an already-captured, fully-dumped snapshot with a bare
        ELF-metadata-only stand-in.
        """
        from abicheck.cli_compare_release_helpers import write_bundle_facts_out

        old_map = self._old_map(tmp_path)
        out = tmp_path / "old.bundlefacts.json"
        write_bundle_facts_out(
            out, self._diff_pairs(), None, old_map, **self._facts_kwargs()
        )

        loaded = load_bundle_facts(out)
        # The real dumped snapshot (via _per_library_snapshots) carries the
        # full exports list; the removed-library fallback would not.
        assert loaded.per_library_snapshots["libcore.so"].elf is not None
        assert loaded.per_library_snapshots["libcore.so"].elf.soname == "libcore.so"

    def test_captures_filesystem_aliases(self, tmp_path: Path) -> None:
        """P2 (Codex review, fresh evidence): a real filesystem alias (a
        symlink's resolved target basename) is captured at write time --
        for every ``old_map`` entry, matched and removed alike -- so a
        later ``bundle_snapshot_from_facts()`` reconstruction can still
        resolve a ``DT_NEEDED`` edge naming that alias without touching the
        filesystem itself.
        """
        from abicheck.cli_compare_release_helpers import write_bundle_facts_out

        old_map = self._old_map(tmp_path)
        # The discovered path for "libcore.so" is a symlink named exactly
        # "libcore.so" (matching the canonical key, as a real release's
        # unversioned symlink would) pointing at the real, versioned file.
        real_target = tmp_path / "libcore.so.1.0.0"
        real_target.write_bytes(b"")
        symlink_path = tmp_path / "libcore.so"
        symlink_path.unlink()
        symlink_path.symlink_to(real_target)
        old_map["libcore.so"] = symlink_path

        out = tmp_path / "old.bundlefacts.json"
        write_bundle_facts_out(
            out, self._diff_pairs(), None, old_map, **self._facts_kwargs()
        )

        loaded = load_bundle_facts(out)
        assert "libcore.so.1.0.0" in loaded.filesystem_aliases.get("libcore.so", ())

    def test_keys_a_versioned_library_by_its_canonical_key(
        self, tmp_path: Path
    ) -> None:
        """P1 (Codex review, fresh evidence): a versioned DSO's discovered
        path (``libfoo.so.1.2``) is a different string from its canonical
        release-matching key (``libfoo.so``, ``_canonical_library_key()``)
        -- the key ``old_map``/a live ``build_bundle_snapshot()`` actually
        use. ``DiffResult.library`` (via ``AbiSnapshot.library``) always
        carries the real, on-disk basename, never the canonical key -- so
        keying the persisted facts by ``Path(diff.library).name`` instead
        of the canonical key would make a reconstructed old bundle
        disagree with a live new bundle on this library's very identity,
        reading as a false ``bundle_library_removed``/``_added`` pair for
        an unchanged library.
        """
        from abicheck.cli_compare_release_helpers import write_bundle_facts_out

        real_path = tmp_path / "libfoo.so.1.2"
        real_path.write_bytes(b"")
        old_map = {"libfoo.so": real_path}  # canonical key != real basename
        old_snapshot = AbiSnapshot(
            library="libfoo.so.1.2",  # AbiSnapshot.library is always path.name
            version="old",
            elf=_meta(soname="libfoo.so", exports=["foo_fn"]),
        )
        diff_pairs = [
            (_diff("libfoo.so.1.2", verdict=Verdict.COMPATIBLE), old_snapshot)
        ]

        out = tmp_path / "old.bundlefacts.json"
        write_bundle_facts_out(out, diff_pairs, None, old_map, **self._facts_kwargs())

        loaded = load_bundle_facts(out)
        assert set(loaded.per_library_snapshots) == {"libfoo.so"}
        assert "libfoo.so.1.2" not in loaded.per_library_snapshots

    def test_no_false_library_removed_for_a_versioned_library(
        self, tmp_path: Path
    ) -> None:
        """End-to-end proof that the canonical-key fix above actually
        closes the false-positive: comparing a reconstructed old bundle
        (from a versioned discovered path) against a live-equivalent new
        bundle (built the same way ``_run_bundle_analysis`` builds one)
        for the *same* library must not report it removed or added.
        """
        from abicheck.cli_compare_release_helpers import write_bundle_facts_out

        real_path = tmp_path / "libfoo.so.1.2"
        real_path.write_bytes(b"")
        old_map = {"libfoo.so": real_path}
        old_meta = _meta(soname="libfoo.so", exports=["foo_fn"])
        old_snapshot = AbiSnapshot(library="libfoo.so.1.2", version="old", elf=old_meta)
        diff_pairs = [
            (_diff("libfoo.so.1.2", verdict=Verdict.COMPATIBLE), old_snapshot)
        ]

        out = tmp_path / "old.bundlefacts.json"
        write_bundle_facts_out(out, diff_pairs, None, old_map, **self._facts_kwargs())
        loaded_facts = load_bundle_facts(out)

        # The "new" bundle, keyed the same canonical way a live
        # build_bundle_snapshot(dict(new_map)) call would key it.
        new_snapshot = _snapshot({"libfoo.so": old_meta})
        old_reconstructed = bundle_snapshot_from_facts(loaded_facts)

        result = compare_bundle(
            old_reconstructed,
            new_snapshot,
            [_diff("libfoo.so", verdict=Verdict.COMPATIBLE)],
        )
        removed_or_added = {
            f.kind
            for f in result.bundle_findings
            if f.kind
            in (ChangeKind.BUNDLE_LIBRARY_REMOVED, ChangeKind.BUNDLE_LIBRARY_ADDED)
        }
        assert not removed_or_added, result.bundle_findings


# ---------------------------------------------------------------------------
# Real-dump proof for a stranded (removed/matched-but-failed) library
# (Codex review, fresh evidence): the fallback must capture a real,
# functions-and-all AbiSnapshot -- the same shape the documented
# old_facts.per_library_snapshots[name] -> compare_snapshots() workflow
# needs -- not just bare ElfMetadata, which would read every declaration
# as a compatible addition instead of the real diff if this library
# reappears in a future release.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="Uses the GNU ld flag -Wl,-soname; Mach-O ld and link.exe don't "
    "accept it, and gcc/clang -shared produces a non-ELF binary on those "
    "platforms anyway. Bundle analysis itself is ELF/Linux-only per "
    "ADR-018 / ADR-023 (matches TestCompareReleaseBundleE2E's identical "
    "guard in tests/test_bundle.py).",
)
@pytest.mark.integration
class TestWriteBundleFactsOutCapturesARealSnapshot:
    def _build_tiny_so(self, tmp_path: Path) -> Path:
        src = tmp_path / "lib.c"
        src.write_text("int add(int a, int b) { return a + b; }\n")
        out = tmp_path / "libreal.so"
        res = subprocess.run(
            [
                shutil.which("gcc"),
                "-shared",
                "-fPIC",
                "-g",
                "-O0",
                str(src),
                "-o",
                str(out),
                "-Wl,-soname,libreal.so.1",
            ],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            pytest.fail(f"gcc failed: {res.stderr}")
        return out

    def test_removed_library_gets_a_full_dump_not_bare_elf_metadata(
        self, tmp_path: Path
    ) -> None:
        from abicheck.cli_compare_release_helpers import write_bundle_facts_out

        real_so = self._build_tiny_so(tmp_path)
        old_map = {"libreal.so": real_so}

        def resolve_stranded_library(old_path: Path) -> AbiSnapshot:
            from abicheck.cli_resolve import _resolve_input

            return _resolve_input(
                old_path, [], [], "old", "c++", include_dependencies=True
            )

        out = tmp_path / "old.bundlefacts.json"
        write_bundle_facts_out(
            out,
            [],  # no diff_pairs -- libreal.so is entirely "stranded"
            None,
            old_map,
            resolve_stranded_library=resolve_stranded_library,
        )

        loaded = load_bundle_facts(out)
        snap = loaded.per_library_snapshots["libreal.so"]
        # A bare ElfMetadata fallback would never populate .functions from
        # DWARF -- this is the whole point of routing through
        # service.resolve_input() instead of parse_elf_metadata() alone.
        assert any(f.name == "add" for f in snap.functions), snap.functions
