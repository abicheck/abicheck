# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2025 abicheck contributors
"""ADR-065 S2, second review round: the bundle graph is scoped by the
acquisition record, an unsupported NEW artifact is a recorded member in the
stored/live driver, and the ``run_outcome`` block is versioned for the
``scope`` axis.

Split out of ``test_release_scope_completeness.py`` (which is near the
architecture gate's test-file cap); its fixture helpers are reused.

Bug class: an unmatched member read as a *removed provider* by a detector
the acquisition record does not govern. ``removed_keys`` learned D2's
reading (a removal must be proven) in the first S2 slice, but the
cross-library bundle detectors (``BUNDLE_LIBRARY_REMOVED``, intra-bundle
dependency removal) still received the raw ``old_map``/``new_map`` -- so a
partial local build under ``warn``, or a narrowed current-artifact
comparison, could exit ``4`` on a provider that was merely unchecked. The
invariant is stated over generated records
(:class:`TestBundleAnalysisMembersProperties`) against an oracle derived
from the *evidence* (presence on both sides, or the lacking side's proof),
not from the record's own ``proven_*`` properties, and pinned end to end
through the stored/stored CLI path in both directions (unproven: no
finding; proven: the finding stays).
"""

from __future__ import annotations

from json import dumps as json_dumps
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st
from test_release_scope_completeness import (
    _facts_file,
    _invoke_json,
    _maps,
    _results,
    _write,
    _write_stored_package,
)

from abicheck.elf_metadata import ElfImport, ElfMetadata, ElfSymbol
from abicheck.errors import IncompatibleSnapshotSchemaError, UnsupportedArtifactError
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.scope_acquisition import (
    AcquisitionState,
    InventoryCompleteness,
    SideInventory,
)
from abicheck.policy.outcome import (
    RUN_OUTCOME_SCHEMA_VERSION,
    OperationalStatus,
    PolicyGateDecision,
    RunOutcome,
    ScopeCompleteness,
    TargetLifecycle,
)
from abicheck.workflows.release_scope import (
    ReleaseInventoryEvidence,
    build_release_scope_record,
    build_stored_baseline_scope_record,
    bundle_analysis_members,
    restrict_bundle_facts,
    scoped_bundle_maps,
)


def _lib(
    name: str,
    *,
    exports: tuple[str, ...] = (),
    needed: tuple[str, ...] = (),
    imports: tuple[str, ...] = (),
) -> AbiSnapshot:
    """An ELF-bearing snapshot the bundle graph can read: *exports* are
    defined dynamic symbols, *imports* undefined ones, *needed* DT_NEEDED."""
    return AbiSnapshot(
        library=name,
        version="1",
        elf=ElfMetadata(
            soname=name,
            needed=list(needed),
            symbols=[ElfSymbol(name=s, visibility="default") for s in exports],
            imports=[ElfImport(name=s) for s in imports],
        ),
        functions=[
            Function(name=s, mangled=s, return_type="int", visibility=Visibility.PUBLIC)
            for s in exports
        ],
    )


def _provider_and_consumer() -> dict[str, AbiSnapshot]:
    return {
        "libcore.so": _lib("libcore.so", exports=("core_mul",)),
        "libalgo.so": _lib("libalgo.so", needed=("libcore.so",), imports=("core_mul",)),
    }


def _removal_findings(doc: dict[str, object]) -> list[str]:
    findings = doc.get("bundle_findings") or []
    assert isinstance(findings, list)
    return [
        str(f["provider_library"])
        for f in findings
        if isinstance(f, dict) and f.get("kind") == "bundle_library_removed"
    ]


# ---------------------------------------------------------------------------
# The bundle graph is scoped by the record (D2)
# ---------------------------------------------------------------------------


class TestBundleAnalysisScope:
    @pytest.mark.parametrize("policy", ["warn", "block"])
    def test_unproven_unmatched_provider_is_not_a_bundle_removal(
        self, tmp_path: Path, policy: str
    ) -> None:
        """A stored/stored comparison whose NEW capture lacks the provider a
        surviving consumer depends on: the provider is unchecked, never a
        `BUNDLE_LIBRARY_REMOVED` break -- exit 0 under `warn`, 1 under
        `block`, never 4."""
        libs = _provider_and_consumer()
        old = _facts_file(tmp_path, "old.bundlefacts.json", libs)
        new = _facts_file(
            tmp_path, "new.bundlefacts.json", {"libalgo.so": libs["libalgo.so"]}
        )
        code, doc = _invoke_json(
            "compare", str(old), str(new), "--on-incomplete-scope", policy
        )
        assert _removal_findings(doc) == []
        assert doc["verdict"] != "BREAKING"
        assert doc["comparison_scope"]["unchecked"] == ["libcore.so"]
        assert doc["comparison_scope"]["proven_removed"] == []
        assert code == (1 if policy == "block" else 0)

    def test_proven_removal_keeps_the_provider_in_the_bundle_graph(
        self, tmp_path: Path
    ) -> None:
        """The same shape with NEW as a stored `ProjectSnapshot` package
        (a proven inventory): now the provider *is* removed, and the bundle
        detector still sees it and reports the broken internal dependency."""
        libs = _provider_and_consumer()
        old = tmp_path / "old_pkg"
        new = tmp_path / "new_pkg"
        _write_stored_package(old, libs)
        _write_stored_package(new, {"libalgo.so": libs["libalgo.so"]})
        code, doc = _invoke_json("compare", str(old), str(new), "-j", "1")
        removed = doc["comparison_scope"]["proven_removed"]
        assert [n.split("-")[0] for n in removed] == ["libcore.so"]
        assert doc["comparison_scope"]["completeness"] == "complete"
        assert _removal_findings(doc) == ["libcore.so"]
        assert doc["verdict"] == "BREAKING"
        assert code == 4

    def test_stored_live_scopes_the_old_bundle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.package as package
        from abicheck.bundle_side_input import compare_release_against_bundle_facts

        monkeypatch.setattr(
            package,
            "discover_shared_libraries",
            lambda d, include_private=False: sorted(Path(d).glob("*.json")),
        )
        libs = _provider_and_consumer()
        old = _facts_file(tmp_path, "old.bundlefacts.json", libs)
        new_dir = tmp_path / "new"
        _write(new_dir, "libalgo.so.json", libs["libalgo.so"])
        result = compare_release_against_bundle_facts(old, new_dir)
        assert result.scope_record is not None
        assert [m.name for m in result.scope_record.unchecked_members] == ["libcore.so"]
        assert not any(
            f.kind.value == "bundle_library_removed" for f in result.bundle_findings
        )

    def test_restrict_bundle_facts_keeps_only_the_members(self) -> None:
        from abicheck.bundle_facts import capture_bundle_facts

        facts = capture_bundle_facts(
            _provider_and_consumer(), degraded_members={"libcore.so": "boom"}
        )
        scoped = restrict_bundle_facts(facts, frozenset({"libalgo.so"}))
        assert set(scoped.per_library_snapshots) == {"libalgo.so"}
        assert set(scoped.library_filenames) <= {"libalgo.so"}
        assert scoped.degraded_members == {}
        assert scoped.artifact_type == facts.artifact_type
        # A superset restriction is the identity, not a copy.
        assert (
            restrict_bundle_facts(facts, frozenset(facts.per_library_snapshots))
            is facts
        )
        old_map, new_map = scoped_bundle_maps({"a": Path("a")}, {"b": Path("b")}, None)
        assert (old_map, new_map) == ({"a": Path("a")}, {"b": Path("b")})


_KEY = st.text(alphabet="abcdefgh", min_size=1, max_size=3)
_VERDICT = st.sampled_from(["NO_CHANGE", "BREAKING", "ERROR", "unsupported"])


class TestBundleAnalysisMembersProperties:
    @settings(max_examples=150, deadline=None)
    @given(
        st.lists(_KEY, unique=True, min_size=0, max_size=6),
        st.lists(_KEY, unique=True, min_size=0, max_size=6),
        st.booleans(),
        st.booleans(),
        st.booleans(),
        st.lists(_VERDICT, min_size=6, max_size=6),
    )
    def test_kept_iff_matched_or_proven_by_the_lacking_side(
        self,
        old_keys: list[str],
        new_keys: list[str],
        old_proven: bool,
        new_proven: bool,
        single: bool,
        verdicts: list[str],
    ) -> None:
        old_map, new_map = _maps(old_keys, new_keys)
        matched = sorted(set(old_map) & set(new_map))
        results = _results(old_map, matched, verdicts[: len(matched)])
        evidence = ReleaseInventoryEvidence(
            old=SideInventory(
                InventoryCompleteness.PROVEN
                if old_proven
                else InventoryCompleteness.UNPROVEN,
                "t",
            ),
            new=SideInventory(
                InventoryCompleteness.PROVEN
                if new_proven
                else InventoryCompleteness.UNPROVEN,
                "t",
            ),
            new_single_artifact=single,
        )
        record = build_release_scope_record(
            old_map, new_map, matched, results, evidence
        )
        kept = bundle_analysis_members(record)
        # Oracle from the evidence, not from the record's proven_* sets: a
        # member is in the bundle graph iff both sides hold it, or the side
        # lacking it has a proven inventory (so its absence is a fact).
        expected = set(matched)
        expected |= {k for k in old_keys if k not in new_map and new_proven}
        expected |= {k for k in new_keys if k not in old_map and old_proven}
        assert kept == frozenset(expected)
        # Never an out-of-scope member, never an unproven unmatched one.
        for m in record.members:
            if m.state is AcquisitionState.OUT_OF_SCOPE:
                assert m.member not in kept
        scoped_old, scoped_new = scoped_bundle_maps(old_map, new_map, record)
        assert set(scoped_old) == set(old_map) & kept
        assert set(scoped_new) == set(new_map) & kept


# ---------------------------------------------------------------------------
# Stored/live: an unsupported NEW artifact is a recorded member (D6)
# ---------------------------------------------------------------------------


class TestStoredLiveUnsupportedMember:
    @pytest.mark.parametrize(
        "exc",
        [
            UnsupportedArtifactError("Unsupported binary format: wasm"),
            IncompatibleSnapshotSchemaError("snapshot schema 99 is newer than 38"),
        ],
        ids=["unsupported-format", "newer-snapshot"],
    )
    def test_unsupported_new_artifact_is_recorded_not_raised(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exc: Exception
    ) -> None:
        import abicheck.package as package
        import abicheck.service as service
        from abicheck.bundle_side_input import compare_release_against_bundle_facts

        monkeypatch.setattr(
            package,
            "discover_shared_libraries",
            lambda d, include_private=False: sorted(Path(d).glob("*.json")),
        )
        real_resolve = service.resolve_input

        def _resolve(path: Path, **kwargs: object) -> AbiSnapshot:
            if Path(path).name.startswith("libbad"):
                raise exc
            return real_resolve(path, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(service, "resolve_input", _resolve)
        libs = {
            "libok.so": _lib("libok.so", exports=("fn",)),
            "libbad.so": _lib("libbad.so"),
        }
        old = _facts_file(tmp_path, "old.bundlefacts.json", libs)
        new_dir = tmp_path / "new"
        for name, snap in libs.items():
            _write(new_dir, f"{name}.json", snap)
        result = compare_release_against_bundle_facts(old, new_dir)
        assert [d.library for d in result.per_library] == ["libok.so"]
        record = result.scope_record
        assert record is not None
        by_key = {m.member: m for m in record.members}
        assert by_key["libbad.so"].state is AcquisitionState.UNSUPPORTED
        assert str(exc) in by_key["libbad.so"].reason
        assert [m.name for m in record.unchecked_members] == ["libbad.so"]
        assert record.is_incomplete
        assert any(
            "libbad.so" in m and "unsupported" in m for m in result.analysis_errors
        )

    def test_every_member_unsupported_is_no_comparison_completed_on_the_cli(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Stored OLD, live NEW, every matched NEW artifact unsupported: the
        dispatcher's "nothing matched" usage error must not fire -- the
        members *did* match -- and the report carries the `unsupported`
        members and D7's `no_comparison_completed` outcome (Codex review)."""
        import abicheck.package as package
        import abicheck.service as service

        monkeypatch.setattr(
            package,
            "discover_shared_libraries",
            lambda d, include_private=False: sorted(Path(d).glob("*.json")),
        )

        def _resolve(path: Path, **kwargs: object) -> AbiSnapshot:
            raise UnsupportedArtifactError("Unsupported binary format: wasm")

        monkeypatch.setattr(service, "resolve_input", _resolve)
        libs = {"libbad.so": _lib("libbad.so")}
        old = _facts_file(tmp_path, "old.bundlefacts.json", libs)
        new_dir = tmp_path / "new"
        _write(new_dir, "libbad.so.json", libs["libbad.so"])
        code, doc = _invoke_json("compare", str(old), str(new_dir))
        assert code == 1
        assert doc["run_outcome"]["operational"] == "no_comparison_completed"
        assert doc["run_outcome"]["scope"] == "incomplete"
        assert doc["comparison_scope"]["no_comparison_completed"] is True
        assert doc["comparison_scope"]["unchecked"] == ["libbad.so"]
        assert doc["comparison_scope"]["counts"]["unsupported"] == 1

    def test_builder_records_unsupported_between_degraded_and_compared(self) -> None:
        record = build_stored_baseline_scope_record(
            ["a", "b", "c", "d"],
            ["a", "b", "c"],
            compared=["a"],
            degraded={"b": "boom"},
            unsupported={"c": "wasm"},
            old_provenance="t",
            new_provenance="t",
        )
        states = {m.member: m.state for m in record.members}
        assert states == {
            "a": AcquisitionState.AVAILABLE,
            "b": AcquisitionState.FAILED,
            "c": AcquisitionState.UNSUPPORTED,
            "d": AcquisitionState.NOT_SUPPLIED,
        }
        assert record.counts()["unsupported"] == 1


# ---------------------------------------------------------------------------
# run_outcome schema version
# ---------------------------------------------------------------------------


class TestRunOutcomeSchemaVersion:
    def test_scope_axis_is_a_versioned_shape(self) -> None:
        assert RUN_OUTCOME_SCHEMA_VERSION == "1.1"
        block = RunOutcome(
            compatibility=None,
            assurance=None,
            gate=PolicyGateDecision.NONE,
            operational=OperationalStatus.NO_COMPARISON_COMPLETED,
            lifecycle=TargetLifecycle.EXISTING,
            scope=ScopeCompleteness.INCOMPLETE,
        ).to_dict()
        assert block["schema_version"] == "1.1"
        assert block["scope"] == "incomplete"
        parsed = RunOutcome.from_dict(block)
        assert parsed is not None and parsed.scope is ScopeCompleteness.INCOMPLETE

    def test_a_1_0_block_without_scope_still_reads_complete(self) -> None:
        legacy = {
            "schema_version": "1.0",
            "compatibility": "NO_CHANGE",
            "assurance": None,
            "gate": "none",
            "operational": "none",
            "lifecycle": "existing",
        }
        parsed = RunOutcome.from_dict(legacy)
        assert parsed is not None
        assert parsed.scope is ScopeCompleteness.COMPLETE


# ---------------------------------------------------------------------------
# Third review round: the marker is version-gated on read; a stored
# ProjectSnapshot package's marker reaches the live fan-out (D8)
# ---------------------------------------------------------------------------


def _degraded_facts_dict() -> dict[str, object]:
    from abicheck.bundle_facts import capture_bundle_facts
    from abicheck.bundle_facts_serialization import bundle_facts_to_dict

    facts = capture_bundle_facts(
        {"liba.so": AbiSnapshot(library="liba.so", version="")},
        degraded_members={"liba.so": "ELF-only: boom"},
    )
    return dict(bundle_facts_to_dict(facts))


class TestDegradedMarkerVersionGate:
    """Every reader refuses a non-empty marker under a pre-marker version:
    such a document would still open in a pre-S2 reader (which ignores the
    key), the exact failure the writer-side bump exists to prevent."""

    @pytest.mark.parametrize("version", [1, 2])
    def test_json_reader_rejects_the_marker_below_version_3(self, version: int) -> None:
        from abicheck.bundle_facts_serialization import bundle_facts_from_dict

        d = _degraded_facts_dict()
        assert d["schema_version"] == 3
        d["schema_version"] = version
        if version == 1:
            del d["artifact_type"]  # v1 predates the marker key too
        with pytest.raises(ValueError, match="degraded_members.*schema_version 3"):
            bundle_facts_from_dict(d)
        # And the writer's own stamp reads back.
        assert bundle_facts_from_dict(_degraded_facts_dict()).degraded_members == {
            "liba.so": "ELF-only: boom"
        }

    def test_archive_reader_rejects_the_marker_below_version_3(
        self, tmp_path: Path
    ) -> None:
        from abicheck.bundle_facts import BUNDLE_ARCHIVE_ARTIFACT_TYPE
        from abicheck.serialization import load_bundle_facts
        from abicheck.storage.bundle_archive import BundleArchiveWriter

        out = tmp_path / "old.bundlefacts.archive.zip"
        with BundleArchiveWriter(out) as writer:
            writer.write_manifest(
                {
                    "artifact_type": BUNDLE_ARCHIVE_ARTIFACT_TYPE,
                    "schema_version": 1,
                    "bundle_facts_schema_version": 2,
                    "library_blobs": {},
                    "degraded_members": {"liba.so": "ELF-only: boom"},
                }
            )
        with pytest.raises(ValueError, match="degraded_members.*schema_version 3"):
            load_bundle_facts(out, format="archive")

    def test_project_snapshot_import_rejects_the_marker_below_version_3(self) -> None:
        from abicheck.serialization import SCHEMA_VERSION
        from abicheck.storage.import_bundle_facts import import_bundle_facts
        from abicheck.storage.package import InMemoryObjectStore

        def _import(doc: dict[str, object]) -> object:
            return import_bundle_facts(
                doc,
                store=InMemoryObjectStore(),
                max_known_schema_version=SCHEMA_VERSION,
            )

        d = _degraded_facts_dict()
        d["schema_version"] = 2
        with pytest.raises(ValueError, match="degraded_members.*schema_version 3"):
            _import(d)
        assert _import(_degraded_facts_dict()) is not None

    def test_composition_section_v1_rejects_the_marker(self) -> None:
        from abicheck.storage.dto import (
            BUNDLE_COMPOSITION_SECTION_KIND,
            SectionDTO,
            bundle_composition_from_dto,
        )

        forged = SectionDTO(
            section_kind=BUNDLE_COMPOSITION_SECTION_KIND,
            section_schema_version=1,
            payload={
                "variant_fingerprint": "x",
                "manifest": None,
                "filesystem_aliases": {},
                "library_filenames": {},
                "degraded_members": {"a": "why"},
            },
        )
        with pytest.raises(ValueError, match="degraded_members"):
            bundle_composition_from_dto(forged)


class TestStoredPackageDegradedMember:
    """A ProjectSnapshot package preserves the marker; the live fan-out
    must read it back through package resolution and record the member
    `failed` rather than diffing an empty stand-in as evidence."""

    @pytest.mark.parametrize("degraded_side", ["old", "new"])
    @pytest.mark.parametrize("policy", ["warn", "block"])
    def test_marked_member_is_failed_not_compared(
        self, tmp_path: Path, degraded_side: str, policy: str
    ) -> None:
        healthy = {
            "libfoo.so": _lib("libfoo.so", exports=("foo",)),
            "libbar.so": _lib("libbar.so", exports=("bar",)),
        }
        # The degraded side holds an *empty* ELF-only stand-in for libfoo:
        # compared as evidence it would read as `func_removed`/BREAKING.
        degraded = {**healthy, "libfoo.so": _lib("libfoo.so")}
        old, new = tmp_path / "old_pkg", tmp_path / "new_pkg"
        marker = {"libfoo.so": "dump failed: boom"}
        if degraded_side == "old":
            _write_stored_package(old, degraded, degraded=marker)
            _write_stored_package(new, healthy)
        else:
            _write_stored_package(old, healthy)
            _write_stored_package(new, degraded, degraded=marker)
        code, doc = _invoke_json(
            "compare", str(old), str(new), "-j", "1", "--on-incomplete-scope", policy
        )
        by_name = {lib["library"].split("-")[0]: lib for lib in doc["libraries"]}
        assert by_name["libfoo.so"]["verdict"] == "failed"
        assert degraded_side.upper() in by_name["libfoo.so"]["reason"]
        assert by_name["libbar.so"]["verdict"] == "NO_CHANGE"
        assert "func_removed" not in json_dumps(doc)
        assert doc["verdict"] != "BREAKING"
        scope = doc["comparison_scope"]
        assert scope["completeness"] == "incomplete"
        assert [n.split("-")[0] for n in scope["unchecked"]] == ["libfoo.so"]
        assert scope["counts"]["failed"] == 1
        assert doc["run_outcome"]["scope"] == "incomplete"
        assert code == (1 if policy == "block" else 0)

    def test_package_degraded_members_are_keyed_like_the_release_map(
        self, tmp_path: Path
    ) -> None:
        from abicheck.workflows.release_package import (
            resolve_release_package_degraded_members,
            resolve_release_package_map,
        )

        root = tmp_path / "pkg"
        _write_stored_package(
            root,
            {"libfoo.so": _lib("libfoo.so"), "libbar.so": _lib("libbar.so")},
            degraded={"libfoo.so": "boom"},
        )
        degraded = resolve_release_package_degraded_members(root, variant_id=None)
        released = resolve_release_package_map(
            root, variant_id=None, dest_root=tmp_path / "materialized"
        )
        assert set(degraded) <= set(released)
        assert list(degraded.values()) == ["boom"]
