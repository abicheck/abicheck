"""Phase 2 cross-layer joins on real toolchain output, through the public
``dump`` -> ``compare`` workflow (evidence-entity-model plan; AGENTS.md
"Validate the user-facing result").

Two versions of one library are built with ``-g`` and dumped with the real
CLI into stored snapshots. The oracle is the fixed ground truth of the
fixture below, not the join's own helpers:

* ``api::run`` and the ``extern "C"`` ``cfunc`` are exported; the public
  inline ``api::inl`` is not (an orphan declaration, not a missing export);
  ``impl::use`` and ``get_anon`` are exported with no public declaration
  (orphan exports).
* ``api::Foo``, ``Plain`` and the typedef'd anonymous ``Anon`` are both in
  the headers and in DWARF; castxml drops ``api::v1``'s inline namespace, so
  ``api::v1::S`` stays separate (G15); ``impl::Foo`` exists only in DWARF.
Dumped with ``--include-system-declarations`` so the DWARF pool is not
pre-scoped at dump time (``dumper_scoping._scoped_dwarf`` would drop the
header-less ``impl::Foo``): the joins, not that filter, decide here.

* v2 grows the private ``impl::Foo`` -- which only shares a *bare* name with
  the public ``api::Foo`` -- and the public ``Plain``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="ELF/DWARF fixture with Itanium linker names",
    ),
]

HEADER = """\
#pragma once
namespace api {
struct Foo { int a; long b; };
inline namespace v1 { struct S { int x; }; }
int run(Foo*);
int helper(S*);
inline int inl() { return 1; }
}
typedef struct { int x; double y; } Anon;
struct Plain { char c; PLAIN_EXTRA };
extern "C" int cfunc(struct Plain*);
"""
SOURCE = """\
#include "a.h"
extern "C" int cfunc(struct Plain* p) { return p->c; }
namespace api { int run(Foo* f) { return f->a; } int helper(S* s) { return s->x; } }
static Anon g; Anon* get_anon() { return &g; }
namespace impl { struct Foo { IMPL_FIELDS }; int use(Foo* f) { return (int)sizeof(*f); } }
"""
VERSIONS = {
    "v1": {"PLAIN_EXTRA": "", "IMPL_FIELDS": "char z;"},
    "v2": {"PLAIN_EXTRA": "int grown;", "IMPL_FIELDS": "char z; long w[4];"},
}


def _require(tool: str) -> None:
    if shutil.which(tool) is None:
        pytest.skip(f"{tool} not available")


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        env={**os.environ, "ABICHECK_AST_FRONTEND": "castxml"},
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def dumps(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    for tool in ("g++", "castxml"):
        _require(tool)
    root = tmp_path_factory.mktemp("joins")
    out: dict[str, Path] = {}
    for version, macros in VERSIONS.items():
        d = root / version
        d.mkdir()
        header = HEADER.replace("PLAIN_EXTRA", macros["PLAIN_EXTRA"])
        (d / "a.h").write_text(header)
        (d / "a.cpp").write_text(SOURCE.replace("IMPL_FIELDS", macros["IMPL_FIELDS"]))
        subprocess.run(
            ["g++", "-g", "-shared", "-fPIC", "-o", "liba.so", "a.cpp"],
            check=True,
            cwd=d,
        )
        target = d / "snap.json"
        proc = _run(
            [
                "abicheck",
                "dump",
                "liba.so",
                "-H",
                "a.h",
                # Keep every DWARF type: the default dependency scoping would
                # already drop the header-less `impl::Foo`, and the point is
                # what the joins decide when it is present.
                "--include-system-declarations",
                "-o",
                str(target),
            ],
            cwd=d,
        )
        assert proc.returncode == 0, proc.stderr
        out[version] = target
    return out


def _load(path: Path):
    from abicheck.serialization import load_snapshot

    return load_snapshot(path)


class TestExportJoinOnStoredDump:
    def test_join_states_match_the_fixture(self, dumps: dict[str, Path]) -> None:
        from abicheck.compare.export_join import join_exports
        from abicheck.model.graph_join import JoinState

        j = join_exports(_load(dumps["v1"]))
        assert j.complete
        assert j.declaration("decl://_ZN3api3runEPNS_3FooE").state is JoinState.MATCHED
        assert j.declaration("decl://cfunc").state is JoinState.MATCHED
        inl = j.declaration("decl://_ZN3api3inlEv")
        assert (inl.state, inl.reason) == (JoinState.UNMATCHED, "no_export")
        for orphan in ("_ZN4impl3useEPNS_3FooE", "_Z8get_anonv"):
            assert j.export("elf", orphan).state is JoinState.UNMATCHED

    def test_join_agrees_with_binary_exported_fact(
        self, dumps: dict[str, Path]
    ) -> None:
        """The castxml producer's ``binary_exported_fact`` and the join read
        one table; they must never contradict each other."""
        from abicheck.compare.export_join import join_exports
        from abicheck.model.fact import FactStatus
        from abicheck.model.graph_join import JoinState

        snap = _load(dumps["v1"])
        j = join_exports(snap)
        checked = 0
        for fn, ident in zip(snap.functions, j.identities.functions):
            fact = fn.binary_exported_fact
            if fact is None or fact.status is not FactStatus.PRESENT:
                continue
            state = j.declaration(ident.node_id).state
            assert (state is JoinState.MATCHED) == fact.value, fn.mangled
            checked += 1
        assert checked >= 4


class TestDebugTypeJoinOnStoredDump:
    def test_join_states_match_the_fixture(self, dumps: dict[str, Path]) -> None:
        from abicheck.compare.debug_type_join import join_debug_types
        from abicheck.model.graph_join import JoinState

        snap = _load(dumps["v1"])
        j = join_debug_types(snap)
        assert j.complete and j.odr_observed

        def state(name: str) -> JoinState:
            return j.join.right[f"debug_type://debug/record/{name}"].state

        assert state("api::Foo") is JoinState.MATCHED
        assert state("Plain") is JoinState.MATCHED
        assert state("Anon") is JoinState.MATCHED
        assert state("api::v1::S") is JoinState.UNMATCHED
        assert state("impl::Foo") is JoinState.UNMATCHED
        assert j.header("type://api::S").state is JoinState.UNMATCHED


class TestCompareUsesTheJoins:
    def _compare(self, dumps: dict[str, Path], *extra: str) -> dict:
        proc = _run(
            [
                "abicheck",
                "compare",
                str(dumps["v1"]),
                str(dumps["v2"]),
                "-o",
                "json=-",
                *extra,
            ],
            cwd=dumps["v1"].parent,
        )
        assert proc.returncode in (0, 2, 4), proc.stderr
        return json.loads(proc.stdout)

    def test_private_type_sharing_a_bare_name_is_not_diffed(
        self, dumps: dict[str, Path]
    ) -> None:
        # Before Phase 2 the DWARF tier scoped by bare name, so `impl::Foo`
        # rode in on the public `api::Foo`: its growth was diffed, then
        # filtered out again as out-of-surface
        # (`surface_scope.out_of_surface_changes`). Its qualified name joins no
        # header type, so it is now never in the DWARF tier's scope -- exactly
        # like any other header-less private type.
        report = self._compare(dumps)
        described = [
            c
            for section in (
                report.get("changes", []),
                report.get("surface_scope", {}).get("out_of_surface_changes", []),
                report.get("scope", {}).get("filtered_internal_changes", []),
            )
            for c in section
            if c.get("symbol") == "impl::Foo"
        ]
        assert described == []
        # The public type's growth is still reported.
        assert "Plain" in {c.get("symbol") for c in report["changes"]}

    def test_export_contract_roots_come_from_the_join(
        self, dumps: dict[str, Path]
    ) -> None:
        # The `exports` contract's roots are the join's matched declarations
        # -- the inline `api::inl` is not one -- and the orphan exports
        # (`impl::use`, `get_anon`) leave the export provider incomplete.
        report = self._compare(dumps, "--contract", "exports")
        providers = report["contract_context"]["contract_evidence"]["providers"]
        from abicheck.model.graph_entity_identity import snapshot_identities

        rooted = {"_ZN3api3runEPNS_3FooE", "_ZN3api6helperEPNS_2v11SE", "cfunc"}
        for side, dump in (("old", "v1"), ("new", "v2")):
            (entry,) = [
                p for p in providers if p["record"]["id"] == f"export_table:{side}"
            ]
            # Roots are recorded as each declaration's Phase 1 (I1) node id,
            # taken from the snapshot's own identity table.
            snap = _load(dumps[dump])
            ids = snapshot_identities(snap)
            expected = sorted(
                ident.node_id
                for fn, ident in zip(snap.functions, ids.functions, strict=True)
                if fn.mangled in rooted
            )
            assert len(expected) == len(rooted)
            assert entry["declarations"] == expected
            assert entry["record"]["reason_code"] == "unmatched_exports"


class TestOdrConflictOnRealDwarf:
    """Two CUs define ``Dup`` differently; the DWARF walk records the second
    definition, and the debug-type join refuses to pick one when the header
    carries no layout that could."""

    def test_conflict_is_observed_and_reported_ambiguous(self, tmp_path: Path) -> None:
        _require("g++")
        from abicheck.compare.debug_type_join import join_debug_types
        from abicheck.dwarf_metadata import parse_dwarf_metadata
        from abicheck.model import AbiSnapshot, RecordType
        from abicheck.model.graph_join import JoinState

        (tmp_path / "a.cpp").write_text(
            "struct Dup { int a; };\nint f1(Dup* d) { return d->a; }\n"
        )
        (tmp_path / "b.cpp").write_text(
            "struct Dup { long a; long b; };\nlong f2(Dup* d) { return d->b; }\n"
        )
        subprocess.run(
            ["g++", "-g", "-shared", "-fPIC", "-o", "libodr.so", "a.cpp", "b.cpp"],
            check=True,
            cwd=tmp_path,
        )
        dwarf = parse_dwarf_metadata(tmp_path / "libodr.so")
        assert dwarf.odr_conflicts_observed
        assert [c.byte_size for c in dwarf.struct_odr_conflicts["Dup"]] == [16]

        snap = AbiSnapshot(
            library="libodr.so",
            version="1",
            types=[RecordType(name="Dup", kind="struct")],
            dwarf=dwarf,
        )
        head = join_debug_types(snap).header("type://Dup")
        assert head.state is JoinState.AMBIGUOUS
        assert head.candidates == (
            "debug_type://debug/record/Dup",
            "debug_type://debug/record/Dup#2",
        )
