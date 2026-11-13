"""Shrink-only ratchet for ChangeKinds with no real-extraction evidence.

See ``scripts/changekind_real_coverage.py``: a kind named by no catalog
case, field shape or ``integration``-marked test assertion may have a
detector the real extractors never feed. The zero-source set may only shrink.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_DIR / "scripts"))

import changekind_real_coverage as cov  # noqa: E402


@pytest.fixture(scope="module")
def inventory() -> dict[str, dict[str, object]]:
    return cov.build_inventory()


def _baseline() -> set[str]:
    return set(
        json.loads(cov.BASELINE_PATH.read_text(encoding="utf-8"))["zero_source_kinds"]
    )


@pytest.mark.repo_scan
def test_no_new_zero_source_kind(inventory: dict[str, dict[str, object]]) -> None:
    new = sorted(set(cov.zero_source_kinds(inventory)) - _baseline())
    assert not new, (
        "ChangeKind(s) with no real-extraction evidence (catalog case, field shape, or "
        f"integration test assertion): {new}. Add an end-to-end source; do not extend the baseline."
    )


@pytest.mark.repo_scan
def test_baseline_only_shrinks(inventory: dict[str, dict[str, object]]) -> None:
    stale = sorted(_baseline() - set(cov.zero_source_kinds(inventory)))
    assert not stale, (
        f"Kinds now covered (or removed); drop them from {cov.BASELINE_PATH.name}: {stale}"
    )


@pytest.mark.repo_scan
def test_generated_doc_is_fresh() -> None:
    assert cov.main(["--check"]) == 0


_KIND = {"func_removed": "FUNC_REMOVED"}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "import pytest\npytestmark = pytest.mark.integration\ndef test_a():\n    assert 'func_removed' in k\n",
            True,
        ),
        (
            "import pytest\npytestmark = [pytest.mark.integration, pytest.mark.slow]\n"
            "def test_a():\n    assert ChangeKind.FUNC_REMOVED in k\n",
            True,
        ),
        (
            "import pytest\nclass TestX:\n    @pytest.mark.integration\n"
            "    def test_a(self):\n        assert 'func_removed' in k\n",
            True,
        ),
        (
            "import pytest\n@pytest.mark.integration\n@pytest.mark.parametrize('k', ['func_removed'])\n"
            "def test_a(k):\n    run(k)\n",
            True,
        ),
        ("def test_a():\n    assert 'func_removed' in k\n", False),
        (
            "import pytest\npytestmark = pytest.mark.slow\ndef test_a():\n    assert 'func_removed' in k\n",
            False,
        ),
        (
            "import pytest\n@pytest.mark.integration\ndef test_a():\n    x = 'func_removed'\n    assert x\n",
            False,
        ),
        (
            "import pytest\n@pytest.mark.integration\ndef helper():\n    assert 'func_removed' in k\n",
            False,
        ),
        # A known-gap canary pins a miss (`not in`), so it is no detection evidence,
        # whether marked on the module, the class or the test.
        (
            "import pytest\npytestmark = [pytest.mark.integration, pytest.mark.known_gap_canary]\n"
            "def test_a():\n    assert 'func_removed' not in k\n",
            False,
        ),
        (
            "import pytest\npytestmark = pytest.mark.integration\n@pytest.mark.known_gap_canary\n"
            "class TestX:\n    def test_a(self):\n        assert 'func_removed' not in k\n",
            False,
        ),
        (
            "import pytest\n@pytest.mark.integration\n@pytest.mark.known_gap_canary\n"
            "def test_a():\n    assert 'func_removed' not in k\n",
            False,
        ),
        (
            "import pytest\n@pytest.mark.integration\n@pytest.mark.known_gap_canary\n"
            "def test_a():\n    assert 'func_removed' not in k\n"
            "@pytest.mark.integration\ndef test_b():\n    assert 'func_removed' in k\n",
            True,
        ),
    ],
)
def test_integration_scan_heuristic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str, expected: bool
) -> None:
    (tmp_path / "test_x.py").write_text(source, encoding="utf-8")
    monkeypatch.setattr(cov, "ROOT", tmp_path)
    found = cov.scan_integration_tests(tmp_path, kinds=_KIND)
    assert ("func_removed" in found) is expected


def test_catalog_and_field_shape_scans_read_expected_kinds(tmp_path: Path) -> None:
    gt = tmp_path / "gt.json"
    gt.write_text(
        json.dumps({"verdicts": {"c1": {"expected_kinds": ["a", "b"]}, "c2": {}}}),
        encoding="utf-8",
    )
    assert cov.scan_catalog(gt) == {"a": {"c1"}, "b": {"c1"}}
    shape = tmp_path / "s1"
    shape.mkdir()
    (shape / "ground_truth.json").write_text(
        json.dumps({"expected_kinds": ["a"]}), encoding="utf-8"
    )
    assert cov.scan_field_shapes(tmp_path) == {"a": {"s1"}}


# ---------------------------------------------------------------------------
# Real-fixture backing ledger (no-shrink): a kind a compiled catalog case or an
# edit-corpus verified floor produces may not fall back to hand-built snapshots.
# ---------------------------------------------------------------------------


def _fixture_ledger() -> dict:
    return json.loads(cov.FIXTURE_LEDGER_PATH.read_text(encoding="utf-8"))


@pytest.mark.repo_scan
def test_no_kind_loses_its_real_fixture_backing() -> None:
    lost = cov.lost_backing(_fixture_ledger(), cov.fixture_backing())
    assert not lost, (
        f"ChangeKind(s) lost every real-fixture source (catalog expected_kinds or an "
        f"edit-corpus verified_floor): {lost}. Restore a compiled fixture that produces "
        f"them; do not drop them from {cov.FIXTURE_LEDGER_PATH.name}."
    )


@pytest.mark.repo_scan
def test_fixture_ledger_matches_its_sources() -> None:
    current = cov.fixture_ledger(cov.fixture_backing())
    assert _fixture_ledger() == current, (
        f"{cov.FIXTURE_LEDGER_PATH.name} is stale: run "
        "`python scripts/changekind_real_coverage.py --write-fixture-ledger` and "
        "review the diff (a removed source must be a deliberate edit)."
    )


def test_removing_a_sole_backing_case_fails_the_gate(tmp_path: Path) -> None:
    """Negative control: delete the expected_kinds of a case that is the only
    backing of some kind, and the no-shrink gate names exactly that kind."""
    ledger = _fixture_ledger()
    sole = {
        kind: sources[0].removeprefix("catalog:")
        for kind, sources in ledger["backed"].items()
        if len(sources) == 1 and sources[0].startswith("catalog:")
    }
    assert sole, "no kind is backed by exactly one catalog case; pick another control"
    kind, case = sorted(sole.items())[0]
    gt = json.loads(cov.CATALOG_GT.read_text(encoding="utf-8"))
    gt["verdicts"][case]["expected_kinds"] = []
    edited = tmp_path / "ground_truth.json"
    edited.write_text(json.dumps(gt), encoding="utf-8")
    backing = cov.fixture_backing(catalog=cov.scan_catalog(edited))
    assert kind in cov.lost_backing(ledger, backing)
    # ... and an unedited catalog loses nothing.
    assert cov.lost_backing(ledger, cov.fixture_backing()) == []


def test_edit_corpus_floor_and_backing_union(tmp_path: Path) -> None:
    led = tmp_path / "ledger.json"
    led.write_text(
        json.dumps({"verified_floor": {"pr": ["FUNC_REMOVED", "NOT_A_KIND"]}}),
        encoding="utf-8",
    )
    kinds = {"func_removed": "FUNC_REMOVED", "func_added": "FUNC_ADDED"}
    floor = cov.scan_edit_corpus_floor(led, kinds=kinds)
    assert floor == {"func_removed": {"edit_corpus:pr"}}
    backing = cov.fixture_backing(
        kinds=kinds, catalog={"func_removed": {"case01"}}, floor=floor
    )
    assert backing == {
        "func_added": [],
        "func_removed": ["catalog:case01", "edit_corpus:pr"],
    }
    ledger = cov.fixture_ledger(backing)
    assert ledger["summary"] == {"total_kinds": 2, "backed": 1, "unbacked": 1}
    assert cov.lost_backing(ledger, {**backing, "func_removed": []}) == ["func_removed"]
    # A kind deleted from ChangeKind is not "lost backing".
    assert cov.lost_backing(ledger, {"func_added": []}) == []
