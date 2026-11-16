"""The default-configuration expectations are unchanged by the observable_at floor (plan B1).

``tests/data/default_cell_expectations.json`` is the expectation of every default
(operator, seed) cell as ``main`` computed it before the floor became per-kind
(``abiforge.model.expect.observability_floor``). At the default headers
configuration every classified kind is observed, so the two must agree
cell for cell. A difference means a kind's ``observable_at`` now hides or
exposes an edit the default lane used to require: review it, then regenerate
the file from main with the same dump, never by editing it to match.
"""

from __future__ import annotations

import json
from pathlib import Path

from abiforge.model.config import Toolchain, default_config
from abiforge.operators import matrix

DATA = Path(__file__).resolve().parent / "data" / "default_cell_expectations.json"
PRODUCER = Toolchain("gcc", 13, "gcc-13", "g++-13")


def _current() -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for op, seed in matrix():
        config = default_config(seed.lang, PRODUCER)
        if op.requires(config) is not None or not op.applies_to(seed):
            continue
        exp = op.expect(config)
        out[f"{op.name}|{seed.name}"] = {
            "expected": sorted(exp.expected),
            "any_of": sorted(sorted(g) for g in exp.any_of),
            "verdict_min": exp.verdict_min,
            "verdict_max": exp.verdict_max,
            "observable": exp.observable,
        }
    return out


def test_default_cell_expectations_match_main_before_the_floor_change() -> None:
    recorded = json.loads(DATA.read_text(encoding="utf-8"))
    current = _current()
    assert current.keys() == recorded.keys()
    differing = sorted(k for k in recorded if current[k] != recorded[k])
    assert not differing, f"default expectations changed: {differing[:10]}"
