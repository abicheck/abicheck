"""Per-member header operand and the unreached-type verdict rule, as
properties over an exhaustive small domain (the integration proof through
the CLI is ``test_release_member_headers_integration.py``).

Oracles are the invariants the gap entry states, not the helpers' code:
a member never loses a header that declares one of its exports, a header no
member claims reaches everyone, unknown ownership keeps the union, and a
type leaves a member's verdict only when a sibling reaches it.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from abicheck.checker_types import DiffResult
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.change_catalog.registry import Verdict
from abicheck.policy.member_type_attribution import (
    PROVEN_UNREACHABLE,
    REACHES,
    UNESTABLISHED,
    unreached_types_owned_elsewhere,
)
from abicheck.workflows.release_member_attribution import (
    dispose_unreached_type_findings,
)
from abicheck.workflows.release_member_headers import (
    ReleaseHeaderOwnership,
    SideHeaderOwnership,
    assign_side_headers,
    member_header_operand,
)
from abicheck.workflows.release_member_request import (
    MemberDelta,
    ReleaseMemberCompareRequest,
    member_request,
)

_FUNCS = ("f0", "f1", "f2")


def _headers(tmp_path: Path, declares: tuple[frozenset[str], ...]) -> list[Path]:
    out = []
    for i, names in enumerate(declares):
        h = tmp_path / f"h{i}.h"
        h.write_text(
            "".join(f"int {n}(void);\n" for n in sorted(names)) or "struct T{int x;};\n"
        )
        out.append(h)
    return out


_SUBSETS = [
    frozenset(c)
    for r in range(len(_FUNCS) + 1)
    for c in itertools.combinations(_FUNCS, r)
]


@pytest.mark.parametrize(
    "declares",
    list(itertools.product(_SUBSETS[:5], repeat=2))
    + [(_SUBSETS[1], _SUBSETS[2], frozenset())],
)
@pytest.mark.parametrize(
    "exports",
    [
        {"a": frozenset({"f0"}), "b": frozenset({"f1"})},
        {"a": frozenset({"f0", "f1"}), "b": frozenset({"f2"})},
        {"a": frozenset({"f0"}), "b": None},
        {"a": frozenset({"f2"}), "b": frozenset({"f2"}), "c": frozenset({"f1"})},
    ],
)
def test_assignment_invariants(
    tmp_path: Path,
    declares: tuple[frozenset[str], ...],
    exports: dict[str, frozenset[str] | None],
) -> None:
    files = _headers(tmp_path, declares)
    side = assign_side_headers(files, exports)
    declared_by = dict(zip(files, declares, strict=True))
    claimed_by_anyone = {
        f for f in files if any(e and declared_by[f] & e for e in exports.values())
    }
    for key, exp in exports.items():
        operand = side.headers_for(key)
        got = files if operand is None else operand
        assert set(got) <= set(files)
        assert got == [f for f in files if f in got], "union order is kept"
        own = {f for f in files if exp and declared_by[f] & exp}
        assert own <= set(got), "a member never loses a header declaring its export"
        assert set(files) - claimed_by_anyone <= set(got), (
            "unclaimed headers reach everyone"
        )
        if not own:
            assert operand is None, "a member claiming nothing keeps the union"
        foreign = {f for f in files if f in claimed_by_anyone and f not in own}
        assert not (foreign & set(got)) or operand is None


def test_single_member_and_no_headers_keep_the_union(tmp_path: Path) -> None:
    files = _headers(tmp_path, (frozenset({"f0"}),))
    assert assign_side_headers(files, {"a": frozenset({"f0"})}).member_files == {}
    assert (
        assign_side_headers([], {"a": frozenset(), "b": frozenset()}).member_files == {}
    )


def test_directory_entry_keeps_its_include_root_and_public_dir(tmp_path: Path) -> None:
    inc = tmp_path / "include"
    inc.mkdir()
    (inc / "a.h").write_text("int fa(void);\n")
    (inc / "b.h").write_text("int fb(void);\n")
    side = assign_side_headers([inc], {"a": frozenset({"fa"}), "b": frozenset({"fb"})})
    assert side.headers_for("a") == [inc / "a.h"]
    parent = ReleaseMemberCompareRequest(old_headers=[inc], new_headers=[inc])
    fields = member_header_operand(
        ReleaseHeaderOwnership(old=side, new=side), "a", parent
    )
    assert fields["old_headers"] == [inc / "a.h"]
    assert fields["old_includes"] == [inc]
    assert fields["public_header_dirs"] == [inc]


def test_member_delta_header_fields_inherit_when_none() -> None:
    parent = ReleaseMemberCompareRequest(
        old_headers=[Path("u.h")], new_headers=[Path("u.h")]
    )
    inherited = member_request(parent, MemberDelta(Path("o.so"), Path("n.so")))
    assert inherited.old_headers is parent.old_headers
    narrowed = member_request(
        parent, MemberDelta(Path("o.so"), Path("n.so"), old_headers=[Path("a.h")])
    )
    assert narrowed.old_headers == [Path("a.h")]
    assert narrowed.new_headers is parent.new_headers
    assert member_header_operand(None, "a", parent) == {}
    assert (
        member_header_operand(
            ReleaseHeaderOwnership(SideHeaderOwnership(), SideHeaderOwnership()),
            "a",
            parent,
        )
        == {}
    )


_VERDICTS = (REACHES, PROVEN_UNREACHABLE, UNESTABLISHED, None)


@pytest.mark.parametrize("row", list(itertools.product(_VERDICTS, repeat=3)))
def test_unreached_type_leaves_a_member_only_when_a_sibling_reaches_it(
    row: tuple[str | None, ...],
) -> None:
    members = ("a", "b", "c")
    attributions = {
        m: ({"T": v} if v else {}) for m, v in zip(members, row, strict=True)
    }
    plan = unreached_types_owned_elsewhere(attributions)
    for m, v in zip(members, row, strict=True):
        siblings_reaching = sorted(
            o for o, w in zip(members, row, strict=True) if o != m and w == REACHES
        )
        expected = v == PROVEN_UNREACHABLE and bool(siblings_reaching)
        assert ("T" in plan.get(m, {})) is expected
        if expected:
            assert plan[m]["T"] == tuple(siblings_reaching)
    # The gate is never lost: whenever someone is relieved, someone reaches.
    if plan:
        assert REACHES in row


def _change(kind: ChangeKind, symbol: str) -> Change:
    return Change(kind=kind, symbol=symbol, description=f"{kind.value} {symbol}")


def test_dispose_records_lowers_and_never_raises() -> None:
    widget = _change(ChangeKind.TYPE_SIZE_CHANGED, "Widget")
    field = _change(ChangeKind.TYPE_SIZE_CHANGED, "Widget::x")
    other = _change(ChangeKind.FUNC_ADDED, "b_new")
    result = DiffResult(
        old_version="1",
        new_version="2",
        library="libb.so",
        changes=[widget, other],
        redundant_changes=[field],
        redundant_count=1,
        verdict=Verdict.BREAKING,
    )
    moved = dispose_unreached_type_findings(result, {"Widget": ("liba.so",)})
    assert {id(c) for c in moved} == {id(widget), id(field)}
    assert result.changes == [other]
    assert result.redundant_changes == [] and result.redundant_count == 0
    assert result.out_of_surface_count == 2
    assert all(
        "reached by liba.so" in (c.surface_exclusion_reason or "") for c in moved
    )
    assert result.verdict == Verdict.COMPATIBLE
    # Nothing to move: the verdict is untouched.
    assert dispose_unreached_type_findings(result, {"Gadget": ("liba.so",)}) == []
    assert result.verdict == Verdict.COMPATIBLE


# --- unit paths the CLI integration proof covers only end to end ---------


def test_export_tokens_reads_plain_and_demangled_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import abicheck.elf_symbol_filter as esf
    import abicheck.workflows.extraction as extraction
    from abicheck.workflows import release_member_headers as rmh

    monkeypatch.setattr(extraction, "parse_elf_metadata", lambda p: object())
    monkeypatch.setattr(
        esf,
        "exported_symbol_names",
        lambda elf, types, abi_relevant_only: [
            "a_open@@V1",
            "_ZN2ns6Widget4drawEv",
            "_Z",  # demangles to nothing usable
        ],
    )
    assert rmh._export_tokens(tmp_path / "lib.so") == frozenset({"a_open", "draw"})
    # No exports, and an unreadable binary: ownership unknown.
    monkeypatch.setattr(esf, "exported_symbol_names", lambda *a, **k: [])
    assert rmh._export_tokens(tmp_path / "lib.so") is None

    def boom(p: Path) -> object:
        raise ValueError("not ELF")

    monkeypatch.setattr(extraction, "parse_elf_metadata", boom)
    assert rmh._export_tokens(tmp_path / "lib.so") is None


def test_header_tokens_of_an_unreadable_header_is_empty(tmp_path: Path) -> None:
    from abicheck.workflows.release_member_headers import _header_tokens

    assert _header_tokens(tmp_path / "missing.h") == frozenset()


def test_resolve_release_header_ownership_sides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from abicheck.workflows import release_member_headers as rmh

    a, b = tmp_path / "a.h", tmp_path / "b.h"
    a.write_text("int a_open(void);\n")
    b.write_text("int b_open(void);\n")
    tokens = {"liba": frozenset({"a_open"}), "libb": frozenset({"b_open"})}
    monkeypatch.setattr(rmh, "_export_tokens", lambda p: tokens.get(p.stem))
    paths = {"liba": tmp_path / "liba", "libb": tmp_path / "libb"}
    got = rmh.resolve_release_header_ownership(
        ["liba", "libb"], paths, paths, [a, b], []
    )
    assert got.old.headers_for("liba") == [a]
    assert got.old.headers_for("libb") == [b]
    assert got.new == SideHeaderOwnership()  # no headers on that side
    # Fewer than two members: the union on both sides.
    one = rmh.resolve_release_header_ownership(["liba"], paths, paths, [a], [b])
    assert one.old.union == (a,) and one.new.union == (b,)


def test_release_pass_moves_only_scoped_sibling_owned_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from abicheck.workflows.release_compare_reporter import SilentReleaseReporter
    from abicheck.workflows.release_members import dispose_release_unreached_types

    def result(library: str, *, scoped: bool) -> DiffResult:
        return DiffResult(
            old_version="1",
            new_version="2",
            library=library,
            changes=[_change(ChangeKind.TYPE_SIZE_CHANGED, "Widget")],
            verdict=Verdict.BREAKING,
            scope_to_public_surface=scoped,
        )

    restamped: list[str] = []

    class _Recording(SilentReleaseReporter):
        def reproject(self, entry, result, *, scope_changed):  # type: ignore[no-untyped-def]
            assert scope_changed
            restamped.append(str(entry["library"]))

    entries: list[dict[str, object]] = [
        {"library": "liba.so", "_type_attribution": {"Widget": REACHES},
         "_diff_result": result("liba.so", scoped=True)},
        {"library": "libb.so", "_type_attribution": {"Widget": PROVEN_UNREACHABLE},
         "_diff_result": result("libb.so", scoped=True)},
        {"library": "libc.so", "_type_attribution": {"Widget": PROVEN_UNREACHABLE},
         "_diff_result": result("libc.so", scoped=False)},  # scoping off: kept
        {"library": "libd.so", "_type_attribution": "not a dict"},
    ]  # fmt: skip
    moved = dispose_release_unreached_types(
        entries,
        _Recording(),
        severity_config=None,
        require_complete_analysis=False,
        contract_evaluation=False,
    )
    assert moved == 1 and restamped == ["libb.so"]
    assert entries[2]["_diff_result"].changes  # type: ignore[union-attr]


def test_restamp_rederives_the_member_entry(tmp_path: Path) -> None:
    from abicheck.frontends.cli.release_member_reporter import CliReleaseReporter
    from abicheck.workflows.release_members import dispose_release_unreached_types

    def result(library: str) -> DiffResult:
        return DiffResult(
            old_version="1",
            new_version="2",
            library=library,
            changes=[
                _change(ChangeKind.TYPE_SIZE_CHANGED, "Widget"),
                _change(ChangeKind.FUNC_ADDED, f"{library}_new"),
            ],
            verdict=Verdict.BREAKING,
            scope_to_public_surface=True,
        )

    res = result("libb.so")
    entry: dict[str, object] = {
        "library": "libb.so",
        "verdict": "BREAKING",
        "_type_attribution": {"Widget": PROVEN_UNREACHABLE},
        "_diff_result": res,
    }
    sibling: dict[str, object] = {
        "library": "liba.so",
        "verdict": "BREAKING",
        "_type_attribution": {"Widget": REACHES},
        "_diff_result": result("liba.so"),
    }
    moved = dispose_release_unreached_types(
        [sibling, entry],
        CliReleaseReporter(output_dir=tmp_path),
        severity_config=None,
        require_complete_analysis=False,
        contract_evaluation=False,
    )
    assert moved == 1
    assert entry["verdict"] == res.verdict.value
    assert entry["breaking"] == 0
    assert entry["filtered_internal_count"] == res.out_of_surface_count == 1
    assert "Filtered as non-public ABI surface in libb.so" in str(
        entry["_scope_ledger_text"]
    )
    assert (tmp_path / "libb.json").is_file()
