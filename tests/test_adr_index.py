"""Validate that the ADR index is generated and in sync with the ADR files.

The index table (`docs/development/adr/index.md`) is a derived artifact built
by `scripts/gen_adr_index.py` from the `NNN-*.md` files. Hand-editing it was a
recurring merge-conflict source and it drifted out of sync; these tests are the
guard that keeps it honest:

1. `scripts/gen_adr_index.py --check` succeeds, i.e. index.md matches the files.
2. Every ADR file has a parseable `# ADR-NNN: <title>` heading and a
   `**Status:**` line — the two fields the generator reads.
3. The generated table contains a row for every ADR file (no silent drift).
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ADR_DIR = ROOT / "docs" / "development" / "adr"
GEN_SCRIPT = ROOT / "scripts" / "gen_adr_index.py"
ADR_FILE_RE = re.compile(r"^(\d{3})-.*\.md$")


def _load_generator_module():
    spec = importlib.util.spec_from_file_location("gen_adr_index", GEN_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules.pop("gen_adr_index", None)
    sys.modules["gen_adr_index"] = module
    spec.loader.exec_module(module)
    return module


def _adr_files() -> list[str]:
    return sorted(
        p.name for p in ADR_DIR.iterdir() if p.is_file() and ADR_FILE_RE.match(p.name)
    )


def test_generator_check_passes() -> None:
    """`gen_adr_index.py --check` must succeed — index.md is in sync."""
    result = subprocess.run(
        [sys.executable, str(GEN_SCRIPT), "--check"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, (
        "docs/development/adr/index.md is out of date — "
        "run `python scripts/gen_adr_index.py`.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_every_adr_file_is_indexed() -> None:
    mod = _load_generator_module()
    indexed = {a.filename for a in mod._collect_adrs()}
    assert indexed == set(_adr_files()), (
        "Every NNN-*.md ADR file must appear in the generated index exactly once."
    )


@pytest.mark.parametrize("filename", _adr_files())
def test_adr_headers_are_parseable(filename: str) -> None:
    mod = _load_generator_module()
    adr = mod._parse_adr(ADR_DIR / filename)
    assert adr.number == filename[:3]
    assert adr.title, f"{filename}: empty title parsed from heading"
    assert adr.status, f"{filename}: missing **Status:** line"


def test_render_is_deterministic_and_sorted() -> None:
    mod = _load_generator_module()
    adrs = mod._collect_adrs()
    keys = [(a.number, a.filename) for a in adrs]
    assert keys == sorted(keys), "ADRs must render in (number, filename) order"
    # Idempotence: rendering the collected ADRs twice yields identical output.
    assert mod._render(adrs) == mod._render(adrs)
