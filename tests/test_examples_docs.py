"""Validate that examples docs are up to date and per-case READMEs are well-formed.

Three guarantees enforced here:

1. `scripts/gen_examples_docs.py --check` succeeds, so the rendered docs site
   tree under `docs/examples/` is in sync with `examples/`.
2. Every case listed in `ground_truth.json` has a `README.md` whose first line
   is an `# H1`, plus at least three `## H2` sections — enough structure to
   render usefully on the docs site.
3. The set of cases on disk under `examples/case*/` matches the set listed in
   `ground_truth.json` (no orphaned dirs, no missing entries).
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = ROOT / "examples"
GROUND_TRUTH = EXAMPLES_DIR / "ground_truth.json"
GEN_SCRIPT = ROOT / "scripts" / "gen_examples_docs.py"


def _load_generator_module():
    spec = importlib.util.spec_from_file_location("gen_examples_docs", GEN_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules.pop("gen_examples_docs", None)
    sys.modules["gen_examples_docs"] = module
    spec.loader.exec_module(module)
    return module


def _ground_truth_cases() -> list[str]:
    data = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    return sorted(data["verdicts"].keys())


def _example_dirs() -> list[str]:
    return sorted(
        p.name
        for p in EXAMPLES_DIR.iterdir()
        if p.is_dir() and p.name.startswith("case")
    )


def test_ground_truth_matches_example_dirs() -> None:
    assert _example_dirs() == _ground_truth_cases(), (
        "Mismatch between examples/case*/ directories and ground_truth.json — "
        "every case directory must have a ground_truth.json entry and vice versa."
    )


@pytest.mark.parametrize("case_name", _ground_truth_cases())
def test_case_readme_has_required_structure(case_name: str) -> None:
    readme = EXAMPLES_DIR / case_name / "README.md"
    assert readme.exists(), f"missing README: {readme}"
    text = readme.read_text(encoding="utf-8")

    first_line = text.lstrip().splitlines()[0] if text.strip() else ""
    assert re.match(r"^#\s+\S", first_line), (
        f"{case_name}/README.md: first line must be an H1 (`# Title`), got: {first_line!r}"
    )

    h2_count = len(re.findall(r"^##\s+\S", text, re.M))
    assert h2_count >= 3, (
        f"{case_name}/README.md: needs at least 3 H2 sections to render usefully, "
        f"found {h2_count}"
    )


def test_generator_check_passes() -> None:
    """Running gen_examples_docs.py --check must succeed, i.e. docs/examples/ is in sync."""
    result = subprocess.run(
        [sys.executable, str(GEN_SCRIPT), "--check"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, (
        "docs/examples/ is out of date — run `python scripts/gen_examples_docs.py`.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_generator_rewrites_source_links_without_mkdocs_broken_links() -> None:
    mod = _load_generator_module()

    rewritten = mod._rewrite_links(
        "[v1 header](v1.h) [guide](../docs/concepts/abi-stability-guide.md)"
    )

    assert "`v1 header`" in rewritten
    assert "[guide](../concepts/abi-stability-guide.md)" in rewritten
    assert "../../examples/" not in rewritten


def test_generator_source_section_uses_code_literals() -> None:
    mod = _load_generator_module()
    case = mod.Case(
        name="case01_symbol_removal",
        title="Case 01: Symbol Removal",
        verdict="BREAKING",
        category="breaking",
        platforms=["linux"],
        abi_break=True,
        api_break=False,
        bad_practice=False,
        expected_kinds=[],
        body="",
    )

    source_section = mod._source_links(case)

    assert "- `v1.c`" in source_section
    assert "](" not in source_section
