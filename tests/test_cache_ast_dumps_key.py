# SPDX-License-Identifier: Apache-2.0
"""The header-AST cache key must cover every input that changes a dump.

``.github/actions/cache-ast-dumps/action.yml`` restores ``~/.cache/abi_check``
across CI runs. A key that leaves out the extractor's source lets a run reuse
dumps an older extractor generation wrote, so the cache can hide a regression
the PR itself introduced. These tests pin the key's inputs and the restore
prefix to the same package hash.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests._yaml_fast import safe_load

REPO = Path(__file__).resolve().parent.parent
ACTION = REPO / ".github" / "actions" / "cache-ast-dumps" / "action.yml"
PACKAGE_HASH = "hashFiles('abicheck/**/*.py')"


def _cache_step() -> dict:
    doc = safe_load(ACTION.read_text(encoding="utf-8"))
    steps = doc["runs"]["steps"]
    (step,) = [s for s in steps if str(s.get("uses", "")).startswith("actions/cache@")]
    return step["with"]


def test_key_hashes_the_whole_abicheck_package():
    key = _cache_step()["key"]
    assert PACKAGE_HASH in key


def test_package_hash_covers_extractor_generation_module():
    # EXTRACTOR_GENERATION (storage/versioning.py) is the knob that says "this
    # binary now yields a different surface"; the key must hash the file it lives in.
    generation_file = REPO / "abicheck" / "storage" / "versioning.py"
    assert "EXTRACTOR_GENERATION" in generation_file.read_text(encoding="utf-8")
    covered = {p.resolve() for p in (REPO / "abicheck").glob("**/*.py")}
    assert generation_file.resolve() in covered


def test_key_keeps_toolchain_and_header_inputs():
    key = _cache_step()["key"]
    assert "hashFiles('results/abi-check-toolchain-versions.txt'" in key
    assert "abicheck/extract/headers/ast_config.py" in key
    assert "hashFiles('examples/**', 'catalog/**')" in key


def test_restore_prefix_never_crosses_package_hash():
    # A restore prefix that drops the package hash would hand a PR the dumps an
    # older extractor wrote. Every restore key must carry the same hash term and
    # end where the content-addressed suffix begins.
    restore = [k for k in _cache_step()["restore-keys"].splitlines() if k.strip()]
    assert restore, "restore-keys must not be empty"
    for key in restore:
        assert PACKAGE_HASH in key
        assert key.strip().endswith("-")


def test_schema_token_is_bumped_past_the_unhashed_v2_key():
    key = _cache_step()["key"]
    assert re.match(r"abi-check-ast-v3-", key)
