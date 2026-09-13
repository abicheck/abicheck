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
import shutil
import subprocess
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


STUB_RELPATH = "abicheck/model/change_catalog/kinds.pyi"


def _ruff(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ruff", *args],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )


def test_the_formatter_leaves_the_generated_stub_alone_on_an_explicit_path():
    """`ruff format` must skip the stub however it is invoked.

    `ruff.toml` excludes `kinds.pyi` from formatting because
    `gen_changekind_stub.py --check` is its formatting authority and fails on
    any byte it would not itself write. But ruff normally ignores `exclude`
    for a path named on the command line, and `pre-commit` invokes its hooks
    with exactly that -- the changed files as arguments. So the exclusion held
    for `verify.py`'s directory-walking `fmt-check` and was silently bypassed
    by the `ruff-format` hook, which would reformat a regenerated stub and
    leave `--check` rejecting it, with no way through the normal hook
    workflow (Codex review on PR #1271).

    This asserts the *mechanism* by running the two invocation shapes, not
    the presence of `force-exclude` in `ruff.toml` -- asserting config text
    is the failure mode `AGENTS.md` records from #705 -> #758, and it would
    not have caught this bug at all, since the original `exclude` line was
    present and correct while the hook ignored it.
    """
    if shutil.which(sys.executable) is None:  # pragma: no cover - defensive
        return
    for argv in (
        [STUB_RELPATH],  # how pre-commit invokes it
        ["abicheck/"],  # how verify.py invokes it
    ):
        proc = _ruff("format", "--check", *argv)
        assert proc.returncode == 0, (
            f"`ruff format --check {' '.join(argv)}` wants to rewrite a "
            f"generated file; `gen_changekind_stub.py --check` would then "
            f"reject the result:\n{proc.stdout}\n{proc.stderr}"
        )


def test_linting_still_reaches_the_generated_stub(tmp_path):
    """The exclusion is formatting-only -- it must not silently drop lint.

    The negative half of the test above: `force-exclude` applies to every
    exclusion ruff knows about, so it would be easy to widen the stub's
    format-only exemption into a lint exemption without noticing. A stub that
    nothing lints is how an unused import or an undefined name reaches mypy's
    view of `ChangeKind`.

    Proving that needs a lint violation in the stub, and the first version of
    this test wrote one into the repository's own file and restored it in a
    `finally`. That is a real flake, not a tidiness point: the unit lanes run
    `-n auto --dist worksteal`, so `test_kinds_pyi_is_in_sync` can read the
    file on another worker inside that window and report the generated stub as
    stale (Codex review on PR #1271; confirmed by running the reader against
    the mutation window, which exits 1). The sandbox below copies the real
    `ruff.toml` and the real stub to the same relative path under `tmp_path`,
    so the configuration under test is still the repository's -- a widened
    `[lint] exclude` is copied in with it -- while nothing shared is written.
    """
    (tmp_path / "ruff.toml").write_text(
        (REPO_DIR / "ruff.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    sandbox_stub = tmp_path / STUB_RELPATH
    sandbox_stub.parent.mkdir(parents=True, exist_ok=True)
    sandbox_stub.write_text(
        (REPO_DIR / STUB_RELPATH).read_text(encoding="utf-8") + "\nimport os\n",
        encoding="utf-8",
    )

    for argv in ([STUB_RELPATH], ["abicheck/"]):
        proc = subprocess.run(
            [sys.executable, "-m", "ruff", "check", *argv],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert proc.returncode != 0, (
            f"`ruff check {' '.join(argv)}` did not flag an unused import in "
            f"the generated stub -- lint no longer reaches it:\n{proc.stdout}"
        )
        assert "F401" in proc.stdout, (
            f"`ruff check {' '.join(argv)}` failed for some reason other than "
            f"the injected unused import:\n{proc.stdout}\n{proc.stderr}"
        )


def test_the_lint_sandbox_reflects_the_real_config(tmp_path):
    """Guard on the test above: its sandbox must not be trivially clean.

    A sandbox that lints nothing -- a bad copy, a missed `mkdir`, a ruff that
    cannot find its config -- would make the assertions above unreachable
    rather than satisfied. So: the same sandbox WITHOUT the injected import
    must come back clean, which fails if the sandbox is reporting some
    unrelated pre-existing error, and passes only if lint really ran there.
    """
    (tmp_path / "ruff.toml").write_text(
        (REPO_DIR / "ruff.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    sandbox_stub = tmp_path / STUB_RELPATH
    sandbox_stub.parent.mkdir(parents=True, exist_ok=True)
    sandbox_stub.write_text(
        (REPO_DIR / STUB_RELPATH).read_text(encoding="utf-8"), encoding="utf-8"
    )

    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", STUB_RELPATH],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"the unmutated sandbox stub is not lint-clean, so the sibling test's "
        f"assertions could pass for the wrong reason:\n{proc.stdout}"
    )


def test_the_stub_is_not_modified_by_this_module(tmp_path):
    """No test here may write to the repository's shared generated stub.

    States the invariant behind the sandbox rather than trusting it: under
    `-n auto --dist worksteal` any write to a shared source file races every
    other worker, and a `finally` that restores it does not close the window.
    """
    before = (REPO_DIR / STUB_RELPATH).read_bytes()
    test_linting_still_reaches_the_generated_stub(tmp_path)
    assert (REPO_DIR / STUB_RELPATH).read_bytes() == before
