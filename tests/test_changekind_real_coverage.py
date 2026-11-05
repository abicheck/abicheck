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
