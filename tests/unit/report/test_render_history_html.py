"""``project history -o html``: the page states exactly what the JSON records.

Oracles are the history's own JSON document (for completeness) and an
independent presence model (for the timeline's drawn spans), never the
renderer's helpers.
"""

from __future__ import annotations

import itertools
import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.report.render_history_html import (
    MAX_TIMELINE_ENTITIES,
    _presence_segments,
    render_history_html,
)
from abicheck.serialization import snapshot_to_json


def _events_for(presence: tuple[bool, ...]) -> list[dict[str, object]]:
    """The lifecycle events a presence vector implies (history's own rules)."""
    events: list[dict[str, object]] = []
    seen_removed = False
    for i, here in enumerate(presence):
        before = presence[i - 1] if i else False
        if i == 0 and here:
            kind = "first_observed"
        elif here and not before:
            kind = "reintroduced" if seen_removed else "introduced"
        elif before and not here:
            kind = "removed"
            seen_removed = True
        else:
            continue
        events.append({"event": kind, "index": i, "version": str(i)})
    return events


def _runs(presence: tuple[bool, ...]) -> list[tuple[int, int]]:
    """Independent oracle: maximal present runs, end = removal index or last."""
    runs, start = [], None
    for i, here in enumerate(presence):
        if here and start is None:
            start = i
        if not here and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(presence) - 1))
    return runs


@pytest.mark.parametrize(
    "presence",
    [p for n in range(2, 6) for p in itertools.product([True, False], repeat=n)],
)
def test_segments_match_presence_runs(presence: tuple[bool, ...]) -> None:
    segments = _presence_segments(_events_for(presence), len(presence) - 1)
    assert [(s, e) for s, e, _ in segments] == _runs(presence)


def _write(tmp: Path, name: str, version: str, funcs: list[str]) -> Path:
    snap = AbiSnapshot(
        library="libfoo.so",
        version=version,
        functions=[
            Function(name=f, mangled=f, return_type="int", visibility=Visibility.PUBLIC)
            for f in funcs
        ],
    )
    path = tmp / f"{name}.json"
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


CHAINS = {
    "add_remove_readd": [["a", "b"], ["a", "b", "c"], ["a", "c"], ["a", "b", "c"]],
    "no_change": [["a"], ["a"]],
    "removal_only": [["a", "b", "c"], ["a"]],
}


@pytest.mark.parametrize("chain", CHAINS)
def test_html_carries_every_json_event(tmp_path: Path, chain: str) -> None:
    paths = [
        str(_write(tmp_path, f"s{i}", f"{i}.0", funcs))
        for i, funcs in enumerate(CHAINS[chain])
    ]
    js, page = tmp_path / "h.json", tmp_path / "h.html"
    result = CliRunner().invoke(
        main, ["project", "history", *paths, "-o", f"json={js}", "-o", f"html={page}"]
    )
    assert result.exit_code == 0, result.output
    doc = json.loads(js.read_text(encoding="utf-8"))
    out = page.read_text(encoding="utf-8")

    events_section = out[out.index('id="events"') :]
    changes = [e for e in doc["events"] if e["event"] != "first_observed"]
    rows = re.findall(
        r"<tr><td>([^<]*)</td><td>([^<]*)</td><td>[^<]*</td><td><code>([^<]*)</code>",
        events_section,
    )
    label = {
        "introduced": "added",
        "reintroduced": "re-added",
        "deprecated": "deprecated",
        "removed": "removed",
    }
    assert sorted(rows) == sorted(
        (e["version"], label[e["event"]], e["display_name"]) for e in changes
    )

    first = sum(e["event"] == "first_observed" for e in doc["events"])
    assert f"{first} entities already present in the first release" in out
    for pair in doc["pairwise"]:
        assert f"<strong>{pair['verdict']}</strong>" in out
    if not changes:
        assert "<svg class='hist-svg'" not in out


def test_timeline_cap_is_disclosed_and_table_is_complete() -> None:
    n = MAX_TIMELINE_ENTITIES + 7
    history = {
        "library": "libx",
        "entries": [{"version": "1"}, {"version": "2"}],
        "events": [
            {
                "entity_key": f"k{i}",
                "display_name": f"f{i}",
                "entity_kind": "function",
                "event": "introduced",
                "version": "2",
                "index": 1,
            }
            for i in range(n)
        ],
        "coverage": {"gaps": []},
        "pairwise": [],
    }
    out = render_history_html(history)
    svg = out[
        out.index("<svg class='hist-svg'") : out.index(
            "</svg>", out.index("<svg class='hist-svg'")
        )
    ]
    assert svg.count("<g><title>") == MAX_TIMELINE_ENTITIES
    assert "7 more entities with lifecycle changes are not drawn" in out
    assert out.count("<td>added</td>") == n


def test_names_and_gaps_are_escaped_and_drawn() -> None:
    history = {
        "library": "<lib>",
        "entries": [{"version": "1"}, {"version": "3"}],
        "events": [
            {
                "entity_key": "k",
                "display_name": "<script>x</script>",
                "entity_kind": "type",
                "event": "removed",
                "version": "3",
                "index": 1,
                "evidence_uncertain": True,
            },
        ],
        "coverage": {
            "gaps": [
                {"from_version": "1", "to_version": "3", "detail": "version 2 missing"}
            ]
        },
        "pairwise": [],
    }
    out = render_history_html(history)
    assert "<script>x</script>" not in out
    assert "&lt;script&gt;" in out
    assert out.count("class='hist-gap'") == 1
    assert "Possible missing release between 1 and 3" in out
    assert "uncertain" in out
