"""Readers migrated onto the I4 edge query (evidence-entity-model Phase 4).

Each test pins one documented fix: a reader that used to read "no edge" or
"not in the export table" as absence without checking that the producer
covered the scope. The fixture in every case is the incomplete-evidence one
-- an export table that was never read (a default ``ElfMetadata()``: no
symbols, no ``machine``), a snapshot with no header AST, a narrowed call
pass -- and the expectation is stated by hand: no negative conclusion.
Each has a sibling with the evidence complete, so the reader is shown to
still conclude when it may.
"""

from __future__ import annotations

from abicheck.buildsource.source_graph_findings import (
    _DEPENDENCY_EDGE_FAMILIES,
    _HEADER_FULL_VISIBILITY_KINDS,
    _HEADER_PASS_ALIAS,
    _call_reachability_findings,
)
from abicheck.checker import compare
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.graph_facts import GraphEdge, GraphNode
from abicheck.model.source_graph import SourceGraphSummary

UNREAD = ElfMetadata  # a default block: no symbols, no machine


def _read(*names):
    return ElfMetadata(symbols=[ElfSymbol(name=n) for n in names], machine="EM_X86_64")


def _fn(name="f", mangled="_Z1fv", **kw):
    return Function(
        name=name, mangled=mangled, return_type="void",
        visibility=Visibility.PUBLIC, source_header="api.h", **kw,
    )  # fmt: skip


def _snap(functions=(), elf=None, **kw):
    return AbiSnapshot(
        library="libx.so", version="1", functions=list(functions), elf=elf,
        from_headers=True, **kw,
    )  # fmt: skip


# -- depth projection ---------------------------------------------------------


class TestDepthProjection:
    def _project(self, elf):
        from abicheck.policy.depth_projection import project_snapshot_to_depth

        return project_snapshot_to_depth(_snap([_fn()], elf=elf), "binary")

    def test_unread_table_does_not_strip_every_declaration(self):
        assert [f.mangled for f in self._project(UNREAD()).functions] == ["_Z1fv"]

    def test_read_table_still_strips_an_unexported_declaration(self):
        assert self._project(_read("other")).functions == []


# -- ELF deleted fallback -----------------------------------------------------


def test_unread_new_table_is_no_elf_fallback_deletion():
    from abicheck.diff_platform import _diff_elf_deleted_fallback

    old = _snap([_fn()], elf=_read("_Z1fv"))
    assert _diff_elf_deleted_fallback(old, _snap([_fn()], elf=UNREAD())) == []
    kinds = [
        c.kind for c in _diff_elf_deleted_fallback(old, _snap([_fn()], elf=_read()))
    ]
    assert kinds == [ChangeKind.FUNC_DELETED_ELF_FALLBACK]


# -- DWARF-deleted suppression ------------------------------------------------


class TestDwarfDeletedSuppression:
    """A DWARF ``= delete`` member is suppressed as "genuinely internal" only
    when it is proven unexported on *both* sides."""

    def _kinds(self, old_elf):
        old = _snap([_fn()], elf=old_elf)
        new = _snap([_fn(is_deleted=True, deleted_from_dwarf=True)], elf=_read("other"))
        return [c.kind for c in compare(old, new).changes]

    def test_both_tables_read_and_lacking_it_suppresses(self):
        assert ChangeKind.FUNC_DELETED_DWARF not in self._kinds(_read("other"))

    def test_unread_old_table_does_not_prove_it_was_never_exported(self):
        assert ChangeKind.FUNC_DELETED_DWARF in self._kinds(UNREAD())


# -- cross-source public_not_exported -----------------------------------------


class TestPublicNotExported:
    def _run(self, elf):
        from abicheck.buildsource.cross_source_checks import (
            CrosscheckConfig,
            _check_public_not_exported,
        )
        from abicheck.model import ScopeOrigin

        fn = _fn(origin=ScopeOrigin.PUBLIC_HEADER)
        return _check_public_not_exported(_snap([fn], elf=elf), CrosscheckConfig())

    def test_unread_table_skips_instead_of_flagging_everything(self):
        out = self._run(UNREAD())
        assert (out.status, out.findings) == ("skipped", [])

    def test_read_table_still_flags_a_missing_export(self):
        out = self._run(_read("other"))
        assert [c.kind for c in out.findings] == [ChangeKind.PUBLIC_NOT_EXPORTED]


# -- exported-but-undeclared conflicts ----------------------------------------


class TestExportedButUndeclared:
    def _conflicts(self, snap):
        from abicheck.export_surface import compute_export_surface
        from abicheck.policy.contract_conflicts import detect_exported_but_undeclared

        return detect_exported_but_undeclared(
            compute_export_surface(snap), side="new", snapshot=snap
        )

    def test_no_header_ast_records_no_conflict(self):
        snap = _snap([_fn()], elf=_read("_Z1fv", "mylib_extra"))
        snap.from_headers = False
        assert self._conflicts(snap) == []

    def test_parsed_headers_record_the_conflict(self):
        snap = _snap([_fn()], elf=_read("_Z1fv", "mylib_extra"))
        assert [c.entity for c in self._conflicts(snap)] == ["mylib_extra"]


# -- undeclared-export seeding ------------------------------------------------


class TestUndeclaredExportSeeding:
    def _undeclared(self, snap):
        from abicheck.model.graph_entity_identity import snapshot_identities
        from abicheck.policy.public_surface import PublicSurface
        from abicheck.policy.public_surface_closure import _seed_undeclared_exports

        surface = PublicSurface()
        surface.has_provenance = True
        _seed_undeclared_exports(snap, surface, snapshot_identities(snap))
        return surface.undeclared_export_symbols

    def test_seeded_when_the_headers_were_parsed(self):
        snap = _snap([_fn()], elf=_read("_Z1fv", "helper", "_ZNSt6thread4joinEv"))
        # A toolchain export is never declared by the library's own headers
        # either, so it stays seeded (scope = the library's headers).
        assert self._undeclared(snap) == {"helper", "_ZNSt6thread4joinEv"}

    def test_not_seeded_without_a_header_ast(self):
        snap = _snap([_fn()], elf=_read("_Z1fv", "helper"))
        snap.from_headers = False
        assert self._undeclared(snap) == set()


# -- L5 call reachability -----------------------------------------------------


def _call_graph(callees, **flags):
    graph = SourceGraphSummary()
    for nid in ("entry", "a", "b"):
        graph.add_node(GraphNode(id=nid, kind="source_decl"))
    graph.add_node(GraphNode(id="sym", kind="binary_symbol"))
    graph.add_edge(GraphEdge(src="entry", dst="sym", kind="SOURCE_DECL_MAPS_TO_SYMBOL"))
    for dst in callees:
        graph.add_edge(GraphEdge(src="entry", dst=dst, kind="DECL_CALLS_DECL"))
    for key, passes in flags.items():
        for p in passes:
            getattr(graph, key)[p] = True
    return graph


class TestCallReachability:
    def _findings(self, old_flags, new_flags):
        old = _call_graph(["a"], **old_flags)
        new = _call_graph(["a", "b"], **new_flags)
        return _call_reachability_findings(old, new, {}, "")

    def test_full_passes_report_the_change(self):
        full = {"extractor_passes": ["call_graph"]}
        assert len(self._findings(full, full)) == 1

    def test_narrowed_or_degraded_side_reports_nothing(self):
        full = {"extractor_passes": ["call_graph"]}
        for weak in ({"narrowed_passes": ["call_graph"]}, {"degraded_passes": ["call_graph"]},
                     {"extractor_passes": ["header_call_graph"]}):  # fmt: skip
            assert self._findings(full, weak) == []
            assert self._findings(weak, full) == []

    def test_unflagged_legacy_graphs_keep_the_legacy_reading(self):
        assert len(self._findings({}, {})) == 1


def test_source_graph_findings_tables_are_the_model_owner():
    from abicheck.model.source_graph_coverage import (
        HEADER_FULL_VISIBILITY_KINDS,
        HEADER_PASS_ALIAS,
        PASS_EDGE_KINDS,
    )

    assert _HEADER_PASS_ALIAS is HEADER_PASS_ALIAS
    assert _HEADER_FULL_VISIBILITY_KINDS is HEADER_FULL_VISIBILITY_KINDS
    assert _DEPENDENCY_EDGE_FAMILIES == {
        "call_graph": PASS_EDGE_KINDS["call_graph"],
        "type_graph": PASS_EDGE_KINDS["type_graph"],
    }
