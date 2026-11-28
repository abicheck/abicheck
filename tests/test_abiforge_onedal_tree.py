"""Finite oneDAL visitor domain and optional real-header end-to-end replay."""

import os
from pathlib import Path

import pytest

from abiforge.run.onedal_tree import cases, run


@pytest.mark.parametrize(
    ("task", "count"), [("regression", 16), ("classification", 20)]
)
def test_tree_domain(task, count):
    domain = list(cases(task))
    assert len(set(domain)) == count
    assert sum(not order for _, _, order in domain) == 1


@pytest.mark.integration
@pytest.mark.skipif(
    not (os.getenv("ABIFORGE_ONEDAL_SOURCE") or os.getenv("ABIFORGE_ONEDAL_OLD")),
    reason="requires oneDAL source or CPU development headers",
)
def test_real_product_visitors(tmp_path, monkeypatch):
    monkeypatch.setenv("ABICHECK_AST_FRONTEND", "clang")
    prefix = os.getenv("ABIFORGE_ONEDAL_SOURCE") or os.environ["ABIFORGE_ONEDAL_OLD"]
    results = run(tmp_path, Path(prefix))
    assert len(results) == 36
    assert [r for r in results if not r["matches"]] == []
