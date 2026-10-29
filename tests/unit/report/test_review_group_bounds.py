"""Related review groups in the Markdown report stay bounded as the surface grows.

Field report: a NO_CHANGE Markdown report was 566 KB, because every ``std::``
symbol became its own "Related review group" with lines up to 6 KB. The JSON
keeps every group; the Markdown now folds them into families by namespace root
and transition, shows counts and a few examples, caps line length and list
length, and stays bounded whatever the verdict.

The properties below are stated over N symbols (10, 1000, 5000) rather than one
input, so a regression to per-symbol output fails at the larger N.
"""

from __future__ import annotations

import json

import pytest

from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import DiffResult
from abicheck.model.change import Change
from abicheck.report.diff_report import to_json
from abicheck.report.dispatch_markdown import to_markdown

# The renderer's documented per-line cap, written as a literal so this test is
# an independent oracle rather than a re-read of the implementation's constant.
LINE_CAP = 1000
SECTION_BOUND = 8192  # bytes; the section is ~1.2 KB at every N today


def _name(i: int, pad: int = 0, namespace: str = "std") -> str:
    return (
        f"{namespace}::__cxx11::basic_string<char, std::char_traits<char>, "
        f"std::allocator<char>>::helper_{i}{'x' * pad}"
        "(std::__cxx11::basic_string<char> const&, std::map<int, std::pair<int, int>>)"
    )


def _changes(n: int, pad: int = 0, namespace: str = "std") -> list[Change]:
    out: list[Change] = []
    for i in range(n):
        name = _name(i, pad, namespace)
        # The mangled name must be unique across namespaces too: identical
        # symbols are one finding, so reusing `_ZSt0` for `boost` as well would
        # merge them before the renderer ever saw two groups.
        out.append(
            Change(
                ChangeKind.FUNC_ADDED,
                f"_Z{namespace}{i}",
                f"added {name}",
                qualified_name=name,
                demangled_symbol=name,
            )
        )
    return out


def _render(changes: list[Change], verdict: Verdict) -> str:
    return to_markdown(DiffResult("1", "2", "libstd.so", changes, verdict))


def _review_section(text: str) -> list[str]:
    lines = text.splitlines()
    start = lines.index("## Related review groups")
    end = next(
        (j for j in range(start + 1, len(lines)) if lines[j].startswith("## ")),
        len(lines),
    )
    return lines[start:end]


def _section_bytes(text: str) -> int:
    return sum(len(line.encode()) + 1 for line in _review_section(text))


@pytest.mark.parametrize("verdict", [Verdict.NO_CHANGE, Verdict.BREAKING])
def test_review_section_size_is_bounded_and_flat_in_the_symbol_count(
    verdict: Verdict,
) -> None:
    sizes = {n: _section_bytes(_render(_changes(n), verdict)) for n in (10, 1000, 5000)}
    assert max(sizes.values()) <= SECTION_BOUND, sizes
    # Only the counts' digits may differ between N=10 and N=5000.
    assert sizes[5000] - sizes[10] <= 128, sizes


@pytest.mark.parametrize("verdict", [Verdict.NO_CHANGE, Verdict.BREAKING])
@pytest.mark.parametrize("pad", [0, 6000])
def test_no_review_line_exceeds_the_cap_even_for_multi_kilobyte_names(
    verdict: Verdict, pad: int
) -> None:
    section = _review_section(_render(_changes(1000, pad), verdict))
    longest = max(len(line) for line in section)
    assert longest <= LINE_CAP, longest


def test_an_over_long_name_is_clipped_with_an_explicit_marker() -> None:
    section = "\n".join(_review_section(_render(_changes(3, 6000), Verdict.BREAKING)))
    assert "…" in section
    assert "x" * 6000 not in section


@pytest.mark.parametrize("n", [10, 1000, 5000])
def test_no_change_keeps_its_family_and_count_visible(n: int) -> None:
    """A NO_CHANGE run is folded like any other, not dropped: the family and its
    count stay on the page, so a reader can still see what was retained."""
    section = "\n".join(_review_section(_render(_changes(n), Verdict.NO_CHANGE)))
    assert f"### 1. `std` ({n} group(s), {n} member finding(s))" in section
    assert f"{n} review group(s) across 1 family" in section


def test_groups_collapse_into_families_with_exact_counts() -> None:
    changes = [
        *_changes(7, namespace="std"),
        *_changes(3, namespace="boost"),
        *_changes(2, namespace="plain_c_name"),
    ]
    section = "\n".join(_review_section(_render(changes, Verdict.BREAKING)))
    # Families sort by namespace root, so the order is alphabetical here.
    assert "### 1. `boost` (3 group(s), 3 member finding(s))" in section
    assert "### 2. `plain_c_name` (2 group(s), 2 member finding(s))" in section
    assert "### 3. `std` (7 group(s), 7 member finding(s))" in section


def test_family_list_is_capped_and_says_how_many_were_omitted() -> None:
    changes = [
        change
        for index in range(80)
        for change in _changes(1, namespace=f"ns{index:02d}")
    ]
    section = _review_section(_render(changes, Verdict.BREAKING))
    headings = [line for line in section if line.startswith("### ")]
    assert len(headings) == 50
    assert "… and 30 more families omitted; see the JSON `review_groups`." in section


def test_each_family_lists_a_few_examples_and_says_how_many_more() -> None:
    section = "\n".join(_review_section(_render(_changes(20), Verdict.BREAKING)))
    examples = [line for line in section.splitlines() if "**Examples:**" in line]
    assert len(examples) == 1
    assert examples[0].count("`") == 2 * 5  # five example names, each in backticks
    assert "… and 15 more" in examples[0]


@pytest.mark.parametrize("n", [10, 1000])
def test_json_keeps_every_group_with_its_members(n: int) -> None:
    result = DiffResult("1", "2", "libstd.so", _changes(n), Verdict.BREAKING)
    groups = json.loads(to_json(result))["review_groups"]
    assert len(groups) == n
    assert all(len(group["member_finding_ids"]) == 1 for group in groups)
    assert all(group["exact_symbols"] for group in groups)
