"""Per-format dependency facts a release records, and how the graph draws them.

Oracle: the expected facts are written out by hand from each synthetic
snapshot's own fields, so a reader that drops, invents or mis-keys a name
cannot agree with itself. Graph edges are asserted as explicit
``(source, target, sides)`` triples.
"""

from __future__ import annotations

from abicheck.model import AbiSnapshot
from abicheck.model.elf_facts import ElfMetadata
from abicheck.model.macho_facts import MachoMetadata
from abicheck.model.pe_facts import PeMetadata
from abicheck.report.release_dependency_graph import (
    DEPS_NOT_EVALUATED,
    DEPS_PARTIAL,
    DEPS_RECORDED,
    compute_release_dependency_graph,
)
from abicheck.workflows.release_dependency_facts import member_dependency_facts


def _pe(imports: dict, delay: dict | None = None) -> AbiSnapshot:
    return AbiSnapshot(
        library="x.dll",
        version="1",
        pe=PeMetadata(imports=imports, delay_imports=delay),
    )


def _macho(
    install_name: str,
    deps: list[str],
    weak: list[str] | None,
    reexports: list[str] | None = None,
) -> AbiSnapshot:
    return AbiSnapshot(
        library="x.dylib",
        version="1",
        macho=MachoMetadata(
            install_name=install_name,
            dependent_libs=deps,
            weak_dependent_libs=weak,
            reexported_libs=reexports or [],
        ),
    )


# ── the recording half ─────────────────────────────────────────────────────


def test_pe_side_records_import_and_delay_load_dll_names() -> None:
    side = member_dependency_facts(
        _pe({"KERNEL32.dll": ["Sleep"], "foo.dll": ["f"]}, {"ole32.dll": ["Co"]}),
        None,
    )
    assert side == {
        "old": {
            "format": "pe",
            "soname": None,
            "needed": ["KERNEL32.dll", "foo.dll", "ole32.dll"],
        }
    }


def test_pe_side_from_legacy_snapshot_marks_delay_imports_incomplete() -> None:
    side = member_dependency_facts(_pe({"KERNEL32.dll": []}), None)
    assert side == {
        "old": {
            "format": "pe",
            "soname": None,
            "needed": ["KERNEL32.dll"],
            "incomplete": ["delay_imports"],
        }
    }


def test_macho_side_records_hard_weak_and_reexported_loads_in_order() -> None:
    side = member_dependency_facts(
        None,
        _macho(
            "@rpath/libtop.dylib",
            deps=["/usr/lib/libSystem.B.dylib"],
            weak=["@rpath/libplug.dylib"],
            reexports=["@rpath/libre.dylib"],
        ),
    )
    assert side == {
        "new": {
            "format": "macho",
            "soname": "@rpath/libtop.dylib",
            "needed": [
                "/usr/lib/libSystem.B.dylib",
                "@rpath/libplug.dylib",
                "@rpath/libre.dylib",
            ],
        }
    }


def test_macho_legacy_snapshot_marks_weak_loads_incomplete() -> None:
    side = member_dependency_facts(
        _macho("@rpath/libtop.dylib", deps=["/usr/lib/libSystem.B.dylib"], weak=None),
        None,
    )
    assert side["old"] == {
        "format": "macho",
        "soname": "@rpath/libtop.dylib",
        "needed": ["/usr/lib/libSystem.B.dylib"],
        "incomplete": ["weak_dependent_libs"],
    }


def test_a_side_with_no_format_metadata_is_not_evaluated_not_empty() -> None:
    header_only = AbiSnapshot(library="h", version="1")
    block = member_dependency_facts(header_only, header_only)
    assert block == {
        "old": {
            "state": "not_evaluated",
            "reason": "no ELF, PE/COFF or Mach-O metadata on this side",
        },
        "new": {
            "state": "not_evaluated",
            "reason": "no ELF, PE/COFF or Mach-O metadata on this side",
        },
    }


def test_elf_side_keeps_its_soname_and_dt_needed_verbatim() -> None:
    elf = AbiSnapshot(
        library="libc.so",
        version="1",
        elf=ElfMetadata(soname="libc.so.6", needed=["ld-linux.so.2", "libc.so.6"]),
    )
    assert member_dependency_facts(elf, None) == {
        "old": {
            "format": "elf",
            "soname": "libc.so.6",
            "needed": ["ld-linux.so.2", "libc.so.6"],
        }
    }


# ── the projection half ────────────────────────────────────────────────────


def _doc(*members: dict) -> dict:
    return {
        "libraries": list(members),
        "comparison_scope": {"members": []},
    }


def _member(name: str, dependencies: dict, verdict: str = "COMPATIBLE") -> dict:
    return {"library": name, "verdict": verdict, "dependencies": dependencies}


def _edges(doc: dict) -> set[tuple[str, str, str]]:
    graph = compute_release_dependency_graph(doc)
    return {(e.source, e.target, e.sides) for e in graph.edges}


def test_pe_import_names_match_member_files_case_insensitively() -> None:
    doc = _doc(
        _member(
            "app.dll",
            {
                "old": {
                    "format": "pe",
                    "soname": None,
                    "needed": ["FOO.DLL", "KERNEL32.dll"],
                }
            },
        ),
        _member(
            "foo.dll",
            {"old": {"format": "pe", "soname": None, "needed": []}},
        ),
    )
    assert _edges(doc) == {
        ("app.dll", "foo.dll", "old"),
        ("app.dll", "KERNEL32.dll", "old"),
    }


def test_macho_install_name_matches_member_by_basename() -> None:
    doc = _doc(
        _member(
            "libtop.dylib",
            {
                "new": {
                    "format": "macho",
                    "soname": "@rpath/libtop.dylib",
                    "needed": ["@rpath/libmid.1.dylib"],
                }
            },
        ),
        _member(
            "libmid.dylib",
            {
                "new": {
                    "format": "macho",
                    "soname": "@rpath/libmid.1.dylib",
                    "needed": [],
                }
            },
        ),
    )
    assert _edges(doc) == {("libtop.dylib", "libmid.dylib", "new")}


def test_macho_names_stay_exact_and_are_not_case_folded() -> None:
    # Install names differing only in case are two libraries: a need for
    # ``@rpath/liba.dylib`` binds to ``liba.dylib``, never to ``libA.dylib``.
    doc = _doc(
        _member(
            "libA.dylib",
            {
                "new": {
                    "format": "macho",
                    "soname": "@rpath/libA.dylib",
                    "needed": ["@rpath/liba.dylib"],
                }
            },
        ),
        _member(
            "liba.dylib",
            {
                "new": {
                    "format": "macho",
                    "soname": "@rpath/liba.dylib",
                    "needed": [],
                }
            },
        ),
    )
    assert _edges(doc) == {("libA.dylib", "liba.dylib", "new")}


def test_elf_names_stay_exact_and_are_not_case_folded() -> None:
    doc = _doc(
        _member(
            "libA.so",
            {"old": {"format": "elf", "soname": "libA.so", "needed": ["liba.so"]}},
        ),
        _member(
            "liba.so",
            {"old": {"format": "elf", "soname": "liba.so", "needed": []}},
        ),
    )
    assert _edges(doc) == {("libA.so", "liba.so", "old")}


def test_scope_only_member_links_a_pe_need_by_its_case_folded_name() -> None:
    # An old-only member is never read, but a PE import of it still names it:
    # the need links to the scope member, not to an external node.
    doc = _doc(
        _member(
            "app.dll",
            {"new": {"format": "pe", "needed": ["FOO.DLL"]}},
        )
    )
    doc["comparison_scope"] = {"members": [{"name": "Foo.dll", "state": "removed"}]}
    graph = compute_release_dependency_graph(doc)
    assert {(e.source, e.target) for e in graph.edges} == {("app.dll", "Foo.dll")}
    assert {n.name for n in graph.nodes if n.status == "external"} == set()


def test_scope_entries_that_are_not_mappings_are_ignored() -> None:
    # A malformed comparison_scope entry is skipped, not read as a member; a
    # well-formed unchecked member beside it is still a not_evaluated node.
    doc = _doc(_member("libA.so", {}))
    doc["comparison_scope"] = {
        "members": ["junk", None, {"name": "libB.so", "state": "unselected"}]
    }
    graph = compute_release_dependency_graph(doc)
    nodes = {n.name: n for n in graph.nodes}
    assert set(nodes) == {"libA.so", "libB.so"}
    assert nodes["libB.so"].deps == DEPS_NOT_EVALUATED


def test_member_without_facts_is_a_not_evaluated_node_with_no_edges() -> None:
    doc = _doc(
        _member(
            "h.so",
            {"old": {"state": "not_evaluated", "reason": "x"}},
        ),
        _member(
            "p.dll",
            {"new": {"format": "pe", "soname": None, "needed": ["K.dll"]}},
        ),
    )
    graph = compute_release_dependency_graph(doc)
    by_name = {n.name: n for n in graph.nodes}
    assert by_name["h.so"].deps == DEPS_NOT_EVALUATED
    assert by_name["p.dll"].deps == DEPS_RECORDED
    assert all(e.source != "h.so" for e in graph.edges)
    assert graph.members_not_evaluated == 1
    assert graph.members_with_facts == 1


def test_partial_member_keeps_its_edges_and_is_counted_as_partial() -> None:
    doc = _doc(
        _member(
            "p.dll",
            {
                "old": {
                    "format": "pe",
                    "soname": None,
                    "needed": ["K.dll"],
                    "incomplete": ["delay_imports"],
                }
            },
        ),
    )
    graph = compute_release_dependency_graph(doc)
    assert [(e.source, e.target) for e in graph.edges] == [("p.dll", "K.dll")]
    assert {n.name: n.deps for n in graph.nodes}["p.dll"] == DEPS_PARTIAL
    assert graph.members_partial == 1
    assert graph.members_with_facts == 1


def test_a_member_with_neither_side_read_is_not_evaluated() -> None:
    assert member_dependency_facts(None, None) == {
        "state": "not_evaluated",
        "reason": "no dependency metadata was read from either side",
    }
