# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 CodeRabbit Inc.
"""The generated ``kinds.pyi`` mypy stub stays in sync with its source data.

Mirrors ``test_detector_spec.py``'s ``test_generated_files_in_sync`` pattern:
``scripts/check_ai_readiness.py``'s ``generated-file-ownership`` check only
verifies ``kinds.pyi`` still carries its "generated, don't hand-edit" marker
comment -- it never re-derives the stub's actual content and compares, so a
``kind_names_*.py`` edit that forgot to re-run
``scripts/gen_changekind_stub.py`` would pass every AI-readiness/architecture
gate while mypy silently type-checked against a stale enum shape (Codex
review on PR #902, abicheck/abicheck). This test is the enforcement
``gen_changekind_stub.py``'s own ``--check`` mode was missing: it runs as an
ordinary part of the fast unit suite on every PR, the same way
``test_detector_spec.py`` already enforces ``detector-spec.{md,json}``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent


def _load_gen():
    path = REPO_DIR / "scripts" / "gen_changekind_stub.py"
    spec = importlib.util.spec_from_file_location("gen_changekind_stub", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(REPO_DIR / "scripts"))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(REPO_DIR / "scripts"))
    return mod


def test_kinds_pyi_is_in_sync():
    """The committed kinds.pyi matches what the generator would write today."""
    gen = _load_gen()
    assert gen.main(["--check"]) == 0, (
        "abicheck/model/change_catalog/kinds.pyi is stale -- run: "
        "python scripts/gen_changekind_stub.py"
    )


def test_kinds_pyi_declares_every_runtime_member():
    """Belt-and-suspenders: the stub's member set matches the real enum.

    ``gen_changekind_stub.render()`` derives from the same three
    ``kind_names_*.py`` data files ``kinds.py`` itself assembles at runtime,
    so the two can't independently drift by construction -- but this checks
    the actual imported runtime enum directly, in case a future change to
    ``kinds.py`` stops deriving purely from those three files.
    """
    from abicheck.model.change_catalog.kinds import ChangeKind

    gen = _load_gen()
    stub_names = set()
    for module_name in gen._DATA_MODULES:
        stub_names.update(name for name, _value in gen._load_kind_names(module_name))
    runtime_names = {member.name for member in ChangeKind}
    assert stub_names == runtime_names, (
        f"stub/runtime mismatch -- stub only: {stub_names - runtime_names}; "
        f"runtime only: {runtime_names - stub_names}"
    )
