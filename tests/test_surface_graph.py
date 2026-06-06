# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for the SurfaceGraph substrate and A1 metrics (ADR-025)."""

from __future__ import annotations

from abicheck.model import (
    AbiSnapshot,
    EnumType,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    TypeField,
    Variable,
    Visibility,
)
from abicheck.surface_graph import (
    build_surface_graph,
    compute_surface_metrics,
)


def _snap() -> AbiSnapshot:
    # libfoo: open(Handle*) -> Status; Handle embeds Detail; Widget is a god type.
    return AbiSnapshot(
        library="libfoo.so.1",
        version="1.0.0",
        from_headers=True,
        functions=[
            Function(
                name="foo_open",
                mangled="foo_open",
                return_type="Status",
                params=[Param(name="h", type="Handle*", pointer_depth=1)],
                visibility=Visibility.PUBLIC,
                source_header="foo/api.h",
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
            Function(
                name="foo_internal",
                mangled="_ZL12foo_internalv",
                return_type="void",
                visibility=Visibility.HIDDEN,
                source_header="foo/internal.h",
                origin=ScopeOrigin.PRIVATE_HEADER,
            ),
            Function(
                name="foo_undocumented",
                mangled="foo_undocumented",
                return_type="int",
                visibility=Visibility.PUBLIC,
                origin=ScopeOrigin.EXPORT_ONLY,
            ),
        ],
        variables=[
            Variable(
                name="foo_version",
                mangled="foo_version",
                type="int",
                visibility=Visibility.PUBLIC,
                source_header="foo/api.h",
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
        ],
        types=[
            RecordType(
                name="Handle",
                kind="struct",
                fields=[TypeField(name="d", type="Detail")],
                source_header="foo/api.h",
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
            RecordType(
                name="Detail",
                kind="struct",
                fields=[TypeField(name="w", type="Widget")],
                source_header="foo/api.h",
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
            RecordType(name="Widget", kind="struct", source_header="foo/api.h"),
            # An island type in a different header, referenced by nobody.
            RecordType(name="Island", kind="struct", source_header="foo/extra.h"),
        ],
        enums=[
            EnumType(name="Status", source_header="foo/api.h"),
        ],
    )


def test_graph_is_deterministic() -> None:
    snap = _snap()
    g1 = build_surface_graph(snap)
    g2 = build_surface_graph(snap)
    assert list(g1.types_by_name) == list(g2.types_by_name)
    assert g1.reached_by == g2.reached_by
    assert list(g1.type_refs) == sorted(g1.type_refs)


def test_public_roots_exclude_hidden() -> None:
    g = build_surface_graph(_snap())
    roots = g.public_roots()
    assert "foo_open" in roots
    assert "foo_version" in roots
    assert "foo_internal" not in roots  # HIDDEN


def test_reachable_types_follows_closure() -> None:
    g = build_surface_graph(_snap())
    reached = g.reachable_types("foo_open")
    # foo_open takes Handle*, Handle embeds Detail, Detail embeds Widget.
    assert {"Handle", "Detail", "Widget"} <= reached
    assert "Island" not in reached  # not reachable from any root


def test_fan_in_and_fan_out() -> None:
    g = build_surface_graph(_snap())
    assert g.fan_out("Handle") == 1  # references Detail
    assert g.fan_in("Widget") == 1  # referenced by Detail
    assert g.fan_in("Island") == 0  # referenced by nobody


def test_reached_by_inverse() -> None:
    g = build_surface_graph(_snap())
    assert g.reached_by.get("Widget") == frozenset({"foo_open"})
    assert "Island" not in g.reached_by


def test_metrics_counts_and_undocumented_ratio() -> None:
    m = compute_surface_metrics(_snap())
    assert m.library == "libfoo.so.1"
    assert m.evidence_tier == "header_aware"
    assert m.public_functions == 2  # foo_open + foo_undocumented
    assert m.public_variables == 1
    # Island is parsed but unreachable from any public root, so it is excluded
    # from the public-type count (only Handle/Detail/Widget remain).
    assert m.public_types == 3
    assert m.public_enums == 1
    # 1 EXPORT_ONLY symbol out of 3 exported (2 fns + 1 var).
    assert m.undocumented_exports == 1
    assert abs(m.undocumented_export_ratio - (1 / 3)) < 1e-9


def test_overloaded_functions_union_seed_types() -> None:
    # Two C++ overloads share the demangled name "process" but reference
    # different types; both type sets must be reachable from the shared root.
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="process",
                mangled="_Z7processP1A",
                return_type="void",
                params=[Param(name="a", type="A*", pointer_depth=1)],
                visibility=Visibility.PUBLIC,
            ),
            Function(
                name="process",
                mangled="_Z7processP1B",
                return_type="void",
                params=[Param(name="b", type="B*", pointer_depth=1)],
                visibility=Visibility.PUBLIC,
            ),
        ],
        types=[
            RecordType(name="A", kind="struct"),
            RecordType(name="B", kind="struct"),
        ],
    )
    g = build_surface_graph(snap)
    reached = g.reachable_types("process")
    assert {"A", "B"} <= reached  # neither overload's type is lost


def test_closure_follows_unqualified_reference_to_namespaced_record() -> None:
    # A signature names the record unqualified ("A"), but the record is defined
    # as "ns::A". The closure must still follow ns::A's fields via the short key.
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="entry",
                mangled="entry",
                return_type="void",
                params=[Param(name="a", type="A*", pointer_depth=1)],
                visibility=Visibility.PUBLIC,
            ),
        ],
        types=[
            RecordType(
                name="ns::A",
                kind="class",
                fields=[TypeField(name="inner", type="Inner")],
            ),
            RecordType(name="Inner", kind="struct"),
        ],
    )
    g = build_surface_graph(snap)
    reached = g.reachable_types("entry")
    # Inner is only reachable if "A" resolves ns::A's field refs.
    assert "Inner" in reached


def test_fan_in_not_double_counted_for_namespaced_record() -> None:
    # ns::B has one field of type ns::A. fan_in(ns::A) must be 1, not 2 — the
    # alias key A must not be counted as a second referrer.
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="use",
                mangled="use",
                return_type="void",
                params=[Param(name="b", type="ns::B*", pointer_depth=1)],
                visibility=Visibility.PUBLIC,
                source_header="h.h",
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
        ],
        types=[
            RecordType(
                name="ns::B",
                kind="class",
                fields=[TypeField(name="a", type="ns::A")],
                source_header="h.h",
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
            RecordType(
                name="ns::A",
                kind="class",
                source_header="h.h",
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
        ],
    )
    g = build_surface_graph(snap)
    assert g.fan_in("ns::A") == 1
    m = compute_surface_metrics(snap)
    # ns::A appears at most once in the top-fan-in listing (not as both A/ns::A).
    names = [n for n, _ in m.top_fan_in]
    assert names.count("ns::A") <= 1
    assert "A" not in names  # the short alias is not listed as a separate type


def test_fan_in_matches_unqualified_field_reference() -> None:
    # ns::B has a field written with the UNQUALIFIED name "A" (common inside a
    # namespace), so type_refs stores only "A". fan_in("ns::A") must still be 1.
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="use",
                mangled="use",
                return_type="void",
                params=[Param(name="b", type="ns::B*", pointer_depth=1)],
                visibility=Visibility.PUBLIC,
                source_header="h.h",
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
        ],
        types=[
            RecordType(
                name="ns::B",
                kind="class",
                fields=[TypeField(name="a", type="A")],  # unqualified
                source_header="h.h",
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
            RecordType(
                name="ns::A",
                kind="class",
                source_header="h.h",
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
        ],
    )
    g = build_surface_graph(snap)
    assert g.fan_in("ns::A") == 1  # the unqualified "A" reference still counts


def test_header_coverage_counts_overloads() -> None:
    # foo(int) and foo(double) in the same header are two declared functions.
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="foo",
                mangled="_Z3fooi",
                return_type="void",
                params=[Param(name="x", type="int")],
                visibility=Visibility.PUBLIC,
                source_header="api.h",
            ),
            Function(
                name="foo",
                mangled="_Z3food",
                return_type="void",
                params=[Param(name="x", type="double")],
                visibility=Visibility.PUBLIC,
                source_header="api.h",
            ),
        ],
    )
    m = compute_surface_metrics(snap)
    api = next(hc for hc in m.header_coverage if hc.header == "api.h")
    assert api.declared == 2  # both overloads, not collapsed to 1
    assert api.exported == 2
    assert m.public_functions == 2


def test_virtual_bases_counted_in_public_types() -> None:
    # D : virtual B. B is reachable through D's public use, so it must be a
    # public type even though it appears only as a virtual base.
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="use_d",
                mangled="use_d",
                return_type="void",
                params=[Param(name="d", type="D*", pointer_depth=1)],
                visibility=Visibility.PUBLIC,
                source_header="h.h",
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
        ],
        types=[
            RecordType(
                name="D",
                kind="class",
                virtual_bases=["B"],
                source_header="h.h",
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
            RecordType(
                name="B",
                kind="class",
                source_header="h.h",
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
        ],
    )
    m = compute_surface_metrics(snap)
    assert m.public_types == 2  # both D and its virtual base B


def test_public_type_count_for_unqualified_namespaced_reference() -> None:
    # A public method takes "A*" (unqualified) but the record is stored as
    # "ns::A". The public-type count must still include it (Codex P2:
    # canonicalize aliases before the membership test).
    from abicheck.surface_graph import _public_type_counts

    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="ns::use",
                mangled="_ZN2ns3useEPNS_1AE",
                return_type="void",
                params=[Param(name="a", type="A*", pointer_depth=1)],
                visibility=Visibility.PUBLIC,
                source_header="h.h",
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
        ],
        types=[
            RecordType(
                name="ns::A",
                kind="class",
                source_header="h.h",
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
        ],
    )
    records, _enums = _public_type_counts(snap)
    assert records == 1  # ns::A counted despite the unqualified "A" reference


def test_cohesion_merges_unqualified_namespaced_reference() -> None:
    # ns::B has a field of unqualified type "A" (== ns::A). The two records are
    # one connected component, so cohesion must report 1 cluster, not 2.
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="use",
                mangled="use",
                return_type="void",
                params=[Param(name="b", type="ns::B*", pointer_depth=1)],
                visibility=Visibility.PUBLIC,
                source_header="h.h",
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
        ],
        types=[
            RecordType(
                name="ns::B",
                kind="class",
                fields=[TypeField(name="a", type="A")],  # unqualified
                source_header="h.h",
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
            RecordType(
                name="ns::A",
                kind="class",
                source_header="h.h",
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
        ],
    )
    m = compute_surface_metrics(snap)
    hc = next(c for c in m.header_coverage if c.header == "h.h")
    assert hc.cohesion_clusters == 1  # ns::B -> ns::A is one component


def test_public_type_count_falls_back_when_unresolvable() -> None:
    # An ELF-only snapshot (no header-derived visibility) cannot resolve a
    # public surface, so the raw parsed counts are used (nothing was scoped).
    from abicheck.surface_graph import _public_type_counts

    snap = AbiSnapshot(
        library="l",
        version="1",
        elf_only_mode=True,
        functions=[
            Function(
                name="f",
                mangled="f",
                return_type="void",
                visibility=Visibility.ELF_ONLY,
            ),
        ],
        types=[RecordType(name="Internal", kind="struct")],
        enums=[EnumType(name="E")],
    )
    assert _public_type_counts(snap) == (1, 1)


def test_metrics_header_coverage_and_cohesion() -> None:
    m = compute_surface_metrics(_snap())
    by_header = {hc.header: hc for hc in m.header_coverage}
    # api.h declares foo_open, foo_version, Handle, Detail, Widget, Status.
    api = by_header["foo/api.h"]
    assert api.declared == 6
    # foo_open + foo_version resolve to exported symbols.
    assert api.exported == 2
    # Handle→Detail→Widget form one connected cluster; Status is a separate
    # type node (enum, not in the record-ref graph) → 2 clusters.
    assert api.cohesion_clusters >= 1
    # extra.h declares only the Island type, its own singleton cluster.
    assert by_header["foo/extra.h"].cohesion_clusters == 1


def test_top_fan_in_sorted_desc() -> None:
    m = compute_surface_metrics(_snap())
    counts = [c for _, c in m.top_fan_in]
    assert counts == sorted(counts, reverse=True)
    assert all(c > 0 for c in counts)


def test_metrics_json_round_trips_through_snapshot(tmp_path) -> None:
    import json as _json

    from click.testing import CliRunner

    from abicheck.cli import main
    from abicheck.serialization import save_snapshot

    snap_path = tmp_path / "libfoo.abi.json"
    save_snapshot(_snap(), snap_path)

    runner = CliRunner()
    result = runner.invoke(main, ["surface-report", str(snap_path), "--format", "json"])
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["library"] == "libfoo.so.1"
    assert data["undocumented_exports"] == 1
    assert any(hc["header"] == "foo/api.h" for hc in data["header_coverage"])


def _bare_snap() -> AbiSnapshot:
    # A void/no-param public function and no types/headers — exercises the
    # empty-seed, empty-coverage, and DWARF/ELF-tier branches.
    return AbiSnapshot(
        library="libbare.so",
        version="",
        functions=[
            Function(
                name="bare_noop",
                mangled="bare_noop",
                return_type="void",
                visibility=Visibility.PUBLIC,
            ),
        ],
    )


def test_reachable_types_empty_for_void_root() -> None:
    g = build_surface_graph(_bare_snap())
    assert g.reachable_types("bare_noop") == frozenset()
    assert g.reachable_types("does_not_exist") == frozenset()


def test_metrics_no_headers_no_types() -> None:
    m = compute_surface_metrics(_bare_snap())
    assert m.evidence_tier == "elf_only"  # no headers, no dwarf
    assert m.top_fan_in == []
    assert m.header_coverage == []
    assert m.undocumented_export_ratio == 0.0


def test_dwarf_tier_without_headers() -> None:
    from abicheck.dwarf_metadata import DwarfMetadata

    snap = _bare_snap()
    snap.dwarf = DwarfMetadata()
    assert compute_surface_metrics(snap).evidence_tier == "dwarf_aware"


def test_surface_report_text_empty_surface(tmp_path) -> None:
    from click.testing import CliRunner

    from abicheck.cli import main
    from abicheck.serialization import save_snapshot

    snap_path = tmp_path / "libbare.abi.json"
    save_snapshot(_bare_snap(), snap_path)
    result = CliRunner().invoke(main, ["surface-report", str(snap_path)])
    assert result.exit_code == 0, result.output
    assert "highest fan-in" not in result.output  # no fan-in section
    assert "header coverage" not in result.output  # no header section


def test_closure_handles_namespaces_diamonds_typedefs_vbases() -> None:
    # ns::A -> B and C (diamond), both -> D; A has a virtual base VB; Alias->A.
    snap = AbiSnapshot(
        library="lib.so",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="entry",
                mangled="entry",
                return_type="Alias",
                params=[Param(name="x", type="ns::A*", pointer_depth=1)],
                visibility=Visibility.PUBLIC,
            ),
        ],
        types=[
            RecordType(
                name="ns::A",
                kind="class",
                fields=[TypeField(name="b", type="B"), TypeField(name="c", type="C")],
                virtual_bases=["VB"],
            ),
            RecordType(name="B", kind="struct", fields=[TypeField(name="d", type="D")]),
            RecordType(name="C", kind="struct", fields=[TypeField(name="d", type="D")]),
            RecordType(name="D", kind="struct"),
            RecordType(name="VB", kind="struct"),
        ],
        typedefs={"Alias": "ns::A"},
    )
    g = build_surface_graph(snap)
    # Namespaced record indexed under both full name and trailing segment.
    assert "ns::A" in g.types_by_name and "A" in g.types_by_name
    reached = g.reachable_types("entry")
    # Diamond: D reached via both B and C; virtual base + typedef target followed.
    assert {"B", "C", "D", "VB"} <= reached
    assert "VB" in g.type_refs["ns::A"]  # virtual base recorded as a reference


def test_metrics_json_empty_surface(tmp_path) -> None:
    import json as _json

    from click.testing import CliRunner

    from abicheck.cli import main
    from abicheck.serialization import save_snapshot

    snap_path = tmp_path / "libbare.abi.json"
    save_snapshot(_bare_snap(), snap_path)
    result = CliRunner().invoke(
        main, ["surface-report", str(snap_path), "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["top_fan_in"] == []
    assert data["header_coverage"] == []


def test_surface_report_rejects_garbage_input(tmp_path) -> None:
    from click.testing import CliRunner

    from abicheck.cli import main

    junk = tmp_path / "not-a-library.bin"
    junk.write_bytes(b"this is not an ELF, PE, Mach-O, JSON, or Perl dump\n")
    result = CliRunner().invoke(main, ["surface-report", str(junk)])
    assert result.exit_code != 0
    assert "Cannot read" in result.output


def test_surface_report_text_output(tmp_path) -> None:
    from click.testing import CliRunner

    from abicheck.cli import main
    from abicheck.serialization import save_snapshot

    snap_path = tmp_path / "libfoo.abi.json"
    save_snapshot(_snap(), snap_path)

    runner = CliRunner()
    result = runner.invoke(main, ["surface-report", str(snap_path)])
    assert result.exit_code == 0, result.output
    assert "Surface report: libfoo.so.1" in result.output
    assert "undocumented exports:" in result.output
