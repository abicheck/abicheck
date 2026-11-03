"""Every BREAKING / API_BREAK catalog label names an independent witness.

case122 was labelled COMPATIBLE_WITH_RISK -- a label written from the
tool's own output -- until an independent consumer compile proved
API_BREAK. A break label is only trustworthy when something other than
abicheck observed the break. Two witnesses exist:

* ``source_smoke`` -- ``tests/validate_examples.py`` compiles (or links /
  runs) a consumer against v1 and v2; it witnesses a break when v1 is
  expected to succeed and v2 to fail.
* the runtime smoke -- ``run_example_runtime_smoke.py`` builds the case's
  ``app.*`` against v1 and runs it against v2. It is an ABI witness only,
  so it counts for BREAKING, never for API_BREAK, and only when that runner
  would really execute the case on the Linux CI lane: its own
  ``_skip_reason`` passes, the case's CMakeLists declares ``APP_SOURCES``,
  and no ``requires_feature`` toolchain probe makes execution conditional.
  (The lane records whether the swap was DEMONSTRATED; this static gate
  only proves the case is executed, not the outcome.)

Everything else is UNWITNESSED. ``catalog/unwitnessed_break_labels.json``
records the current UNWITNESSED cases with their label; it may only
shrink. A new unwitnessed break label fails, and so does changing a
baselined case's label without adding a witness.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.repo_scan

_ROOT = Path(__file__).resolve().parents[1]
_GROUND_TRUTH = _ROOT / "catalog" / "ground_truth.json"
_BASELINE = _ROOT / "catalog" / "unwitnessed_break_labels.json"
_RUNNER = (
    _ROOT / "skills-src/evaluation/validation/scripts/run_example_runtime_smoke.py"
)
_BREAK_LABELS = frozenset({"BREAKING", "API_BREAK"})


def _load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_runtime_smoke_runner", _RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # The CI runtime lane is Linux; classify against that lane, not the host.
    module._platform = lambda: "linux"  # type: ignore[attr-defined]
    return module


def _source_smoke_witnesses(entry: dict) -> bool:
    smoke = entry.get("source_smoke")
    if not isinstance(smoke, dict):
        return False
    v1 = smoke.get("v1", {}).get("expect", "success")
    v2 = smoke.get("v2", {}).get("expect", "success")
    return v1 == "success" and v2 == "failure"


def _runtime_executes(runner: ModuleType, name: str, entry: dict) -> bool:
    if entry.get("requires_feature"):
        return False
    if runner._skip_reason(name, entry) is not None:
        return False
    cmake = runner.example_catalog.case_dir(name) / "CMakeLists.txt"
    return "APP_SOURCES" in cmake.read_text(encoding="utf-8")


def classify(verdicts: dict[str, dict], runner: ModuleType) -> dict[str, str]:
    """Map each BREAKING/API_BREAK case to its witness class."""
    out: dict[str, str] = {}
    for name, entry in sorted(verdicts.items()):
        label = entry.get("expected")
        if label not in _BREAK_LABELS:
            continue
        if _source_smoke_witnesses(entry):
            out[name] = "source_smoke"
        elif label == "BREAKING" and _runtime_executes(runner, name, entry):
            out[name] = "runtime_smoke"
        else:
            out[name] = "UNWITNESSED"
    return out


@pytest.fixture(scope="module")
def verdicts() -> dict[str, dict]:
    return json.loads(_GROUND_TRUTH.read_text(encoding="utf-8"))["verdicts"]


@pytest.fixture(scope="module")
def classes(verdicts: dict[str, dict]) -> dict[str, str]:
    return classify(verdicts, _load_runner())


@pytest.fixture(scope="module")
def baseline() -> dict[str, str]:
    return json.loads(_BASELINE.read_text(encoding="utf-8"))["unwitnessed"]


def test_no_new_unwitnessed_break_label(
    classes: dict[str, str], verdicts: dict[str, dict], baseline: dict[str, str]
) -> None:
    new = sorted(
        n for n, c in classes.items() if c == "UNWITNESSED" and n not in baseline
    )
    assert not new, (
        "BREAKING/API_BREAK label without an independent witness (add a "
        f"source_smoke or a runtime app, see catalog/CLAUDE.md): {new}"
    )


def test_baselined_label_does_not_change_without_witness(
    classes: dict[str, str], verdicts: dict[str, dict], baseline: dict[str, str]
) -> None:
    changed = sorted(
        f"{n}: {old} -> {verdicts.get(n, {}).get('expected')}"
        for n, old in baseline.items()
        if classes.get(n) == "UNWITNESSED" and verdicts[n]["expected"] != old
    )
    assert not changed, f"unwitnessed label changed: {changed}"


def test_baseline_only_shrinks(
    classes: dict[str, str], baseline: dict[str, str]
) -> None:
    stale = sorted(n for n in baseline if classes.get(n) != "UNWITNESSED")
    assert not stale, (
        "now witnessed, relabelled or removed -- drop from "
        f"catalog/unwitnessed_break_labels.json: {stale}"
    )


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"source_smoke": {"v1": {}, "v2": {"expect": "failure"}}}, True),
        ({"source_smoke": {"v1": {}, "v2": {}}}, False),
        ({"source_smoke": {"v1": {"expect": "failure"}, "v2": {}}}, False),
        ({}, False),
    ],
)
def test_source_smoke_witness_needs_v1_pass_v2_fail(
    entry: dict, expected: bool
) -> None:
    assert _source_smoke_witnesses(entry) is expected


def test_runtime_smoke_never_witnesses_api_break() -> None:
    runner = _load_runner()
    runner._skip_reason = lambda _n, _e: None
    runner.example_catalog = type(
        "C",
        (),
        {
            "case_dir": staticmethod(
                lambda _n: _ROOT / "catalog/cases/case01_symbol_removal"
            )
        },
    )
    got = classify(
        {"a": {"expected": "API_BREAK"}, "b": {"expected": "BREAKING"}}, runner
    )
    assert got == {"a": "UNWITNESSED", "b": "runtime_smoke"}
