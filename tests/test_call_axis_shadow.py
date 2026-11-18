"""ADR-094 D9: the shadow ``call`` axis wired into ``compare``.

The axis is recorded for each function a signature finding names, rendered in
the JSON and Markdown reports, and never changes a verdict or finding. The
oracle for each case is the x86-64 SysV rule stated independently in the
test (which register an argument travels in), not the evaluator's helpers.
"""

from __future__ import annotations

import itertools
import json
from typing import Any

import pytest

from abicheck.checker import compare
from abicheck.compare.call_axis_shadow import abi_of, call_axis_shadow
from abicheck.compare.call_interface import AxisStatus
from abicheck.model.abi_fundamental import AbiFundamental, FundamentalEncoding
from abicheck.model.call_interface import SYSV_AMD64
from abicheck.model.change_catalog.registry import Verdict
from abicheck.model.declarations import Function, Param, Visibility
from abicheck.model.elf_facts import ElfMetadata
from abicheck.model.fact import Fact
from abicheck.model.snapshot import AbiSnapshot
from abicheck.report.call_axis_section import render_call_axis_shadow_from_mapping
from abicheck.workflows.call_axis_shadow import disagreement_of

_FACT = {
    "int": AbiFundamental(FundamentalEncoding.SIGNED, 4, 4),
    "long": AbiFundamental(FundamentalEncoding.SIGNED, 8, 8),
    "double": AbiFundamental(FundamentalEncoding.FLOAT, 8, 8),
}


def _fn(name: str, params: list[tuple[str, str]], ret: str = "int") -> Function:
    return Function(
        name=name,
        mangled=name,
        return_type=ret,
        return_type_abi_fact=Fact.present(_FACT[ret]),
        params=[
            Param(name=n, type=t, type_abi_fact=Fact.present(_FACT[t]))
            for n, t in params
        ],
        is_variadic=False,
        is_extern_c=True,
        contract_attributes=[],
        visibility=Visibility.PUBLIC,
    )


def _snap(
    version: str,
    fns: list[Function],
    *,
    machine: str = "EM_X86_64",
    elf_class: int = 64,
    platform: str | None = "elf",
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libdemo.so",
        version=version,
        functions=fns,
        elf=ElfMetadata(machine=machine, elf_class=elf_class),
        platform=platform,
    )


#: (old params, new params, expected status). Oracle: SysV passes the n-th
#: INTEGER-class argument in the n-th integer register and the n-th SSE-class
#: one in the n-th xmm register; an old caller is intact iff every argument
#: the new function reads sits in the same register, at the same width, and
#: was set by the old caller.
_CASES = [
    ([("x", "int")], [("x", "long")], AxisStatus.BROKEN),  # width change
    ([("a", "int"), ("b", "int")], [("a", "int")], AxisStatus.PRESERVED),
    ([("a", "int")], [("a", "int"), ("b", "int")], AxisStatus.BROKEN),
    ([("a", "int"), ("d", "double")], [("a", "int")], AxisStatus.PRESERVED),
    # dropping the first int moves `b` from rsi to rdi
    ([("a", "int"), ("b", "int")], [("b", "int")], AxisStatus.BROKEN),
    # dropping an int leaves a double in xmm0
    ([("a", "int"), ("d", "double")], [("d", "double")], AxisStatus.PRESERVED),
    ([("d", "double")], [("d", "long")], AxisStatus.BROKEN),
]


@pytest.mark.parametrize(("old_p", "new_p", "status"), _CASES)
def test_call_axis_shadow_matches_the_register_oracle(
    old_p: list[tuple[str, str]],
    new_p: list[tuple[str, str]],
    status: AxisStatus,
) -> None:
    old = _snap("1", [_fn("f", old_p)])
    new = _snap("2", [_fn("f", new_p)])
    result = compare(old, new)
    records = call_axis_shadow(old, new, result.changes)
    assert [r.symbol for r in records] == ["f"]
    assert records[0].abi == SYSV_AMD64
    assert records[0].axis.status is status, records[0].axis.reason


@pytest.mark.parametrize(
    ("machine", "elf_class", "platform", "expected"),
    [
        ("EM_X86_64", 64, "elf", SYSV_AMD64),
        ("EM_X86_64", 64, None, SYSV_AMD64),
        ("EM_X86_64", 32, "elf", ""),  # x32
        ("EM_AARCH64", 64, "elf", ""),
        ("EM_386", 32, "elf", ""),
        ("EM_X86_64", 64, "pe", ""),
        ("EM_X86_64", 64, "macho", ""),
    ],
)
def test_abi_of_maps_only_x86_64_elf(
    machine: str, elf_class: int, platform: str | None, expected: str
) -> None:
    snap = _snap("1", [], machine=machine, elf_class=elf_class, platform=platform)
    assert abi_of(snap) == expected


def test_abi_of_without_binary_facts_is_unmodelled() -> None:
    assert abi_of(AbiSnapshot(library="l", version="1")) == ""


def test_unmodelled_platform_reads_unknown_never_a_claim() -> None:
    old = _snap("1", [_fn("f", [("x", "int")])], machine="EM_AARCH64")
    new = _snap("2", [_fn("f", [("x", "long")])], machine="EM_AARCH64")
    result = compare(old, new)
    (entry,) = result.call_axis_shadow or []
    assert entry["status"] == "unknown"
    assert entry["abi"] is None
    assert entry["disagreement"] is None


def test_mixed_platform_pair_is_unmodelled() -> None:
    old = _snap("1", [_fn("f", [("x", "int")])])
    new = _snap("2", [_fn("f", [("x", "long")])], machine="EM_AARCH64")
    records = call_axis_shadow(old, new, compare(old, new).changes)
    assert [(r.abi, r.axis.status) for r in records] == [("", AxisStatus.UNKNOWN)]


@pytest.mark.parametrize(
    ("verdict", "status"), list(itertools.product(Verdict, AxisStatus))
)
def test_disagreement_is_exactly_the_binary_compatibility_mismatch(
    verdict: Verdict, status: AxisStatus
) -> None:
    breaking = verdict == "BREAKING"
    expected = {
        (True, "preserved"): "breaking_but_call_preserved",
        (False, "broken"): "compatible_but_call_broken",
    }.get((breaking, status.value))
    assert disagreement_of(verdict, status) == expected


def _three_function_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    old = _snap(
        "1",
        [
            _fn("scale", [("x", "int"), ("f", "double")], "long"),
            _fn("trim", [("a", "int"), ("b", "int")]),
            _fn("grow", [("a", "int")]),
            _fn("same", [("a", "int")]),
        ],
    )
    new = _snap(
        "2",
        [
            _fn("scale", [("x", "long"), ("f", "double")], "long"),
            _fn("trim", [("a", "int")]),
            _fn("grow", [("a", "int"), ("b", "int")]),
            _fn("same", [("a", "int")]),
        ],
    )
    return old, new


def test_compare_records_the_shadow_without_changing_findings_or_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old, new = _three_function_pair()
    result = compare(old, new)
    entries = {e["symbol"]: e for e in result.call_axis_shadow or []}
    assert set(entries) == {"scale", "trim", "grow"}  # `same` has no finding
    assert entries["scale"]["status"] == "broken"
    assert entries["grow"]["status"] == "broken"
    assert entries["trim"]["status"] == "preserved"
    assert entries["trim"]["existing_verdict"] == "BREAKING"
    assert entries["trim"]["disagreement"] == "breaking_but_call_preserved"
    assert entries["scale"]["disagreement"] is None

    # The verdict and the findings are those of a run with the shadow erased.
    import abicheck.workflows.call_axis_shadow as shadow

    monkeypatch.setattr(shadow, "attach_call_axis_shadow", lambda *a: None)
    old2, new2 = _three_function_pair()
    erased = compare(old2, new2)
    assert erased.call_axis_shadow is None
    assert result.verdict is erased.verdict is Verdict.BREAKING
    assert [(c.kind, c.symbol, c.description) for c in result.changes] == [
        (c.kind, c.symbol, c.description) for c in erased.changes
    ]


def test_json_report_carries_the_block_and_validates() -> None:
    from schema_validation import validate_instance

    from abicheck.report.diff_report import to_json
    from abicheck.schemas import COMPARE_REPORT_SCHEMA_PATH

    old, new = _three_function_pair()
    doc: dict[str, Any] = json.loads(to_json(compare(old, new)))
    assert {e["symbol"] for e in doc["call_axis_shadow"]} == {"scale", "trim", "grow"}
    schema = json.loads(COMPARE_REPORT_SCHEMA_PATH.read_text())
    validate_instance(doc, schema)


def test_json_report_omits_the_block_without_signature_findings() -> None:
    from abicheck.report.diff_report import to_json

    old = _snap("1", [_fn("f", [("a", "int")])])
    new = _snap("2", [_fn("f", [("a", "int")]), _fn("g", [("a", "int")])])
    assert "call_axis_shadow" not in json.loads(to_json(compare(old, new)))


def test_markdown_report_renders_the_disagreement() -> None:
    from abicheck.report.dispatch_markdown import to_markdown

    old, new = _three_function_pair()
    text = to_markdown(compare(old, new))
    assert "## Call axis (shadow, ADR-094)" in text
    (trim_row,) = [line for line in text.splitlines() if line.startswith("| `trim`")]
    assert "old callers still call it correctly" in trim_row


def test_markdown_section_escapes_table_cells() -> None:
    lines = render_call_axis_shadow_from_mapping(
        [
            {
                "symbol": "a|b",
                "status": "broken",
                "existing_verdict": "COMPATIBLE",
                "disagreement": "compatible_but_call_broken",
                "reason": "x | y",
            }
        ]
    )
    row = lines[-2]
    assert row.count("|") - row.count("\\|") == 6
    assert render_call_axis_shadow_from_mapping(None) == []
