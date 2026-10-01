"""Per-member attribution of header-derived type findings in a release.

Known-gaps "A directory ``compare``'s ``-H``/``--header`` set is applied to
every member", step 3: a type finding several members report identically is
folded into one product-level finding, and that finding now says which
members' own export surfaces reach the type. Also covers the export-surface
defect the work exposed -- the C tag idiom ``typedef struct X {...} X;`` was
flagged as an *ambiguous* name, which made every such type undecidable.

Oracles here are written independently of the implementation: a regex for
the tag idiom, and a plain truth table for attribution.
"""

from __future__ import annotations

import itertools
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.export_surface import compute_export_surface
from abicheck.model import (
    AbiSnapshot,
    EnumType,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    Visibility,
)
from abicheck.policy.member_type_attribution import (
    PROVEN_UNREACHABLE,
    REACHES,
    UNESTABLISHED,
    attribute_type,
)
from abicheck.report.release_public_surface import (
    compute_release_public_surface,
    dedupe_shared_member_findings,
    render_release_public_surface_markdown,
)
from abicheck.workflows.release_member_attribution import member_type_attribution


def _api(param_type: str) -> Function:
    return Function(
        name="api",
        mangled="api",
        return_type="void",
        params=[Param(name="a0", type=param_type)],
        visibility=Visibility.PUBLIC,
        origin=ScopeOrigin.UNKNOWN,
    )


def _rec(name: str) -> RecordType:
    return RecordType(name=name, kind="struct", size_bits=64)


def _enum(name: str) -> EnumType:
    return EnumType(name=name, members=[])


# ── the tag-idiom ambiguity fix ─────────────────────────────────────────────

_SELF_ALIAS = re.compile(r"^\s*(?:(?:struct|union|enum|class)\s+)?X\s*$")

_TARGETS = (
    "X",
    "struct X",
    "union  X",
    "enum X",
    "class X",
    " struct   X ",
    "struct Y",
    "Y",
    "X *",
    "const X",
    "structX",
    "ns::X",
)


@pytest.mark.parametrize(
    ("target", "records", "enums"),
    list(itertools.product(_TARGETS, range(3), range(2))),
)
def test_typedef_ambiguity_matches_independent_oracle(
    target: str, records: int, enums: int
) -> None:
    """``X`` is ambiguous iff two nodes could answer to it.

    Oracle: more than one record/enum named ``X`` is always ambiguous; with
    exactly one, the typedef adds a second resolution unless it is the tag
    idiom (target spelled ``X`` with an optional elaborated keyword); with
    none, the typedef is the only resolution.
    """
    types = [_rec("X") for _ in range(records)] + [_rec("Y")]
    enum_list = [_enum("X") for _ in range(enums)]
    snap = AbiSnapshot(
        library="l",
        version="1",
        functions=[_api("X")],
        types=types,
        enums=enum_list,
        typedefs={"X": target},
        elf=ElfMetadata(symbols=[ElfSymbol(name="api")]),
    )
    total = records + enums
    expected = total >= 2 or (total == 1 and not _SELF_ALIAS.match(target))
    assert ("X" in compute_export_surface(snap).ambiguous_type_names) is expected


def test_tag_idiom_type_is_reached_and_decidable() -> None:
    """The motivating C shape: one record plus its own same-name typedef."""
    snap = AbiSnapshot(
        library="l",
        version="1",
        functions=[_api("const Widget *")],
        types=[_rec("Widget"), _rec("Internal")],
        typedefs={"Widget": "Widget"},
        elf=ElfMetadata(symbols=[ElfSymbol(name="api")]),
    )
    surf = compute_export_surface(snap)
    assert "Widget" not in surf.ambiguous_type_names
    assert "Widget" in surf.export_types
    assert "Internal" not in surf.export_types
    assert surf.exclusion_is_provable


# ── attribute_type: exhaustive truth table ──────────────────────────────────


def _surface(
    *, knows: bool, ambiguous: bool, resolvable: bool, reached: bool, provable: bool
):
    return SimpleNamespace(
        all_types={"T"} if knows else set(),
        ambiguous_type_names={"T"} if ambiguous else set(),
        resolvable=resolvable,
        export_types={"T"} if reached else set(),
        exclusion_is_provable=provable,
    )


_FLAGS = ("knows", "ambiguous", "resolvable", "reached", "provable")
_SIDE_STATES = [
    dict(zip(_FLAGS, bits, strict=True))
    for bits in itertools.product((False, True), repeat=5)
]


def _oracle(sides: list[dict[str, bool]]) -> str | None:
    knowing = [s for s in sides if s["knows"]]
    if not knowing:
        return None
    if any(s["ambiguous"] for s in knowing):
        return UNESTABLISHED
    if any(s["resolvable"] and s["reached"] for s in knowing):
        return REACHES
    if all(s["provable"] for s in knowing):
        return PROVEN_UNREACHABLE
    return UNESTABLISHED


def test_attribute_type_matches_truth_table_for_every_side_pair() -> None:
    mismatches = []
    seen = set()
    for old, new in itertools.product(_SIDE_STATES, repeat=2):
        got = attribute_type("T", [_surface(**old), _surface(**new)])
        want = _oracle([old, new])
        seen.add(want)
        if got != want:
            mismatches.append((old, new, got, want))
    assert not mismatches, mismatches[:5]
    # Vacuity guard: the table exercises every outcome.
    assert seen == {None, REACHES, PROVEN_UNREACHABLE, UNESTABLISHED}


def test_attribute_type_is_side_order_independent() -> None:
    for old, new in itertools.product(_SIDE_STATES, repeat=2):
        a = attribute_type("T", [_surface(**old), _surface(**new)])
        b = attribute_type("T", [_surface(**new), _surface(**old)])
        assert a == b


def test_never_proven_unreachable_while_any_knowing_side_is_unprovable() -> None:
    """The safety property: "not shown to reach" never becomes "shown not to"."""
    for old, new in itertools.product(_SIDE_STATES, repeat=2):
        got = attribute_type("T", [_surface(**old), _surface(**new)])
        if got == PROVEN_UNREACHABLE:
            assert all(s["provable"] for s in (old, new) if s["knows"])


def test_member_type_attribution_skips_non_type_symbols() -> None:
    snap = AbiSnapshot(
        library="l",
        version="1",
        functions=[_api("Widget")],
        types=[_rec("Widget"), _rec("Internal")],
        elf=ElfMetadata(symbols=[ElfSymbol(name="api")]),
    )
    got = member_type_attribution(["Widget", "Internal", "api", "nope"], snap, snap)
    assert dict(got) == {"Widget": REACHES, "Internal": PROVEN_UNREACHABLE}
    assert member_type_attribution(["api"], snap, None) == {}


# ── the release fold ────────────────────────────────────────────────────────


def _finding(symbol: str, kind: str = "type_size_changed") -> dict[str, object]:
    return {
        "kind": kind,
        "symbol": symbol,
        "old_value": "64",
        "new_value": "96",
        "description": f"Size changed: {symbol}",
    }


def test_shared_finding_carries_member_partition_and_keeps_affected_list() -> None:
    results = [
        {
            "library": lib,
            "findings": [_finding("Widget"), _finding("api", "func_removed")],
        }
        for lib in ("liba.so", "libb.so", "libc.so")
    ]
    attribution = {
        "liba.so": {"Widget": REACHES},
        "libb.so": {"Widget": PROVEN_UNREACHABLE},
        # libc.so: its snapshots did not carry Widget at all.
    }
    fold = dedupe_shared_member_findings(results, attribution)
    by_symbol = {f.symbol: f for f in fold.shared}
    widget = by_symbol["Widget"]
    assert widget.affected_libraries == ("liba.so", "libb.so", "libc.so")
    assert widget.attribution == {
        REACHES: ("liba.so",),
        PROVEN_UNREACHABLE: ("libb.so",),
        UNESTABLISHED: ("libc.so",),
    }
    assert by_symbol["api"].attribution is None
    # Counts are untouched: every member still records the folded findings.
    assert all(entry["product_level_findings"] == 2 for entry in results)

    md = render_release_public_surface_markdown(
        compute_release_public_surface(
            None, acquisition={}, shared_findings=fold.shared
        )
    )
    assert (
        "reached by: liba.so; not reached by: libb.so; reach not established for: libc.so"
        in md
    )
    assert "(affects: liba.so, libb.so, libc.so)" in md  # the symbol finding


def test_no_attribution_input_leaves_the_fold_unchanged() -> None:
    results = [{"library": lib, "findings": [_finding("Widget")]} for lib in ("a", "b")]
    (shared,) = dedupe_shared_member_findings(results).shared
    assert shared.attribution is None
    assert "attribution" not in shared.to_dict()


# ── end to end through the CLI ──────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="ELF/DWARF tests require Linux")
@pytest.mark.skipif(
    shutil.which("gcc") is None or shutil.which("castxml") is None,
    reason="needs gcc + castxml",
)
def test_directory_compare_attributes_header_type_to_the_member_that_uses_it(
    tmp_path: Path,
) -> None:
    import json

    from click.testing import CliRunner

    from abicheck.cli import main

    for d in ("old", "new", "inc", "inc_new"):
        (tmp_path / d).mkdir()
    (tmp_path / "inc/foo.h").write_text(
        "typedef struct Widget { int w; int h; } Widget;\nint widget_area(const Widget *w);\n"
    )
    (tmp_path / "inc_new/foo.h").write_text(
        "typedef struct Widget { long w; int h; int d; } Widget;\nint widget_area(const Widget *w);\n"
    )
    (tmp_path / "foo.c").write_text(
        '#include "foo.h"\nint widget_area(const Widget *w) { return w->w * w->h; }\n'
    )
    (tmp_path / "bar.c").write_text("int bar_fn(int x) { return x + 1; }\n")
    for side, inc in (("old", "inc"), ("new", "inc_new")):
        subprocess.run(
            [
                "gcc",
                "-shared",
                "-fPIC",
                f"-I{tmp_path / inc}",
                str(tmp_path / "foo.c"),
                "-o",
                str(tmp_path / side / "libfoo.so"),
            ],
            check=True,
        )
        subprocess.run(
            [
                "gcc",
                "-shared",
                "-fPIC",
                str(tmp_path / "bar.c"),
                "-o",
                str(tmp_path / side / "libbar.so"),
            ],
            check=True,
        )
    out = tmp_path / "out.json"
    CliRunner().invoke(
        main,
        [
            "compare",
            str(tmp_path / "old"),
            str(tmp_path / "new"),
            "--header",
            f"old={tmp_path / 'inc/foo.h'}",
            "--header",
            f"new={tmp_path / 'inc_new/foo.h'}",
            "-o",
            f"json={out}",
        ],
    )
    doc = json.loads(out.read_text())
    shared = [
        f
        for f in doc["public_surface_reconciliation"]["shared_findings"]
        if f["symbol"] == "Widget"
    ]
    assert shared
    for finding in shared:
        assert finding["affected_libraries"] == ["libbar.so", "libfoo.so"]
        # libfoo's widget_area reaches Widget; libbar's only export has no
        # declaration, so its reach is honestly not established.
        assert finding["attribution"] == {
            "reaches": ["libfoo.so"],
            "unestablished": ["libbar.so"],
        }
