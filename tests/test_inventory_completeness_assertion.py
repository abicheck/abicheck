# Copyright (c) 2026 abicheck contributors
# SPDX-License-Identifier: Apache-2.0
"""ADR-065 D2: a stored baseline proves a complete inventory only by its
own capture's ``inventory_complete`` assertion, carried through every
storage form (Codex review, twenty-eighth round).

Before this, the release fan-out treated *every* stored ``ProjectSnapshot``
package as a proven inventory while the stored-baseline drivers treated
the same ``BundleFacts`` document as unproven -- so importing a document
into a package could promote an unmatched member to a proven removal
(exit 8) that the document itself never supported. The invariants:

* the assertion round-trips unchanged through the JSON document, the G40
  archive, and the ``ProjectSnapshot`` import/export, and is refused when
  malformed rather than coerced;
* one document decides its side's completeness identically whether it is
  compared directly or after import into a package;
* only the assertion proves a removal/addition -- the container type
  never does.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st
from test_release_scope_bundle import _lib
from test_release_scope_completeness import _invoke_json, _write_stored_package

from abicheck.bundle_facts import BundleFacts, capture_bundle_facts
from abicheck.bundle_facts_serialization import (
    bundle_facts_from_dict,
    bundle_facts_to_dict,
)
from abicheck.model import AbiSnapshot
from abicheck.model.scope_acquisition import InventoryCompleteness
from abicheck.workflows.release_scope import (
    build_stored_baseline_scope_record,
    release_inventory_evidence,
)


def _facts(
    libs: dict[str, AbiSnapshot],
    *,
    complete: bool,
    degraded: dict[str, str] | None = None,
) -> BundleFacts:
    return capture_bundle_facts(
        libs, inventory_complete=complete, degraded_members=degraded
    )


_LIBS = {"libfoo.so": _lib("libfoo.so", exports=("foo",))}


class TestAssertionRoundTrips:
    @pytest.mark.parametrize("complete", [True, False])
    def test_json_document(self, complete: bool) -> None:
        d = bundle_facts_to_dict(_facts(_LIBS, complete=complete))
        assert d["inventory_complete"] is complete
        assert bundle_facts_from_dict(d).inventory_complete is complete

    def test_absent_key_is_unasserted(self) -> None:
        d = bundle_facts_to_dict(_facts(_LIBS, complete=True))
        del d["inventory_complete"]
        assert bundle_facts_from_dict(d).inventory_complete is False

    @pytest.mark.parametrize("raw", [None, 1, "true", "yes", [], {}])
    def test_malformed_value_is_refused_not_coerced(self, raw: object) -> None:
        d = bundle_facts_to_dict(_facts(_LIBS, complete=True))
        d["inventory_complete"] = raw
        with pytest.raises(ValueError, match="inventory_complete"):
            bundle_facts_from_dict(d)

    @pytest.mark.parametrize("complete", [True, False])
    def test_archive(self, tmp_path: Path, complete: bool) -> None:
        from abicheck.serialization import load_bundle_facts, save_bundle_facts

        path = tmp_path / "facts.zip"
        save_bundle_facts(_facts(_LIBS, complete=complete), path, format="archive")
        assert load_bundle_facts(path).inventory_complete is complete

    @pytest.mark.parametrize("complete", [True, False])
    def test_project_snapshot_package(self, tmp_path: Path, complete: bool) -> None:
        from abicheck.bundle_facts_store import (
            read_bundle_facts_package,
            write_bundle_facts_package,
        )
        from abicheck.project_snapshot_store import (
            DirectoryObjectStore,
            write_project_manifest,
        )
        from abicheck.storage.variant_composition import (
            read_variant_composition_inventory_complete,
        )
        from abicheck.workflows.release_scope import stored_side_inventory_complete

        store = DirectoryObjectStore(tmp_path)
        manifest = write_bundle_facts_package(
            _facts(_LIBS, complete=complete), store=store, variant_id="default"
        )
        write_project_manifest(tmp_path, manifest)
        assert (
            read_bundle_facts_package(manifest, store=store).inventory_complete
            is complete
        )
        assert (
            read_variant_composition_inventory_complete(tmp_path, "default") is complete
        )
        assert stored_side_inventory_complete(tmp_path, variant_id=None) is complete

    def test_a_malformed_stored_assertion_fails_closed(self, tmp_path: Path) -> None:
        """A hand-edited composition with a non-boolean assertion is
        refused by the fan-out's read (never read as asserted, never as
        unasserted) -- the same posture the D8 marker takes."""
        from abicheck.errors import SnapshotError
        from abicheck.project_snapshot_store import (
            DirectoryObjectStore,
            read_manifest_summary,
            read_variant_ref,
        )
        from abicheck.storage.dto import BUNDLE_COMPOSITION_SECTION_KIND
        from abicheck.storage.package import variant_ref_relpath
        from abicheck.workflows.release_scope import stored_side_inventory_complete

        pkg = tmp_path / "pkg"
        _write_stored_package(pkg, _LIBS)
        (variant_id,) = read_manifest_summary(pkg).variant_ids
        ref = read_variant_ref(pkg, variant_id).sections[
            BUNDLE_COMPOSITION_SECTION_KIND
        ]
        store = DirectoryObjectStore(pkg)
        raw = dict(store.get(ref.digest))
        raw["payload"] = {**dict(raw["payload"]), "inventory_complete": "true"}
        digest = store.put(raw)
        ref_path = pkg / variant_ref_relpath(variant_id)
        doc = json.loads(ref_path.read_text(encoding="utf-8"))
        doc["sections"][BUNDLE_COMPOSITION_SECTION_KIND]["digest"] = digest
        ref_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        with pytest.raises(SnapshotError, match="inventory assertion"):
            stored_side_inventory_complete(pkg, variant_id=None)


class TestOnlyTheAssertionProves:
    """The fan-out: the same OLD library set, stored as a package with and
    without the assertion, against a live NEW directory lacking one of them.
    Only the asserted package proves the removal."""

    @pytest.mark.parametrize("asserted", [True, False])
    def test_fan_out_reads_the_assertion_not_the_container(
        self, tmp_path: Path, asserted: bool
    ) -> None:
        libs = {**_LIBS, "libgone.so": _lib("libgone.so", exports=("gone",))}
        old, new = tmp_path / "old_pkg", tmp_path / "new_pkg"
        _write_stored_package(old, libs)
        _write_stored_package(new, _LIBS, inventory_complete=asserted)
        code, doc = _invoke_json(
            "compare", str(old), str(new), "-j", "1", "--fail-on-removed-library"
        )
        scope = doc["comparison_scope"]
        assert isinstance(scope, dict)
        assert scope["new_inventory"]["completeness"] == (
            "proven" if asserted else "unproven"
        )
        assert "inventory_complete" in scope["new_inventory"]["provenance"]
        unmatched = doc["unmatched_old"]
        assert isinstance(unmatched, list) and len(unmatched) == 1
        if asserted:
            assert len(scope["proven_removed"]) == 1
            assert code == 8
        else:
            assert scope["proven_removed"] == []
            assert doc["run_outcome"]["scope"] == "incomplete"
            assert code == 0

    @pytest.mark.parametrize("asserted", [True, False])
    def test_stored_facts_document_decides_like_the_package(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, asserted: bool
    ) -> None:
        """The same document, compared directly as a stored OLD baseline
        against a live NEW directory (the stored/live driver the CLI
        dispatches to): OLD's proof follows its own assertion, so a
        NEW-only member is a proven addition iff asserted -- the identical
        reading the package form above gives."""
        import abicheck.package as package
        from abicheck.bundle_side_input import compare_release_against_bundle_facts
        from abicheck.serialization import save_bundle_facts, save_snapshot

        monkeypatch.setattr(
            package,
            "discover_shared_libraries",
            lambda d, include_private=False: sorted(Path(d).glob("*.json")),
        )
        old = tmp_path / "old.bundlefacts.json"
        save_bundle_facts(_facts(_LIBS, complete=asserted), old)
        new = tmp_path / "new"
        new.mkdir()
        save_snapshot(_LIBS["libfoo.so"], new / "libfoo.so.json")
        save_snapshot(_lib("libnew.so", exports=("n",)), new / "libnew.so.json")
        record = compare_release_against_bundle_facts(old, new).scope_record
        assert record is not None
        assert (
            record.old_inventory.completeness is InventoryCompleteness.PROVEN
        ) is asserted
        assert "inventory" in record.old_inventory.provenance
        assert [m.member for m in record.proven_added_members] == (
            ["libnew.so"] if asserted else []
        )
        assert [m.member for m in record.members if not m.old_present] == ["libnew.so"]


_KEYS = st.sets(st.sampled_from(["a.so", "b.so", "c.so"]))


class TestProofFollowsTheAssertionEverywhere:
    """Property over both builders: for any side, ``PROVEN`` iff that side
    is stored *and* asserted (and, for the fan-out, not withheld by an
    unclassified member); the container type alone never proves."""

    @settings(max_examples=80, deadline=None)
    @given(
        old_stored=st.booleans(),
        new_stored=st.booleans(),
        old_complete=st.booleans(),
        new_complete=st.booleans(),
        unclassified=_KEYS,
    )
    def test_release_inventory_evidence(
        self,
        old_stored: bool,
        new_stored: bool,
        old_complete: bool,
        new_complete: bool,
        unclassified: set[str],
    ) -> None:
        evidence = release_inventory_evidence(
            old_stored=old_stored,
            new_stored=new_stored,
            old_complete=old_complete,
            new_complete=new_complete,
            old_unclassified={k: "x" for k in unclassified},
        )
        expect_old = old_stored and old_complete and not unclassified
        expect_new = new_stored and new_complete
        assert (evidence.old.completeness is InventoryCompleteness.PROVEN) is expect_old
        assert (evidence.new.completeness is InventoryCompleteness.PROVEN) is expect_new

    @settings(max_examples=60, deadline=None)
    @given(old=_KEYS, new=_KEYS, old_complete=st.booleans(), new_complete=st.booleans())
    def test_stored_baseline_record(
        self, old: set[str], new: set[str], old_complete: bool, new_complete: bool
    ) -> None:
        record = build_stored_baseline_scope_record(
            old,
            new,
            compared=sorted(old & new),
            degraded={},
            old_provenance="unasserted",
            new_provenance="unasserted",
            old_complete=old_complete,
            new_complete=new_complete,
        )
        assert (
            record.old_inventory.completeness is InventoryCompleteness.PROVEN
        ) is old_complete
        assert (
            record.new_inventory.completeness is InventoryCompleteness.PROVEN
        ) is new_complete
        removed = {m.member for m in record.proven_removed_members}
        added = {m.member for m in record.proven_added_members}
        assert removed == ((old - new) if new_complete else set())
        assert added == ((new - old) if old_complete else set())
