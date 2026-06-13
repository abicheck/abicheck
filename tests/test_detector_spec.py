# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 CodeRabbit Inc.
"""The generated detector specification matrix stays complete and in sync."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from abicheck.checker_policy import ChangeKind

REPO_DIR = Path(__file__).resolve().parent.parent


def _load_gen():
    path = REPO_DIR / "scripts" / "gen_detector_spec.py"
    spec = importlib.util.spec_from_file_location("gen_detector_spec", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(REPO_DIR / "scripts"))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(REPO_DIR / "scripts"))
    return mod


def test_every_changekind_in_spec():
    gen = _load_gen()
    rows = gen.build_spec()
    kinds_in_spec = {r["kind"] for r in rows}
    all_kinds = {k.value for k in ChangeKind}
    assert kinds_in_spec == all_kinds, (
        f"spec missing: {all_kinds - kinds_in_spec}; extra: {kinds_in_spec - all_kinds}"
    )


def test_every_row_has_a_known_category():
    gen = _load_gen()
    valid = {"breaking", "api_break", "risk", "addition", "quality"}
    bad = [r["kind"] for r in gen.build_spec() if r["category"] not in valid]
    assert not bad, f"rows with unknown category (unpartitioned?): {bad}"


def test_generated_files_in_sync():
    """The committed docs/reference/detector-spec.{md,json} are up to date."""
    gen = _load_gen()
    assert gen.main(["--check"]) == 0, (
        "detector spec is stale — run: python scripts/gen_detector_spec.py"
    )


# Baseline number of kinds with no declared evidence tier (EVIDENCE_TIER_BY_KIND
# is intentionally partial). The guard fails if a NEWLY-added ChangeKind ships
# without a tier (count rises) — forcing a conscious choice: add a tier in
# scripts/evidence_tiers.py, or bump this baseline deliberately. Lowering the
# count (mapping more kinds) is always welcome; tighten the baseline then.
UNSPECIFIED_TIER_BASELINE = 153


def test_unspecified_evidence_tier_count_does_not_grow():
    gen = _load_gen()
    unspecified = sorted(
        r["kind"] for r in gen.build_spec()
        if r["min_evidence"] == gen.UNSPECIFIED_TIER
    )
    assert len(unspecified) <= UNSPECIFIED_TIER_BASELINE, (
        f"{len(unspecified)} kinds lack an evidence tier (baseline "
        f"{UNSPECIFIED_TIER_BASELINE}). A new ChangeKind shipped without a tier — "
        f"add one in scripts/evidence_tiers.py or bump the baseline. New/extra: "
        f"{unspecified}"
    )
