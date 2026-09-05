from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.dumper import dump
from abicheck.serialization import snapshot_to_dict

FIXTURES_DIR = Path(__file__).parent / "fixtures"
# Anchor all paths to repo root (robust against CWD differences in CI)
REPO_ROOT = Path(__file__).parent.parent


def _actual_digest(so_path: Path, header: Path, compiler: str) -> dict:
    snap = dump(so_path, [header], version="golden", compiler=compiler)
    d = snapshot_to_dict(snap)
    return {
        "function_count": len(d.get("functions", [])),
        "variable_count": len(d.get("variables", [])),
        "type_count": len(d.get("types", [])),
        "enum_count": len(d.get("enums", [])),
        "public_functions": sorted(
            [
                {"name": f["name"], "return_type": f["return_type"]}
                for f in d.get("functions", [])
                if f.get("visibility") == "public"
            ],
            key=lambda x: x["name"],
        ),
        "public_types": sorted(
            [
                {
                    "name": t["name"],
                    "kind": t["kind"],
                    "size_bits": t.get("size_bits"),
                }
                for t in d.get("types", [])
            ],
            key=lambda x: x["name"],
        ),
    }


@pytest.mark.integration
@pytest.mark.parametrize(
    "fixture_path", sorted(FIXTURES_DIR.glob("*.json")), ids=lambda p: p.stem
)
def test_dumper_golden_fixture(fixture_path: Path) -> None:
    """Golden-file regression gate for the dumper pipeline.

    Requires pre-built shared libraries (build with ``cmake`` in catalog/cases/).
    Also requires castxml in PATH. Skipped automatically when artifacts
    are missing so unit-test CI jobs are not broken.

    This is the Phase 0 gate: if any of these tests fail after a refactor,
    the dumper output has changed in a way that must be explicitly acknowledged
    by regenerating the fixture (python scripts/regen_fixtures.py).
    """
    fixture = json.loads(fixture_path.read_text())
    # Resolve paths relative to repo root, not pytest CWD
    so_path = (REPO_ROOT / fixture["source"]).resolve()
    header = (REPO_ROOT / fixture["header"]).resolve()
    compiler = fixture.get("compiler", "c++")

    if not so_path.exists():
        pytest.skip(f"pre-built artifact missing: {so_path}  (build with cmake in catalog/cases/)")
    if not header.exists():
        pytest.skip(f"header/source missing: {header}")

    expected = {
        "function_count": fixture["function_count"],
        "variable_count": fixture["variable_count"],
        "type_count": fixture["type_count"],
        "enum_count": fixture["enum_count"],
        "public_functions": fixture["public_functions"],
        "public_types": fixture["public_types"],
    }
    assert _actual_digest(so_path, header, compiler) == expected
