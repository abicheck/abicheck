# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Compiled-example lane of the cross-format gating-count oracle.

Builds every castxml-free catalog example (``CASTXML_FREE_CASES``) with the
system C/C++ compiler, compares the DWARF + symbol-table dumps, and asserts
that each gating count the Markdown report and review digest state equals
the JSON's, under every gate scheme and contract state the fast lane
(``tests/unit/report/test_report_gating_consistency.py``) crosses. Bug class
``report.gating_count_recomputed_from_verdict``.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.dumper import dump
from tests._report_gating_oracle import (
    CONTRACT_STATES,
    SEVERITY_CONFIGS,
    assert_gating_consistent,
    stamp_contract,
)
from tests.test_castxml_free_examples import (
    CASTXML_FREE_CASES,
    _compile,
    _sources,
    example_catalog,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform != "linux", reason="castxml-free DWARF lane is Linux-only"
    ),
]


@pytest.mark.parametrize("case_name", CASTXML_FREE_CASES)
def test_example_markdown_gating_counts_equal_json(
    case_name: str, tmp_path: Path
) -> None:
    v1_src, v2_src = _sources(example_catalog.case_dir(case_name))
    v1_lib, v2_lib = tmp_path / "libv1.so", tmp_path / "libv2.so"
    _compile(v1_src, v1_lib)
    _compile(v2_src, v2_lib)
    base = compare(
        dump(v1_lib, headers=[], version="v1"), dump(v2_lib, headers=[], version="v2")
    )
    for contract in CONTRACT_STATES:
        for scheme, config in SEVERITY_CONFIGS.items():
            result = copy.deepcopy(base)
            stamp_contract(result, contract)
            total = assert_gating_consistent(result, config, contract=contract)
            if contract == "out":
                assert total == 0, (case_name, scheme)
