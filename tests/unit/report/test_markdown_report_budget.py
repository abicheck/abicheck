"""The whole Markdown report stays within a size budget as the finding count grows.

SVS field report: a C++ library that leaks libstdc++ template instantiations
produced a 574 KB Markdown report on a byte-identical rebuild (785 KB against
an older release). A GitHub PR comment holds 65,536 characters and a step
summary 1 MiB. Every non-gating section itemised one row per finding, and the
whole-document demangle pass grew each already-clipped mangled name back into
a multi-KB demangled template name.

The properties are stated over N (10, 500, 3000) synthetic leaked ``std::``
exports with real Itanium names that demangle to several KB, spread across
every section that lists findings: hygiene, deployment risk, additions,
quality, informational, not-evaluated, suppressed, contract conflicts and
review groups. The oracle is independent of the implementation: byte counts
of the rendered text, and the JSON's own finding list.
"""

from __future__ import annotations

import json
import shutil

import pytest

from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import DiffResult
from abicheck.contract_relevance_types import ContractRelevance
from abicheck.model.change import Change
from abicheck.model.evidence_status import CrossSourceEvolution
from abicheck.reporter import to_json, to_markdown

#: The documented budget for a report with no gating finding: well under a
#: GitHub PR comment (64 KiB), whatever the finding count. Written as a
#: literal so the test does not re-read the implementation's constants.
REPORT_BUDGET = 48 * 1024
#: The review digest (`-o review=`) is the summary-oriented view: smaller.
DIGEST_BUDGET = 8 * 1024
#: Longest line the budget allows: a clipped line (1000) plus the demangle
#: pass's bounded additions (a clipped demangled half beside each name).
LINE_BUDGET = 2500

pytestmark = pytest.mark.skipif(
    shutil.which("c++filt") is None, reason="needs c++filt for real demangling"
)

# A real libstdc++ `_Rb_tree<string, pair<const string, vector<string>>>`
# member: ~170 mangled characters that demangle to ~1.5 KB.
_TEMPLATE = (
    "_ZNSt8_Rb_treeINSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEESt4pair"
    "IKS5_St6vectorIS5_SaIS5_EEESt10_Select1stISA_ESt4lessIS5_ESaISA_EE"
    "{length}{ident}EPSt13_Rb_tree_nodeISA_E"
)


def _mangled(tag: str, i: int) -> str:
    ident = f"_M_{tag}{i}"
    return _TEMPLATE.format(length=len(ident), ident=ident)


def _change(kind: ChangeKind, tag: str, i: int, **extra: object) -> Change:
    sym = _mangled(tag, i)
    change = Change(kind, sym, f"Symbol '{sym}' ({kind.value})")
    for name, value in extra.items():
        setattr(change, name, value)
    return change


def _result(n: int) -> DiffResult:
    changes: list[Change] = []
    for i in range(n):
        changes += [
            _change(
                ChangeKind.EXPORTED_NOT_PUBLIC,
                "hyg",
                i,
                cross_source_evolution=CrossSourceEvolution.PERSISTENT,
            ),
            _change(ChangeKind.SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED, "leak", i),
            _change(ChangeKind.FUNC_ADDED, "add", i),
            _change(
                ChangeKind.FUNC_REMOVED,
                "ooc",
                i,
                contract_relevance=ContractRelevance.PROVEN_OUT_OF_CONTRACT,
                contract_reason_code="toolchain_runtime_origin",
            ),
        ]
    suppressed = [_change(ChangeKind.FUNC_REMOVED, "sup", i) for i in range(n)]
    result = DiffResult(
        "1",
        "2",
        "libsvs.so",
        changes,
        Verdict.COMPATIBLE_WITH_RISK,
        suppressed_count=n,
        suppressed_changes=suppressed,
        suppression_file_provided=True,
    )
    result.contract_conflicts = [
        {
            "conflict_kind": "exported_but_undeclared",
            "entity": _mangled("cfl", i),
            "side": "new",
            "reason_code": "x",
            "sources": [
                {
                    "source_kind": "export_table",
                    "claim": f"exports {_mangled('cfl', i)}",
                },
                {"source_kind": "header_declarations", "claim": "not declared"},
            ],
        }
        for i in range(n)
    ]
    return result


def _markdown(n: int) -> str:
    return to_markdown(_result(n), demangle=True, contract_evaluation=True)


@pytest.fixture(scope="module")
def sizes() -> dict[int, int]:
    return {n: len(_markdown(n).encode()) for n in (10, 500, 3000)}


def test_report_stays_under_the_documented_budget(sizes: dict[int, int]) -> None:
    assert max(sizes.values()) <= REPORT_BUDGET, sizes


def test_report_grows_sublinearly_with_the_finding_count(
    sizes: dict[int, int],
) -> None:
    # 300x the findings; only counts' digits and the switch from itemised to
    # rolled-up rows may differ. Linear growth would be ~300x.
    assert sizes[3000] <= 2 * sizes[500], sizes
    assert sizes[3000] - sizes[500] <= 1024, sizes


@pytest.mark.parametrize("n", [10, 3000])
def test_review_digest_stays_under_its_budget(n: int) -> None:
    from tests._report_test_helpers import review_digest

    text = review_digest(_result(n))
    assert len(text.encode()) <= DIGEST_BUDGET, len(text.encode())


def test_no_line_exceeds_the_line_budget() -> None:
    text = _markdown(500)
    longest = max(len(line) for line in text.splitlines())
    assert longest <= LINE_BUDGET, longest


def test_every_bounded_section_says_what_it_cut() -> None:
    n = 500
    text = _markdown(n)
    # Each population is still counted on the page, and the reader is told
    # where the itemised list lives.
    assert f"**exported_not_public**: {n} findings" in text
    assert f"**symbol_leaked_from_dependency_changed**: {n} findings" in text
    assert f"**func_added**: {n} findings" in text
    assert f"{n} change(s) suppressed" in text
    assert "(JSON `suppression.suppressed_changes`)" in text
    assert "(JSON `changes`)" in text  # not-evaluated overflow
    assert "(JSON `contract_conflicts`)" in text
    assert f"| exported_but_undeclared | toolchain runtime | {n} |" in text


def test_clipped_demangled_names_keep_their_mangled_identity() -> None:
    text = _markdown(10)
    sample = _mangled("add", 0)
    # The exact mangled spelling survives, in the demangle pass's brackets,
    # beside a clipped demangled half.
    assert f"[{sample}]" in text
    assert "std::_Rb_tree<" in text
    assert "…" in text


@pytest.mark.parametrize("n", [10, 500])
def test_json_keeps_every_finding(n: int) -> None:
    result = _result(n)
    to_markdown(result, demangle=True, contract_evaluation=True)
    doc = json.loads(to_json(result, contract_evaluation=True))
    symbols = {change["symbol"] for change in doc["changes"]}
    for tag in ("hyg", "leak", "add", "ooc"):
        assert all(_mangled(tag, i) in symbols for i in range(n)), tag
    assert len(doc["suppression"]["suppressed_changes"]) == n
    # `contract_conflicts` is emitted only beside a persisted contract
    # context, which this hand-built result has none of; the Markdown cap
    # never touches the result's own list either way.
    assert isinstance(result.contract_conflicts, list)
    assert len(result.contract_conflicts) == n


@pytest.mark.parametrize("limit", [20, 80, 160])
def test_demangle_clip_bounds_only_the_demangled_half(limit: int) -> None:
    from abicheck.demangle import demangle_batch, demangle_text

    tokens = [_mangled("dm", i) for i in range(5)] + ["_ZN5mylib3addEv"]
    text = " ; ".join(tokens)
    full = demangle_batch(tokens, accept_macho_prefix=True)
    clipped = demangle_text(text, max_demangled=limit)
    for token, part in zip(tokens, clipped.split(" ; "), strict=True):
        demangled = full[token]
        # The mangled identity is kept whole, in brackets, after the half.
        assert part.endswith(f" [{token}]"), part
        shown = part[: -len(f" [{token}]")]
        if len(demangled) <= limit:
            assert shown == demangled
        else:
            assert len(shown) == limit and shown.endswith("…")
            assert demangled.startswith(shown[:-1])
    # Unbounded by default: other callers see the whole name.
    assert demangle_text(text).split(" ; ")[0].startswith(full[tokens[0]])


@pytest.mark.parametrize(
    ("addition_level", "itemised"),
    [(None, False), ("info", False), ("error", True)],
)
def test_additions_roll_up_unless_they_gate(
    addition_level: str | None, itemised: bool
) -> None:
    from abicheck.policy.severity import SeverityConfig, SeverityLevel

    config = (
        None
        if addition_level is None
        else SeverityConfig(addition=SeverityLevel(addition_level))
    )
    n = 60
    changes = [_change(ChangeKind.FUNC_ADDED, "add", i) for i in range(n)]
    text = to_markdown(
        DiffResult("1", "2", "libsvs.so", changes, Verdict.COMPATIBLE),
        severity_config=config,
    )
    rows = [line for line in text.splitlines() if line.startswith("- **func_added**")]
    if itemised:
        # A gating section names every finding, each on its own clipped row.
        assert len(rows) == n
        assert all(len(row) <= LINE_BUDGET for row in rows)
    else:
        assert rows == [rows[0]] and f"**func_added**: {n} findings" in rows[0]
