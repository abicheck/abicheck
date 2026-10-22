"""Shared loader for the Phase 4 clang golden tests.

``scripts/measure_l4_clang_projection.py`` and ``scripts/gen_l4_clang_golden.py``
are scripts, not package modules. Both tests in
``test_l4_clang_projection_golden.py`` need them, so the ``importlib`` load lives
here once, following ``_l2_real_profiles_support.py``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
GOLDEN = REPO_ROOT / "tests" / "golden" / "l4_clang" / "surface_0_13.jsonl"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Dataclasses look their module up in sys.modules while being built.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


#: The spike's comparison and projection-view helpers.
M = _load("measure_l4_clang_projection")
#: The generator: compile-unit construction, golden text, clang version.
G = _load("gen_l4_clang_golden")


def golden_header_and_rows() -> tuple[dict, list[dict]]:
    lines = GOLDEN.read_text(encoding="utf-8").splitlines()
    return json.loads(lines[0]), [json.loads(line) for line in lines[1:]]
