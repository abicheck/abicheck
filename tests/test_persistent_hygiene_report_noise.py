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
from abicheck.reporter import to_json, to_markdown

_STATES = (*CrossSourceEvolution, None)
_KINDS = (ChangeKind.EXPORTED_NOT_PUBLIC, ChangeKind.PUBLIC_NOT_EXPORTED)


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
    hygiene = _row(md, "Cross-source hygiene (standing, not changes)")
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
