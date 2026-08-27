# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 CodeRabbit Inc.
"""``kinds.py``'s runtime-constructed ``ChangeKind`` stays faithful to an
ordinary class-body ``str`` `Enum` -- both in what mypy sees (the generated
``kinds.pyi`` stub) and in what actually runs.

Stub-drift half: mirrors ``test_detector_spec.py``'s
``test_generated_files_in_sync`` pattern. ``scripts/check_ai_readiness.py``'s
``generated-file-ownership`` check only verifies ``kinds.pyi`` still carries
its "generated, don't hand-edit" marker comment -- it never re-derives the
stub's actual content and compares, so a ``kind_names_*.py`` edit that forgot
to re-run ``scripts/gen_changekind_stub.py`` would pass every AI-readiness/
architecture gate while mypy silently type-checked against a stale enum
shape (Codex review on PR #902, abicheck/abicheck). This test is the
enforcement ``gen_changekind_stub.py``'s own ``--check`` mode was missing: it
runs as an ordinary part of the fast unit suite on every PR, the same way
``test_detector_spec.py`` already enforces ``detector-spec.{md,json}``.

Runtime-fidelity half: a second, independent Codex review round on the same
PR caught a real regression in ``kinds.py``'s first version -- constructing
the functional ``Enum()`` with a custom ``str`` subclass as its ``type=``
mixin (to carry a ``_missing_`` classmethod) changes every member's
``.value`` from an exact ``str`` instance to a subclass instance, which
breaks a serializer that dispatches on exact type rather than
``isinstance`` -- PyYAML's default representer lookup is exactly this, and
``yaml.safe_dump(ChangeKind.FUNC_REMOVED.value)`` raised ``RepresenterError``
under the mixin version even though every ``isinstance(x, str)`` check still
passed. Per this repo's "regression test targets the bug class, not the one
reported input" convention (`AGENTS.md`), the tests below check the
*property* (every member's ``.value`` is an exact ``str``, and behaves like
one under real serializers) across every one of the 397 members, not just
the one value the report happened to use.
"""

from __future__ import annotations

import importlib.util
import json
import pickle
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


def test_every_member_value_is_an_exact_str():
    """No member's ``.value`` is a ``str`` *subclass* instance.

    ``type(x) is str``, not merely ``isinstance(x, str)`` -- the mixin-type
    regression this guards against passed every ``isinstance`` check while
    still being the wrong exact type. Checked over every one of the 397
    members, not a hand-picked sample.
    """
    from abicheck.model.change_catalog.kinds import ChangeKind

    not_exact_str = [m.name for m in ChangeKind if type(m.value) is not str]
    assert not not_exact_str, (
        f"members whose .value is not an exact str: {not_exact_str}"
    )


def test_every_member_value_round_trips_through_yaml_and_json():
    """Every member's ``.value`` serializes the way a plain ``str`` would.

    The reported break: PyYAML's default representer dispatches on exact
    type (``type(data) in self.yaml_representers``), not ``isinstance``, so
    a ``str`` subclass instance falls through to ``represent_undefined`` and
    raises ``RepresenterError`` -- reproduced directly against the
    pre-fix ``_ChangeKindBase``-mixin version before trusting this test.
    JSON's ``json.dumps`` is included as a second, independent serializer
    that would have caught the same class of defect differently (it
    round-trips a ``str`` subclass fine by default, so this is a weaker
    check on its own -- kept for completeness, not as the primary guard).
    """
    import yaml

    from abicheck.model.change_catalog.kinds import ChangeKind

    for member in ChangeKind:
        dumped = yaml.safe_dump(member.value)
        assert yaml.safe_load(dumped) == member.value
        assert json.loads(json.dumps(member.value)) == member.value


def test_missing_hook_still_resolves_the_back_compat_alias():
    """Attaching ``_missing_`` by assignment (not a mixin) still works.

    Regression guard for the fix itself, not just the bug it fixes: moving
    ``_missing_`` off a custom ``type=`` mixin and onto plain post-
    construction assignment must not silently stop the hook from firing.
    """
    from abicheck.model.change_catalog.kinds import ChangeKind

    assert (
        ChangeKind("evidence_coverage_asymmetric")
        is ChangeKind.EVIDENCE_COVERAGE_ASYMMETRIC
    )


def test_members_pickle_by_qualified_reference():
    """Enum pickling still resolves by name, not by (now plain-str) value."""
    from abicheck.model.change_catalog.kinds import ChangeKind

    restored = pickle.loads(pickle.dumps(ChangeKind.FUNC_REMOVED))
    assert restored is ChangeKind.FUNC_REMOVED
