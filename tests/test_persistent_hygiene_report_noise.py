"""Standing cross-source hygiene does not read as this change's own.

**Bug class:** a finding stamped as pre-existing inventory reads as a
change. A byte-identical MKL ``libmkl_rt.so.2`` self-compare (verdict
``NO_CHANGE``) headlined "Deployment risk changes | 43490" and emitted
43,490 single-member review groups labelled "Observed: modified", every one
a ``persistent`` ``exported_not_public``/``public_not_exported`` finding.

**General invariant:** for every mix of evolution states, the Markdown
headline's deployment-risk row counts only unstamped risk findings, the
hygiene row counts every stamped finding with the introduced subset, and
JSON review groups never contain a non-gating PERSISTENT finding while
still containing every other one. The oracle is the stamp on each input
``Change``, not the renderer's own helpers.
"""

from __future__ import annotations

import itertools
import json
import re

import pytest

from abicheck.checker_policy import ChangeKind, CrossSourceEvolution, Verdict
from abicheck.checker_types import DiffResult
from abicheck.model.change import Change
from abicheck.policy.severity import SeverityConfig, SeverityLevel
from abicheck.report.dispatch_markdown import to_markdown as markdown_for
from abicheck.reporter import to_json, to_markdown

_STATES = (*CrossSourceEvolution, None)
_KINDS = (ChangeKind.EXPORTED_NOT_PUBLIC, ChangeKind.PUBLIC_NOT_EXPORTED)
_HYGIENE_ROW = "Cross-source hygiene findings (not counted above)"


def _result(states: tuple[CrossSourceEvolution | None, ...]) -> DiffResult:
    changes = [
        Change(
            kind=_KINDS[i % 2],
            symbol=f"sym{i}",
            description="d",
            cross_source_evolution=state,
        )
        for i, state in enumerate(states)
    ]
    return DiffResult(
        old_version="1",
        new_version="2",
        library="libx.so",
        changes=changes,
        verdict=Verdict.COMPATIBLE_WITH_RISK,
    )


def _row(md: str, label: str) -> str | None:
    m = re.search(rf"^\| {re.escape(label)} \| (.+?) \|$", md, re.M)
    return m.group(1) if m else None


_MIXES = [
    combo
    for n in (1, 2, 3)
    for combo in itertools.combinations_with_replacement(_STATES, n)
]


@pytest.mark.parametrize("states", _MIXES, ids=lambda s: "-".join(map(str, s)))
def test_headline_separates_hygiene_from_risk(
    states: tuple[CrossSourceEvolution | None, ...],
) -> None:
    md = to_markdown(_result(states))
    stamped = [s for s in states if s is not None]
    unstamped = len(states) - len(stamped)
    introduced = stamped.count(CrossSourceEvolution.INTRODUCED)
    assert _row(md, "Deployment risk changes") == str(unstamped)
    hygiene = _row(md, _HYGIENE_ROW)
    if stamped:
        assert hygiene == f"{len(stamped)} ({introduced} introduced)"
    else:
        assert hygiene is None


@pytest.mark.parametrize("states", _MIXES, ids=lambda s: "-".join(map(str, s)))
def test_review_groups_omit_only_persistent_hygiene(
    states: tuple[CrossSourceEvolution | None, ...],
) -> None:
    doc = json.loads(to_json(_result(states)))
    grouped = {sym for g in doc["review_groups"] for sym in g.get("exact_symbols", ())}
    expected = {
        f"sym{i}" for i, s in enumerate(states) if s != CrossSourceEvolution.PERSISTENT
    }
    assert grouped == expected
    # Every finding is still recorded (ADR-067: record before disposing).
    assert len(doc["changes"]) == len(states)


_VERDICT_ROWS = (
    "Breaking changes",
    "Source-level breaks",
    "Deployment risk changes",
    "Compatible changes",
)


@pytest.mark.parametrize("state", list(CrossSourceEvolution))
def test_headline_rows_never_count_one_finding_twice(
    state: CrossSourceEvolution,
) -> None:
    """A stamped finding outside the risk bucket (``odr_type_variant`` is an
    API_BREAK by default) stays in its own verdict row and not in the
    hygiene row, so every finding is counted on exactly one row."""
    result = _result((state, None))
    result.changes.append(
        Change(
            kind=ChangeKind.ODR_TYPE_VARIANT,
            symbol="T",
            description="d",
            cross_source_evolution=state,
        )
    )
    md = to_markdown(result)
    rows = [int(_row(md, r) or 0) for r in _VERDICT_ROWS]
    hygiene = _row(md, _HYGIENE_ROW)
    assert rows[1] == 1  # the ODR finding, as a source-level break
    assert hygiene is not None and hygiene.startswith("1 ")
    assert sum(rows) + int(hygiene.split()[0]) == len(result.changes)


@pytest.mark.parametrize("gates", [True, False])
def test_surface_fallback_omits_what_review_groups_omit(gates: bool) -> None:
    """With no review groups left the Markdown falls back to the surface
    section; a non-gating persistent finding must not reappear there, and a
    gating one keeps its review group."""
    level = SeverityLevel.ERROR if gates else SeverityLevel.INFO
    config = SeverityConfig(potential_breaking=level)  # odr_type_variant's
    result = DiffResult(
        old_version="1",
        new_version="2",
        library="libx.so",
        changes=[
            Change(
                kind=ChangeKind.ODR_TYPE_VARIANT,
                symbol="PersistentOdrType",
                description="d",
                cross_source_evolution=CrossSourceEvolution.PERSISTENT,
            )
        ],
        verdict=Verdict.API_BREAK,
    )
    doc = json.loads(to_json(result, severity_config=config))
    md = markdown_for(result, severity_config=config)
    grouped = [s for g in doc["review_groups"] for s in g["exact_symbols"]]
    assert grouped == (["PersistentOdrType"] if gates else [])
    if not gates:
        assert doc["surface_changes"]["modifications"] == []
        surface = md.split("## Related review groups")[0]
        assert (
            "## What changed" not in surface
            or "PersistentOdrType" not in (surface.split("## What changed")[1])
        )
