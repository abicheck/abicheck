# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""One finding set, every renderer: each format's stated facts agree with JSON.

The formats are not hand-listed. The dispatch table in
:mod:`abicheck.frontends.render` (``_PROJECTIONS``, plus the ``oneline``
one-line summary) is the source of truth, and the CLI's own ``-o`` help text
must offer a subset of it. A format added to the dispatch table is therefore
parametrized automatically; until it has an invariant extractor below, its
cells fail with a message naming it.

The oracle is the JSON report of the same completed comparison, read through
:func:`_extract_json`. Every other format is read back out of its own text
(regex, XML, HTML text, or a Markdown token walk) and checked against it:

* every fact a format *states* equals the JSON value (verdict, detected,
  gating, non-gating, suppressed, excluded-by-scope, acknowledged);
* every gating finding a format itemises is a JSON gating finding, and an
  itemised gating list is complete (no format truncates one today);
* the per-group lists of the review digest and terminal output account for
  every retained group, with an explicit omission count when truncated;
* the disposition counts every format is expected to state are present
  (the mixed scenario), with a strict xfail cell wherever a format drops one;
* a gating symbol a format itemises is visible to a reader of that format,
  so a Markdown renderer that hides a name inside a raw-HTML tag or a broken
  code span is caught.

Scenarios are built through the public :func:`abicheck.checker.compare` path
with real suppression, acknowledgment and public-surface-scope inputs.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

import pytest
from click.testing import CliRunner
from markdown_it import MarkdownIt

from abicheck.checker import compare
from abicheck.checker_types import DiffResult
from abicheck.frontends.render import _PROJECTIONS, ONELINE_FORMAT, render_output
from abicheck.model import AbiSnapshot
from abicheck.model.declarations import Function, Param
from abicheck.policy.acknowledgment import AcknowledgmentList
from abicheck.policy.suppression.rules import Suppression, SuppressionList

LIB = "libfoo.so.1"

#: Every ``render_output`` format: the dispatch table plus the one-line summary.
DISPATCH_FORMATS: frozenset[str] = frozenset(_PROJECTIONS) | {ONELINE_FORMAT}

#: Declared aliases: a second dispatch name routed to another format's projection.
#: ``test_every_shared_projection_is_a_declared_alias`` fails on an undeclared one.
ALIASES: dict[str, str] = {"md": "markdown"}

#: Names with a demangled-looking spelling and every Markdown-special character
#: a symbol can carry. Only the first two are gating in the mixed scenario.
WEIRD = 'weird<name>&"`*_x'
UNICODE = "naïve_函数_ü"
PIPE = "tbl|cell\\x"


def _fn(name: str, ret: str = "int") -> Function:
    return Function(
        name=name, mangled=name, return_type=ret, params=[Param(name="a", type="int")]
    )


def _snapshot(version: str, functions: list[Function]) -> AbiSnapshot:
    return AbiSnapshot(
        version=version,
        library=LIB,
        functions=functions,
        variables=[],
        types=[],
        enums=[],
    )


# ---------------------------------------------------------------------------
# Scenarios: finding sets built through the public compare() path.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    name: str
    result: DiffResult
    old: AbiSnapshot
    new: AbiSnapshot


def _mixed_snapshots(bulk: int = 0) -> tuple[AbiSnapshot, AbiSnapshot, set[str]]:
    old = [
        _fn("keep"),
        _fn("removed_a"),
        _fn("removed_sup"),
        _fn("ret_change"),
        _fn("ack_removed"),
        _fn("scoped_out"),  # removed, but not in the public surface: scope-excluded
        _fn(WEIRD),
        _fn(UNICODE),
        _fn(PIPE),
    ]
    new = [
        _fn("keep"),
        _fn("ret_change", ret="long"),
        _fn("added_ok"),
        _fn("added_ack"),
        _fn("new_out"),
        _fn(UNICODE, ret="long"),
    ]
    old += [_fn(f"bulk_removed_{i:03d}") for i in range(bulk)]
    surface = {f.name for f in old + new} - {"scoped_out"}
    return _snapshot("1.0", old), _snapshot("2.0", new), surface


def _write_acks(directory: Path) -> AcknowledgmentList:
    path = directory / "acks.yml"
    path.write_text(
        "version: 1\n"
        "acknowledgments:\n"
        "  - symbol: ack_removed\n"
        "    change_kind: func_removed\n"
        "    reason: 'scenario acknowledgment'\n"
        "  - symbol: added_ack\n"
        "    change_kind: func_added\n"
        "    reason: 'scenario addition acknowledgment'\n"
    )
    return AcknowledgmentList.load(path)


def _build_scenarios(directory: Path) -> dict[str, Scenario]:
    acks = _write_acks(directory)
    scenarios: dict[str, Scenario] = {}

    old, new, surface = _mixed_snapshots()
    mixed = compare(
        old,
        new,
        suppression=SuppressionList(
            [Suppression(symbol_pattern="removed_sup", reason="suppressed in scenario")]
        ),
        acknowledgments=acks,
        public_surface_allowlist=surface,
    )
    scenarios["mixed"] = Scenario("mixed", mixed, old, new)

    old, new, surface = _mixed_snapshots(bulk=300)
    bulk = compare(
        old,
        new,
        suppression=SuppressionList(
            [Suppression(symbol_pattern="removed_sup", reason="suppressed in scenario")]
        ),
        acknowledgments=acks,
        public_surface_allowlist=surface,
    )
    scenarios["bulk"] = Scenario("bulk", bulk, old, new)

    old, new, _ = _mixed_snapshots()
    everything = compare(
        old,
        new,
        suppression=SuppressionList(
            [Suppression(symbol_pattern=".*", reason="suppress all")]
        ),
    )
    scenarios["suppressed_all"] = Scenario("suppressed_all", everything, old, new)

    same = _snapshot("1.0", [_fn("keep"), _fn("also_keep")])
    clean = compare(same, _snapshot("2.0", [_fn("keep"), _fn("also_keep")]))
    scenarios["clean"] = Scenario("clean", clean, same, same)
    return scenarios


# ---------------------------------------------------------------------------
# Stated facts: what one rendering says, in a common vocabulary.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Stated:
    """The facts one rendering states. ``None`` means the format does not state it."""

    verdict: str | None = None
    detected: int | None = None
    gating: int | None = None
    non_gating: int | None = None
    suppressed: int | None = None
    excluded: int | None = None
    acknowledged: int | None = None
    #: Gating findings the format itemises by symbol; ``None`` if it does not.
    listed_gating: frozenset[str] | None = None
    #: Gating rows the format states it left out of ``listed_gating``.
    #: Per-group rows the format itemises (review digest, terminal); ``None`` if none.
    group_rows: int | None = None
    #: Group rows the format states it left out.
    group_omitted: int | None = None
    #: Text a reader of this format actually sees, for presence checks.
    visible: str = ""


#: Every scalar fact. A rendering that states one must state the JSON value.
SCALARS = (
    "verdict",
    "detected",
    "gating",
    "non_gating",
    "suppressed",
    "excluded",
    "acknowledged",
)
#: The disposition counts each format is expected to state (checked per cell).
DISPOSITION_FIELDS = (
    "detected",
    "gating",
    "non_gating",
    "suppressed",
    "excluded",
    "acknowledged",
)


def _int(pattern: str, text: str) -> int | None:
    match = re.search(pattern, text)
    return int(match.group(1)) if match else None


def _first(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text)
    return match.group(1) if match else None


def _json_strings(node: object) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in _json_strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in _json_strings(v)]
    return []


def _extract_json(text: str) -> Stated:
    """The oracle. Gating is a retained change whose JSON severity is ``breaking``.

    The oracle checks itself too (``test_oracle_reconciles_with_its_own_counts``):
    if this derivation ever disagreed with ``disposition_audit.counts.gating``,
    that test fails before any renderer is consulted.
    """
    doc = json.loads(text)
    audit = doc["disposition_audit"]
    counts = audit["counts"]
    gating = frozenset(
        c["symbol"] for c in doc["changes"] if c["severity"] == "breaking"
    )
    return Stated(
        verdict=doc["verdict"],
        detected=audit["detected_total"],
        gating=counts["gating"],
        non_gating=counts["non_gating"],
        suppressed=counts["suppressed"],
        excluded=counts["out_of_contract"],
        acknowledged=audit["acknowledged_total"],
        listed_gating=gating,
        group_rows=len(doc["review_groups"]),
        visible="\n".join(_json_strings(doc)),
    )


def _extract_sarif(text: str) -> Stated:
    doc = json.loads(text)
    run = doc["runs"][0]
    props = run["properties"]
    audit = props["dispositionAudit"]
    counts = audit["counts"]
    gating = frozenset(
        r["properties"]["symbol"]
        for r in run["results"]
        if r["level"] == "error" and not r.get("suppressions")
    )
    return Stated(
        verdict=props["abiVerdict"],
        detected=audit["detected_total"],
        gating=counts["gating"],
        non_gating=counts["non_gating"],
        suppressed=counts["suppressed"],
        excluded=counts["out_of_contract"],
        acknowledged=audit["acknowledged_total"],
        listed_gating=gating,
        visible="\n".join(_json_strings(doc)),
    )


def _extract_junit(text: str) -> Stated:
    root = ET.fromstring(text)
    suite = root.find("testsuite")
    assert suite is not None, "JUnit output has no testsuite"
    props = {p.get("name"): p.get("value") for p in suite.iter("property")}
    cases = suite.findall("testcase")
    failing = [c for c in cases if c.find("failure") is not None]
    failure_types = {f.get("type") for c in failing for f in c.findall("failure")}
    strings: list[str] = []
    for el in root.iter():
        strings.extend(v for v in el.attrib.values())
        strings.extend(t for t in (el.text, el.tail) if t)
    return Stated(
        verdict=next(iter(failure_types)) if len(failure_types) == 1 else None,
        detected=int(props["abicheck.detected_total"]),
        gating=int(props["abicheck.disposition.gating"]),
        non_gating=int(props["abicheck.disposition.non_gating"]),
        suppressed=int(props["abicheck.disposition.suppressed"]),
        excluded=int(props["abicheck.disposition.out_of_contract"]),
        acknowledged=None,
        listed_gating=frozenset(c.get("name") or "" for c in failing),
        visible="\n".join(strings),
    )


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _html_text(text: str) -> str:
    parser = _Text()
    parser.feed(text)
    parser.close()
    return " ".join(" ".join(parser.parts).split())


def _extract_html(text: str) -> Stated:
    """HTML's disposition line (``🔎 10 detected · 5 gating · 3 non gating, ...``).

    The line omits zero-valued items (``0 gating`` and ``non gating`` are absent
    when zero), so an absent label reads as 0 here. That is still checked: a
    dropped nonzero count reads as 0 and disagrees with a JSON oracle that
    says otherwise, and the mixed scenario requires every count to be stated.
    """
    visible = _html_text(text)
    start = visible.find("🔎")
    assert start >= 0, "HTML states no disposition line"
    end = visible.find(" detector", start)
    pieces = visible[start:end].split(" · ")[:-1]  # last piece is the detector count
    items: dict[str, int] = {}
    for piece in pieces:
        for part in piece.split(", "):
            match = re.search(
                r"(\d+) (detected|gating|non gating|suppressed|out of contract)$",
                part.strip(),
            )
            assert match is not None, f"unrecognised HTML disposition item: {part!r}"
            items[match.group(2)] = int(match.group(1))
    return Stated(
        verdict=_first(r"Compatibility: ([A-Z_]+)", visible),
        detected=items.get("detected", 0),
        gating=items.get("gating", 0),
        non_gating=items.get("non gating", 0),
        suppressed=items.get("suppressed", 0),
        excluded=items.get("out of contract", 0),
        acknowledged=None,
        visible=visible,
    )


_MD = MarkdownIt("commonmark").enable("table")


def _markdown_visible(text: str) -> str:
    """What a CommonMark reader sees: raw inline HTML is dropped, code spans kept.

    Mirrors how a GitHub-rendered job summary or PR comment shows the text: a
    ``<tag>``-shaped span is passed through as markup (so its text is not shown),
    and a backtick inside a code span closes it early exactly as CommonMark says.
    """
    parts: list[str] = []
    for token in _MD.parse(text):
        if token.children is not None:
            parts.append(
                "".join(
                    c.content
                    for c in token.children
                    if c.type in ("text", "code_inline")
                )
            )
            parts.append("\n")
    return "".join(parts)


def _disposition_table(text: str) -> dict[str, int | None]:
    return {
        "detected": _int(r"\| Detected \(raw\) \| (\d+) \|", text),
        "gating": _int(r"\| Effective \(gating\) \| (\d+) \|", text),
        "non_gating": _int(r"\| … non gating \| (\d+) \|", text),
        "suppressed": _int(r"\| … suppressed \| (\d+) \|", text),
        "excluded": _int(r"\| … out of contract \| (\d+) \|", text),
        "acknowledged": _int(r"\*\*Acknowledged:\*\* (\d+)", text),
    }


def _markdown_breaking_symbols(text: str) -> frozenset[str]:
    start = text.find("## ❌ Breaking Changes")
    if start < 0:
        return frozenset()
    end = text.find("\n## ", start + 1)
    section = text[start : end if end > 0 else len(text)]
    symbols = set()
    for line in section.splitlines():
        if not line.startswith("- **"):
            continue
        # "- **kind**: <description> (`<old> → <new>` or `<symbol>`)": the
        # description's last ": " separates the kind text from the symbol.
        body = line.split("**: ", 1)[1]
        head = body.split(" (`", 1)[0]
        assert ": " in head, f"Breaking line has no description: {line!r}"
        symbols.add(head.rsplit(": ", 1)[1])
    return frozenset(symbols)


def _extract_markdown(text: str) -> Stated:
    table = _disposition_table(text)
    return Stated(
        verdict=_first(r"\| \*\*Verdict\*\* \| [^`]*`([A-Z_]+)`", text),
        listed_gating=_markdown_breaking_symbols(text),
        visible=_markdown_visible(text),
        **table,
    )


def _extract_review(text: str) -> Stated:
    counts = re.search(
        r"\*\*Count populations:\*\* (\d+) detected; \d+ retained \((\d+) gating, (\d+) non-gating\)",
        text,
    )
    assert counts is not None, "review digest states no count populations line"
    table = _disposition_table(text)
    rows = re.findall(r"^\d+\. \*\*(.+?)\*\* — ", text, re.M)
    return Stated(
        verdict=_first(r"\*\*Verdict:\*\* [^`]*`([A-Z_]+)`", text),
        detected=int(counts.group(1)),
        gating=int(counts.group(2)),
        non_gating=int(counts.group(3)),
        suppressed=table["suppressed"],
        excluded=table["excluded"],
        acknowledged=table["acknowledged"],
        group_rows=len(rows),
        group_omitted=_int(r"… (\d+) more group\(s\) omitted", text),
        visible=_markdown_visible(text),
    )


def _extract_terminal(text: str) -> Stated:
    counts = re.search(
        r"Findings: (\d+) detected; \d+ retained \((\d+) gating, (\d+) non-gating\)",
        text,
    )
    audit = re.search(
        r"Audit: \d+ detected; \d+ gating; (\d+) suppressed; \d+ rule\(s\) applied; (\d+) scope-excluded",
        text,
    )
    assert counts is not None and audit is not None, (
        "terminal output states no findings/audit line"
    )
    rows = re.findall(r"^\d+\. (.+): \w+$", text, re.M)
    return Stated(
        verdict=_first(r"Compatibility: ([A-Z_]+)", text),
        detected=int(counts.group(1)),
        gating=int(counts.group(2)),
        non_gating=int(counts.group(3)),
        suppressed=int(audit.group(1)),
        excluded=int(audit.group(2)),
        group_rows=len(rows),
        group_omitted=_int(r"\.\.\. (\d+) more groups? omitted", text),
        visible=text,
    )


def _extract_oneline(text: str) -> Stated:
    return Stated(
        verdict=_first(r"^([A-Z_]+):", text),
        detected=_int(r"\[audit: (\d+) detected", text),
        gating=_int(r"(\d+) gating,", text),
        non_gating=_int(r"(\d+) non_gating", text),
        suppressed=_int(r"(\d+) suppressed", text),
        excluded=_int(r"(\d+) out_of_contract", text),
        acknowledged=_int(r"(\d+) acknowledged,", text),
        visible=text,
    )


#: One invariant extractor per dispatch format. Adding a format to
#: ``_PROJECTIONS`` without an entry here fails
#: ``test_every_dispatch_format_has_an_invariant_extractor``.
EXTRACTORS: dict[str, Callable[[str], Stated]] = {
    "json": _extract_json,
    "sarif": _extract_sarif,
    "junit": _extract_junit,
    "html": _extract_html,
    "markdown": _extract_markdown,
    "md": _extract_markdown,
    "review": _extract_review,
    "terminal": _extract_terminal,
    ONELINE_FORMAT: _extract_oneline,
}

#: Formats whose text shows finding names a reader can check for presence.
#: The one-line summary deliberately carries counts only.
SHOWS_SYMBOLS: frozenset[str] = DISPATCH_FORMATS - {ONELINE_FORMAT}

#: Cells that are known-defective. Each is strict: once the defect is fixed the
#: XPASS fails the test and the entry must be removed in the same change.
KNOWN_DISPOSITION_GAPS: dict[tuple[str, str], str] = {
    ("junit", "acknowledged"): (
        "JUnit publishes the disposition counts as properties but not the "
        "acknowledgment overlay (acknowledged_total), so a CI reader of the "
        "XML cannot see that some failing breaks were acknowledged (they keep "
        "their verdict class and gate contribution, ADR-067 D5)."
    ),
    ("html", "acknowledged"): (
        "The HTML disposition line states detected/gating/non-gating/suppressed/"
        "out-of-contract but omits the acknowledged count that Markdown and the "
        "review digest state (ADR-067 D5)."
    ),
    ("terminal", "acknowledged"): (
        "The terminal Audit line omits the acknowledged count that Markdown, the "
        "review digest and the one-line summary state, so an acknowledged break "
        "is invisible to a terminal reader (ADR-067 D5)."
    ),
}

#: Verified against a CommonMark parser (``_markdown_visible``): the symbol
#: ``weird<name>&"`*_x`` reads back as ``weird&"*_x``. Two causes, both unescaped
#: values: the raw ``<name>`` in prose is inline HTML and is dropped, and a
#: backtick inside the symbol closes the code span that wraps it. The same
#: happens in the Markdown "Examples" line. The fix (escape prose and pick a
#: code-span fence longer than any backtick run) changes rendered bytes for
#: every template-named symbol, so it is recorded here rather than made under
#: this harness; it needs its own golden-output update.
KNOWN_RENDER_DEFECTS: dict[tuple[str, str], str] = {
    **{
        (fmt, WEIRD): (
            "Markdown shows the symbol as 'weird&\"*_x': the raw '<name>' in the "
            "Breaking Changes prose is inline HTML and is dropped, and the "
            "backtick in the symbol closes the code span that wraps it. Values "
            "are not escaped for prose or code spans (only table cells are)."
        )
        for fmt in ("markdown", "md")
    },
    ("review", WEIRD): (
        "The review digest prints the symbol bold, '**weird<name>&\"`*_x**'. "
        "The raw '<name>' is inline HTML and is dropped by a CommonMark reader, "
        "so the name reads back as 'weird&\"*_x'."
    ),
}


# ---------------------------------------------------------------------------
# Fixtures: render every scenario through every dispatch format, once.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def scenarios(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Scenario]:
    return _build_scenarios(tmp_path_factory.mktemp("renderer_parity"))


@pytest.fixture(scope="module")
def rendered(scenarios: dict[str, Scenario]) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for name, scenario in scenarios.items():
        for fmt in sorted(DISPATCH_FORMATS):
            out[(name, fmt)] = render_output(
                fmt, scenario.result, scenario.old, scenario.new
            )
    return out


def _extractor(fmt: str) -> Callable[[str], Stated]:
    assert fmt in EXTRACTORS, (
        f"dispatch format {fmt!r} has no invariant extractor: add one to "
        "EXTRACTORS in tests/unit/report/test_renderer_parity_matrix.py"
    )
    return EXTRACTORS[fmt]


def _oracle(rendered: dict[tuple[str, str], str], scenario: str) -> Stated:
    return _extract_json(rendered[(scenario, "json")])


def _stated(rendered: dict[tuple[str, str], str], scenario: str, fmt: str) -> Stated:
    return _extractor(fmt)(rendered[(scenario, fmt)])


# ---------------------------------------------------------------------------
# Enrollment: the formats under test come from the source of truth.
# ---------------------------------------------------------------------------


def test_every_dispatch_format_has_an_invariant_extractor() -> None:
    missing = DISPATCH_FORMATS - set(EXTRACTORS)
    stale = set(EXTRACTORS) - DISPATCH_FORMATS
    assert not missing, (
        f"dispatch formats without an invariant extractor: {sorted(missing)}"
    )
    assert not stale, (
        f"extractors for formats the dispatch table no longer has: {sorted(stale)}"
    )


def test_cli_offers_only_dispatch_formats() -> None:
    from abicheck.cli import main

    output = (
        CliRunner().invoke(main, ["compare", "--help"], terminal_width=100_000).output
    )
    match = re.search(r"FORMAT is one of ([a-z/]+);", output)
    assert match is not None, "compare --help no longer states its FORMAT choices"
    cli_formats = frozenset(match.group(1).split("/"))
    assert cli_formats <= DISPATCH_FORMATS, (
        f"CLI offers unrouted formats: {sorted(cli_formats - DISPATCH_FORMATS)}"
    )
    not_offered = DISPATCH_FORMATS - cli_formats
    assert not_offered <= set(ALIASES), (
        f"dispatch formats the CLI does not offer and that are not aliases: {sorted(not_offered - set(ALIASES))}"
    )


def test_every_shared_projection_is_a_declared_alias() -> None:
    by_projection: dict[int, list[str]] = {}
    for fmt, projection in _PROJECTIONS.items():
        by_projection.setdefault(id(projection), []).append(fmt)
    for names in by_projection.values():
        if len(names) > 1:
            undeclared = [
                n for n in names if n not in ALIASES and n not in ALIASES.values()
            ]
            assert not undeclared, (
                f"formats sharing a projection without a declared alias: {sorted(names)}"
            )


@pytest.mark.parametrize(("alias", "target"), sorted(ALIASES.items()))
def test_alias_renders_byte_identically_to_its_target(
    alias: str, target: str, rendered: dict[tuple[str, str], str]
) -> None:
    assert _PROJECTIONS[alias] is _PROJECTIONS[target]
    for scenario in ("mixed", "bulk", "suppressed_all", "clean"):
        assert rendered[(scenario, alias)] == rendered[(scenario, target)]


# ---------------------------------------------------------------------------
# Oracle self-check and the per-format invariants.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", ["mixed", "bulk", "suppressed_all", "clean"])
def test_oracle_reconciles_with_its_own_counts(
    scenario: str, rendered: dict[tuple[str, str], str]
) -> None:
    oracle = _oracle(rendered, scenario)
    assert oracle.gating is not None and oracle.gating == len(
        oracle.listed_gating or ()
    ), "JSON gating symbols do not match disposition_audit.counts.gating"
    assert oracle.detected == (
        oracle.gating
        + (oracle.non_gating or 0)
        + (oracle.suppressed or 0)
        + (oracle.excluded or 0)
    ), "JSON detected_total is not the sum of its dispositions"


@pytest.mark.parametrize("fmt", sorted(DISPATCH_FORMATS))
@pytest.mark.parametrize("scenario", ["mixed", "bulk", "suppressed_all", "clean"])
def test_format_agrees_with_json_oracle(
    scenario: str, fmt: str, rendered: dict[tuple[str, str], str]
) -> None:
    oracle = _oracle(rendered, scenario)
    stated = _stated(rendered, scenario, fmt)

    for name in SCALARS:
        got = getattr(stated, name)
        if got is not None:
            assert got == getattr(oracle, name), (
                f"{fmt} states {name}={got!r}; JSON says {getattr(oracle, name)!r}"
            )

    assert stated.gating is not None, f"{fmt} states no gating count"
    assert stated.detected is not None, f"{fmt} states no detected count"
    if oracle.gating:
        assert stated.verdict is not None, (
            f"{fmt} states gating findings but no verdict"
        )

    if stated.listed_gating is not None:
        assert stated.listed_gating <= oracle.listed_gating, (
            f"{fmt} lists as gating what JSON does not: {sorted(stated.listed_gating - oracle.listed_gating)}"
        )
        assert len(stated.listed_gating) == stated.gating, (
            f"{fmt} itemises {len(stated.listed_gating)} gating finding(s) of "
            f"{stated.gating}; a format that truncates this list needs an "
            "omission count here and an extractor that reads it"
        )

    if stated.group_rows is not None:
        assert stated.group_rows + (stated.group_omitted or 0) == oracle.group_rows, (
            f"{fmt} shows {stated.group_rows} group row(s) and states "
            f"{stated.group_omitted or 0} omitted; JSON has {oracle.group_rows} review group(s)"
        )


@pytest.mark.parametrize(
    ("fmt", "field"),
    [
        pytest.param(
            fmt,
            field,
            id=f"{fmt}-{field}",
            marks=(
                pytest.mark.xfail(
                    strict=True, reason=KNOWN_DISPOSITION_GAPS[(fmt, field)]
                )
                if (fmt, field) in KNOWN_DISPOSITION_GAPS
                else ()
            ),
        )
        for fmt in sorted(DISPATCH_FORMATS)
        for field in DISPOSITION_FIELDS
    ],
)
def test_format_states_each_disposition_count(
    fmt: str, field: str, rendered: dict[tuple[str, str], str]
) -> None:
    """The mixed scenario exercises every disposition, so every count is nonzero.

    Asserting a format *states* each one (not merely that a stated value
    agrees) is what catches a count dropped from a projection.
    """
    stated = _stated(rendered, "mixed", fmt)
    oracle = _oracle(rendered, "mixed")
    assert getattr(oracle, field) not in (None, 0), (
        "mixed scenario must exercise this disposition"
    )
    assert getattr(stated, field) == getattr(oracle, field), (
        f"{fmt} does not state {field}={getattr(oracle, field)}"
    )


@pytest.mark.parametrize(
    ("fmt", "symbol"),
    [
        pytest.param(
            fmt,
            symbol,
            id=f"{fmt}-{symbol}",
            marks=(
                pytest.mark.xfail(
                    strict=True, reason=KNOWN_RENDER_DEFECTS[(fmt, symbol)]
                )
                if (fmt, symbol) in KNOWN_RENDER_DEFECTS
                else ()
            ),
        )
        for fmt in sorted(SHOWS_SYMBOLS)
        for symbol in (WEIRD, UNICODE, PIPE, "removed_a", "ack_removed")
    ],
)
def test_format_shows_gating_symbol_intact(
    fmt: str, symbol: str, rendered: dict[tuple[str, str], str]
) -> None:
    """A gating symbol a format shows must read back as the same name."""
    oracle = _oracle(rendered, "mixed")
    assert symbol in oracle.listed_gating
    stated = _stated(rendered, "mixed", fmt)
    assert symbol in stated.visible, (
        f"{fmt} does not show {symbol!r} intact to a reader"
    )


def test_truncated_review_and_terminal_lists_say_how_many_were_omitted(
    rendered: dict[tuple[str, str], str],
) -> None:
    """The bulk scenario overflows the group lists; both must state the remainder."""
    oracle = _oracle(rendered, "bulk")
    assert oracle.group_rows > 8, "bulk scenario must overflow the group list"
    for fmt in ("review", "terminal"):
        stated = _stated(rendered, "bulk", fmt)
        assert stated.group_omitted and stated.group_omitted > 0, (
            f"{fmt} truncated without stating it"
        )
        assert stated.group_rows + stated.group_omitted == oracle.group_rows


def test_untruncated_formats_list_every_gating_finding(
    rendered: dict[tuple[str, str], str],
) -> None:
    """The bulk scenario is 305 gating breaks; these formats must itemise all of them."""
    oracle = _oracle(rendered, "bulk")
    assert oracle.gating is not None and oracle.gating > 300, (
        "bulk scenario must overflow a 300-row cap"
    )
    for fmt in ("markdown", "md", "sarif", "junit"):
        stated = _stated(rendered, "bulk", fmt)
        assert stated.listed_gating == oracle.listed_gating, (
            f"{fmt} dropped gating findings"
        )


def test_scope_excluded_and_suppressed_are_not_listed_as_gating(
    rendered: dict[tuple[str, str], str],
) -> None:
    oracle = _oracle(rendered, "mixed")
    assert "removed_sup" not in oracle.listed_gating
    assert "scoped_out" not in oracle.listed_gating
    for fmt in ("markdown", "md", "sarif", "junit"):
        stated = _stated(rendered, "mixed", fmt)
        assert stated.listed_gating is not None
        assert not {"removed_sup", "scoped_out"} & stated.listed_gating, fmt


def test_fully_suppressed_run_does_not_read_as_clean(
    rendered: dict[tuple[str, str], str],
) -> None:
    """Zero gating findings, but the suppressed population is still stated everywhere it is shown."""
    oracle = _oracle(rendered, "suppressed_all")
    assert oracle.gating == 0 and (oracle.suppressed or 0) > 0
    for fmt in sorted(DISPATCH_FORMATS):
        stated = _stated(rendered, "suppressed_all", fmt)
        assert stated.gating == 0, fmt
        if stated.suppressed is not None:
            assert stated.suppressed == oracle.suppressed, fmt
