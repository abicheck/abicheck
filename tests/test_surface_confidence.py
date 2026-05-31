# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors.
"""Surface-scope structured confidence + the ``no-provenance`` ledger reason.

Covers ADR-024 §D5.3: the dumper records a structured ``scope_fallback`` on the
snapshot when header scoping had to fall back, and the surface resolver flags a
reachability demotion made without provenance. Both are disclosed (confidence +
notes) in the JSON/SARIF surface ledger so "demote + disclose" stays auditable.
"""
from __future__ import annotations

import json

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    Visibility,
)
from abicheck.reporter import to_json
from abicheck.sarif import to_sarif
from abicheck.surface import (
    REASON_NO_PROVENANCE,
    REASON_NON_PUBLIC_TYPE,
    classify_change_surface,
    compute_public_surface,
    surface_scope_confidence,
)


def _fn(name, ret="void", params=(), vis=Visibility.PUBLIC, origin=ScopeOrigin.UNKNOWN):
    return Function(
        name=name,
        mangled=f"_Z{len(name)}{name}",
        return_type=ret,
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=vis,
        origin=origin,
    )


def _rec(name, size=64, origin=ScopeOrigin.UNKNOWN):
    return RecordType(name=name, kind="struct", size_bits=size, origin=origin)


# ── scope_fallback → notes / confidence ──────────────────────────────────────


class TestScopeFallbackConfidence:
    def test_mangling_fallback_recorded(self):
        old = AbiSnapshot(library="l.dll", version="1", scope_fallback="mangling-fallback")
        new = AbiSnapshot(library="l.dll", version="2", scope_fallback="mangling-fallback")
        conf, notes = surface_scope_confidence(old, new, scope_enabled=True)
        assert conf == "reduced"
        assert notes == ["mangling-fallback"]

    def test_castxml_unavailable_recorded(self):
        old = AbiSnapshot(library="l.dll", version="1", scope_fallback="castxml-unavailable")
        new = AbiSnapshot(library="l.dll", version="2")
        conf, notes = surface_scope_confidence(old, new, scope_enabled=False)
        assert conf == "reduced"
        assert notes == ["castxml-unavailable"]

    def test_clean_run_is_high_confidence(self):
        old = AbiSnapshot(library="l", version="1", functions=[_fn("api", origin=ScopeOrigin.PUBLIC_HEADER)])
        new = AbiSnapshot(library="l", version="2", functions=[_fn("api", origin=ScopeOrigin.PUBLIC_HEADER)])
        conf, notes = surface_scope_confidence(old, new, scope_enabled=True)
        assert conf == "high"
        assert notes == []

    def test_no_provenance_note_when_surface_lacks_origins(self):
        # Resolvable surface (a PUBLIC symbol exists) but every origin is UNKNOWN
        # → the resolution is reachability-only; disclose reduced confidence.
        old = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        new = AbiSnapshot(library="l", version="2", functions=[_fn("api")])
        # Force resolvable surface: a PUBLIC function with a non-elf-only mode.
        conf, notes = surface_scope_confidence(old, new, scope_enabled=True)
        assert conf == "reduced"
        assert notes == [REASON_NO_PROVENANCE]

    def test_notes_deduplicated_and_ordered(self):
        old = AbiSnapshot(library="l", version="1", scope_fallback="mangling-fallback")
        new = AbiSnapshot(library="l", version="2", scope_fallback="mangling-fallback")
        _, notes = surface_scope_confidence(old, new, scope_enabled=False)
        assert notes == ["mangling-fallback"]  # both sides collapse to one


# ── no-provenance reason on a reachability demotion ───────────────────────────


class TestNoProvenanceReason:
    def test_reachability_demotion_with_provenance_present(self):
        # One type carries provenance (public header); an unreferenced type with
        # no origin is demoted by reachability → no-provenance (not plain
        # non-public-type), disclosing the demotion was not provenance-confirmed.
        snap = AbiSnapshot(
            library="l", version="1",
            functions=[_fn("api", ret="Public *", origin=ScopeOrigin.PUBLIC_HEADER)],
            types=[
                _rec("Public", origin=ScopeOrigin.PUBLIC_HEADER),
                _rec("Orphan", origin=ScopeOrigin.UNKNOWN),
            ],
        )
        s = compute_public_surface(snap)
        assert s.has_provenance is True
        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Orphan", description="")
        assert classify_change_surface(c, s, s) == (False, REASON_NO_PROVENANCE)

    def test_reachability_demotion_without_provenance_stays_non_public(self):
        # No provenance anywhere → keep the plain reachability reason (regression
        # guard for the pre-Phase-1 behaviour).
        snap = AbiSnapshot(
            library="l", version="1",
            functions=[_fn("api", ret="Public *")],
            types=[_rec("Public"), _rec("Orphan")],
        )
        s = compute_public_surface(snap)
        assert s.has_provenance is False
        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Orphan", description="")
        assert classify_change_surface(c, s, s) == (False, REASON_NON_PUBLIC_TYPE)


# ── anti-hiding: export-only snapshots have no trustworthy type roots ─────────


class TestExportOnlyAntiHiding:
    """A PE binary whose header scoping fell back to the export table has
    functions with ``return_type="?"`` and no params, so the reachability
    closure has no roots. Demoting a type as "unreachable" there would hide a
    real public ABI break (ADR-024 §D5.2 / Codex P1). The classifier must keep
    such type findings unless provenance *confirms* they are private/system.
    """

    def _export_only_fn(self, name):
        # Mirrors service._dump_pe export-table functions.
        return Function(name=name, mangled=name, return_type="?", visibility=Visibility.PUBLIC)

    def test_public_header_type_not_hidden_on_fallback(self):
        # A PUBLIC_HEADER PDB type whose layout changed must stay in surface
        # even though no typed function reaches it.
        old = AbiSnapshot(
            library="l.dll", version="1",
            functions=[self._export_only_fn("Api")],
            types=[_rec("Widget", size=64, origin=ScopeOrigin.PUBLIC_HEADER)],
        )
        new = AbiSnapshot(
            library="l.dll", version="2",
            functions=[self._export_only_fn("Api")],
            types=[_rec("Widget", size=128, origin=ScopeOrigin.PUBLIC_HEADER)],
        )
        s_old, s_new = compute_public_surface(old), compute_public_surface(new)
        assert s_old.has_typed_roots is False
        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Widget", description="")
        # Kept in surface — NOT demoted as non-public-type.
        assert classify_change_surface(c, s_old, s_new) == (True, None)

    def test_private_header_type_still_demoted_on_fallback(self):
        # Confident provenance still demotes — anti-hiding does not block a
        # genuinely private type.
        old = AbiSnapshot(
            library="l.dll", version="1",
            functions=[self._export_only_fn("Api")],
            types=[_rec("Internal", size=64, origin=ScopeOrigin.PRIVATE_HEADER)],
        )
        new = AbiSnapshot(
            library="l.dll", version="2",
            functions=[self._export_only_fn("Api")],
            types=[_rec("Internal", size=128, origin=ScopeOrigin.PRIVATE_HEADER)],
        )
        s_old, s_new = compute_public_surface(old), compute_public_surface(new)
        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Internal", description="")
        in_surf, reason = classify_change_surface(c, s_old, s_new)
        assert in_surf is False
        assert reason == "private-header"

    def test_typed_roots_still_demote_unreachable_type(self):
        # Regression guard: with real typed roots, reachability demotion is
        # still active (this is the normal castxml/ELF path).
        old = AbiSnapshot(
            library="l", version="1",
            functions=[_fn("api", ret="Public *")],
            types=[_rec("Public"), _rec("Unreached")],
        )
        s = compute_public_surface(old)
        assert s.has_typed_roots is True
        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Unreached", description="")
        in_surf, _ = classify_change_surface(c, s, s)
        assert in_surf is False


# ── ledger disclosure (JSON + SARIF) ──────────────────────────────────────────


class TestLedgerConfidenceDisclosure:
    def _result_with_fallback(self):
        old = AbiSnapshot(
            library="l.dll", version="1",
            functions=[_fn("api", ret="Public *", origin=ScopeOrigin.PUBLIC_HEADER)],
            types=[_rec("Public", size=64, origin=ScopeOrigin.PUBLIC_HEADER)],
            scope_fallback="mangling-fallback",
        )
        new = AbiSnapshot(
            library="l.dll", version="2",
            functions=[_fn("api", ret="Public *", origin=ScopeOrigin.PUBLIC_HEADER)],
            types=[_rec("Public", size=128, origin=ScopeOrigin.PUBLIC_HEADER)],
            scope_fallback="mangling-fallback",
        )
        return compare(old, new, scope_to_public_surface=True)

    def test_json_ledger_includes_confidence_and_notes(self):
        d = json.loads(to_json(self._result_with_fallback()))
        ledger = d["surface_scope"]
        assert ledger["confidence"] == "reduced"
        assert "mangling-fallback" in ledger["notes"]

    def test_sarif_ledger_includes_confidence_and_notes(self):
        props = to_sarif(self._result_with_fallback())["runs"][0]["properties"]
        ledger = props["surfaceScope"]
        assert ledger["confidence"] == "reduced"
        assert "mangling-fallback" in ledger["notes"]

    def test_clean_result_high_confidence(self):
        old = AbiSnapshot(
            library="l", version="1",
            functions=[_fn("api", ret="Public *", origin=ScopeOrigin.PUBLIC_HEADER)],
            types=[_rec("Public", size=64, origin=ScopeOrigin.PUBLIC_HEADER)],
        )
        new = AbiSnapshot(
            library="l", version="2",
            functions=[_fn("api", ret="Public *", origin=ScopeOrigin.PUBLIC_HEADER)],
            types=[_rec("Public", size=64, origin=ScopeOrigin.PUBLIC_HEADER)],
        )
        d = json.loads(to_json(compare(old, new, scope_to_public_surface=True)))
        assert d["surface_scope"]["confidence"] == "high"
        assert d["surface_scope"]["notes"] == []
