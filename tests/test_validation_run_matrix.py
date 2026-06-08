"""Tests for the real-world validation harness helpers."""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path


def _load_logical_name():
    script = Path("validation/scripts/run_matrix.py").read_text()
    module = ast.parse(script)
    func = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "logical_name")
    ns = {"os": os, "re": re}
    exec(compile(ast.Module(body=[func], type_ignores=[]), str(Path("validation/scripts/run_matrix.py")), "exec"), ns)
    return ns["logical_name"]


def test_logical_name_handles_standard_soname_suffixes() -> None:
    logical_name = _load_logical_name()

    assert logical_name("/pkg/lib/libprotobuf.so.33.5.0") == "libprotobuf"
    assert logical_name("/pkg/lib/libssl.so.3") == "libssl"


def test_logical_name_strips_version_embedded_before_so_suffix() -> None:
    logical_name = _load_logical_name()

    assert logical_name("/pkg/lib/libcapnp-1.4.0.so") == "libcapnp"
    assert logical_name("/pkg/lib/libkj-async-1.3.0.so") == "libkj-async"
