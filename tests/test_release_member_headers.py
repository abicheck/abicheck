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
