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
    "ABIFORGE_ONEDAL_SOURCE" not in os.environ, reason="requires oneDAL checkout"
)
def test_real_product_visitors(tmp_path, monkeypatch):
    monkeypatch.setenv("ABICHECK_AST_FRONTEND", "clang")
    results = run(tmp_path, Path(os.environ["ABIFORGE_ONEDAL_SOURCE"]))
    assert len(results) == 36
    assert [r for r in results if not r["matches"]] == []
