# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit-test mirror of the ``engine-cli-boundary`` AI-readiness check
(``scripts/engine_cli_boundary.py``, registered by
``scripts/check_ai_readiness.py``) — Phase 0 of
``docs/contribute/plans/duplication-and-convergence-assessment.md``.

The check ERRORs if an engine-layer module (``scan_engine.py``,
``service*.py``, ``artifact_*.py``, ``buildsource/**/*.py``,
``workflows/artifact/**/*.py``) imports
``click`` or a ``cli``/``cli_*`` sibling module — the CLI is a frontend
adapter over the engine, not the reverse. Pre-existing violations are
recorded in ``ENGINE_CLI_BOUNDARY_ALLOWLIST`` (allowlist-and-shrink,
mirroring ``IMPORT_CYCLE_ALLOWLIST``'s own design); this file both pins
that the real repository has no *unlisted* violation and that the
detection logic itself actually catches every way an engine module can
cross the boundary.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.check_ai_readiness import Findings  # noqa: E402
from scripts.engine_cli_boundary import (  # noqa: E402
    ENGINE_CLI_BOUNDARY_ALLOWLIST,
    check_engine_cli_boundary,
)


def test_no_unlisted_violation_in_real_repo() -> None:
    """The real repository has zero *unlisted* engine/CLI-boundary violations.

    A pre-existing one must be named in ``ENGINE_CLI_BOUNDARY_ALLOWLIST``
    (reviewed, not silently accumulating) — this pins that the check itself
    is clean against the actual tree, not just against a synthetic fixture.
    """
    findings = Findings()
    check_engine_cli_boundary(findings)
    errors = [m for c, m in findings.errors if c == "engine-cli-boundary"]
    assert errors == [], "Unlisted engine/CLI-boundary violations:\n" + "\n".join(
        errors
    )


def test_allowlist_entries_are_real_sites() -> None:
    """Every ``ENGINE_CLI_BOUNDARY_ALLOWLIST`` entry must still name a real
    violation — an entry that no longer matches anything is dead weight the
    allowlist should have shrunk, not a permanent grandfather clause."""
    import ast

    import scripts.engine_cli_boundary as gate

    seen: set[str] = set()
    for path in sorted(gate.PKG.rglob("*.py")):
        rel = gate._rel(path)
        if not gate._is_engine_module(rel):
            continue
        try:
            tree = ast.parse(gate._read(path), filename=rel)
        except SyntaxError:
            continue
        seen.update(
            key for key, _lineno, _desc in gate._engine_boundary_sites(tree, rel)
        )
    stale = ENGINE_CLI_BOUNDARY_ALLOWLIST - seen
    assert stale == set(), f"Stale allowlist entries (no longer violate): {stale}"


# ── Detection logic: each way of crossing the boundary is caught once ───────

_ENGINE_VIOLATION_CASES: list[pytest.ParameterSet] = [
    pytest.param(
        "scan_engine.py",
        "import click\n",
        {},
        id="scan_engine-import-click",
    ),
    pytest.param(
        "service_widget.py",
        "def go():\n    import click\n    return click\n",
        {},
        id="service-lazy-import-click",
    ),
    pytest.param(
        "service_widget.py",
        "from click import echo\n",
        {},
        id="service-from-click-import",
    ),
    pytest.param(
        "service_widget.py",
        "from .cli_dump_helpers import _gated_source_label\n",
        {},
        id="service-relative-cli-submodule",
    ),
    pytest.param(
        "service_widget.py",
        "from . import cli_scan_helpers\n",
        # `cli_scan_helpers` must be a *real* submodule on disk for this to
        # be a genuine boundary crossing — see
        # test_symbol_named_like_cli_module_is_not_flagged below for the
        # negative case where no such file exists.
        {"cli_scan_helpers.py": "\n"},
        id="service-relative-import-cli-name",
    ),
    pytest.param(
        "service_widget.py",
        "from . import cli_tools\n",
        # A directory with no __init__.py is still a real, importable PEP
        # 420 namespace package — Python would resolve this import.
        {"cli_tools/helpers.py": "\n"},
        id="service-relative-import-cli-namespace-package",
    ),
    pytest.param(
        "scan_engine.py",
        "from abicheck.cli_buildsource import embed_build_source\n",
        {},
        id="scan_engine-absolute-cli-submodule",
    ),
    pytest.param(
        "scan_engine.py",
        "import abicheck.cli_dump_helpers\n",
        {},
        id="scan_engine-absolute-import-dotted",
    ),
    pytest.param(
        "service_widget.py",
        "from abicheck import cli_dump_helpers\n",
        {"cli_dump_helpers.py": "\n"},
        id="service-from-abicheck-import-cli-name",
    ),
    pytest.param(
        "artifact_plan.py",
        "import click\n",
        {},
        id="artifact-module-import-click",
    ),
    pytest.param(
        "scan_engine.py",
        "from .compat import cli\n",
        {"compat/cli.py": "\n"},
        id="scan_engine-nested-cli-adapter-relative-alias",
    ),
    pytest.param(
        "scan_engine.py",
        "from .compat.cli import compat_group\n",
        {},
        id="scan_engine-nested-cli-adapter-relative-submodule",
    ),
    pytest.param(
        "scan_engine.py",
        "import abicheck.compat.cli\n",
        {},
        id="scan_engine-nested-cli-adapter-absolute-dotted",
    ),
    pytest.param(
        "service_widget.py",
        "from abicheck.compat import cli\n",
        {"compat/cli.py": "\n"},
        id="service-nested-cli-adapter-absolute-alias",
    ),
    pytest.param(
        "service_widget.py",
        "from abicheck.compat.cli import compat_group\n",
        {},
        id="service-nested-cli-adapter-absolute-submodule",
    ),
]


@pytest.mark.parametrize("filename, source, extra_files", _ENGINE_VIOLATION_CASES)
def test_gate_flags_violation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    source: str,
    extra_files: dict[str, str],
) -> None:
    """The gate is not a no-op: each way of crossing the boundary is caught."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / filename).write_text(source)
    for rel_path, contents in extra_files.items():
        path = pkg / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    errors = [m for c, m in findings.errors if c == "engine-cli-boundary"]
    assert len(errors) == 1
    assert filename in errors[0]


def test_symbol_named_like_cli_module_is_not_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ordinary symbol (constant, function, class) that merely happens
    to be *spelled* like a CLI module — `cli_default` imported from an
    unrelated, non-CLI module — is not a real submodule on disk and must
    not be flagged; only a genuine CLI-module import creates the
    engine-to-CLI-frontend dependency this check exists to catch."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "model.py").write_text("cli_default = 1\n")
    (pkg / "scan_engine.py").write_text("from .model import cli_default\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    assert not any(c == "engine-cli-boundary" for c, _ in findings.errors)


def test_package_attribute_shadowing_same_named_submodule_is_not_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A package whose own `__init__.py` binds `cli` to an ordinary
    attribute (a function here) shadows a same-named `cli.py` submodule
    sitting alongside it — `from .pkg import cli` resolves to the
    already-executed package's own attribute, not the submodule, since
    Python checks the package object for the name before ever attempting
    to import a same-named submodule. Must not be flagged even though a
    real `pkg/cli.py` file exists on disk."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    engine_pkg = pkg / "engine_pkg"
    engine_pkg.mkdir()
    (engine_pkg / "__init__.py").write_text("def cli() -> None:\n    pass\n")
    (engine_pkg / "cli.py").write_text("# unrelated CLI-shaped submodule\n")
    (pkg / "scan_engine.py").write_text("from .engine_pkg import cli\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    assert not any(c == "engine-cli-boundary" for c, _ in findings.errors)


def test_reexport_of_real_submodule_through_alias_still_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`from . import cli as _cli; cli = _cli` re-exports the real
    submodule object itself through an intermediate alias — the `cli =
    _cli` assignment's right-hand side names the submodule, not an
    unrelated value, so `from .pkg import cli` still loads the real CLI
    adapter and the gate must still flag it, even though the assignment
    target matches `alias_name`."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    engine_pkg = pkg / "engine_pkg3"
    engine_pkg.mkdir()
    (engine_pkg / "__init__.py").write_text("from . import cli as _cli\ncli = _cli\n")
    (engine_pkg / "cli.py").write_text("# real CLI adapter\n")
    (pkg / "scan_engine.py").write_text("from .engine_pkg3 import cli\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    errors = [m for c, m in findings.errors if c == "engine-cli-boundary"]
    assert len(errors) == 1
    assert "scan_engine.py" in errors[0]


def test_plain_import_binding_alias_name_shadows_real_submodule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`import math as cli` inside `__init__.py` binds `cli` to the stdlib
    `math` module — an `ast.Import` can never spell the relative `from .
    import cli` self-reference (it carries no relative-import level), so
    it always shadows a same-named `cli.py` submodule sitting alongside
    it. Must NOT be flagged."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    engine_pkg = pkg / "engine_pkg11"
    engine_pkg.mkdir()
    (engine_pkg / "cli.py").write_text("# unrelated CLI-shaped submodule\n")
    (engine_pkg / "__init__.py").write_text("import math as cli\n")
    (pkg / "scan_engine.py").write_text("from .engine_pkg11 import cli\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    assert not any(c == "engine-cli-boundary" for c, _ in findings.errors)


def test_absolute_importfrom_bare_self_reference_still_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`from abicheck.engine_pkg12 import cli` inside
    `engine_pkg12/__init__.py` is the absolute spelling of `from . import
    cli` — it resolves to the identical real submodule file. Must still be
    flagged."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    engine_pkg = pkg / "engine_pkg12"
    engine_pkg.mkdir()
    (engine_pkg / "cli.py").write_text("# real CLI adapter\n")
    (engine_pkg / "__init__.py").write_text("from abicheck.engine_pkg12 import cli\n")
    (pkg / "scan_engine.py").write_text("from .engine_pkg12 import cli\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    errors = [m for c, m in findings.errors if c == "engine-cli-boundary"]
    assert len(errors) == 1
    assert "scan_engine.py" in errors[0]


def test_absolute_importfrom_direct_from_submodule_still_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`from abicheck.engine_pkg13.cli import main as cli` imports a symbol
    directly from the real submodule, spelled absolutely rather than
    relatively (the `from .cli import main as cli` case already covered,
    but via `abicheck.<pkg>.cli` instead of `.cli`). Must still be
    flagged."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    engine_pkg = pkg / "engine_pkg13"
    engine_pkg.mkdir()
    (engine_pkg / "cli.py").write_text("def main() -> None:\n    pass\n")
    (engine_pkg / "__init__.py").write_text(
        "from abicheck.engine_pkg13.cli import main as cli\n"
    )
    (pkg / "scan_engine.py").write_text("from .engine_pkg13 import cli\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    errors = [m for c, m in findings.errors if c == "engine-cli-boundary"]
    assert len(errors) == 1
    assert "scan_engine.py" in errors[0]


def test_unaliased_dotted_import_binds_only_first_component(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`import cli.helpers` (no `as`) inside `__init__.py` binds only the
    *first* dotted component, `cli` — Python's own dotted-import rule — to
    whatever top-level `cli` package that resolves to (here, an unrelated
    one, not `engine_pkg14/cli.py`). `from .engine_pkg14 import cli` then
    resolves to that unrelated binding, never reaching the real, same-named
    `cli.py` submodule sitting alongside it. Must NOT be flagged."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    # An unrelated top-level package also named `cli`, with a `helpers`
    # submodule — this is what `import cli.helpers` actually binds `cli` to.
    unrelated_cli_pkg = tmp_path / "cli"
    unrelated_cli_pkg.mkdir()
    (unrelated_cli_pkg / "__init__.py").write_text("")
    (unrelated_cli_pkg / "helpers.py").write_text("# unrelated third-party module\n")

    engine_pkg = pkg / "engine_pkg14"
    engine_pkg.mkdir()
    (engine_pkg / "cli.py").write_text("# unrelated CLI-shaped submodule\n")
    (engine_pkg / "__init__.py").write_text("import cli.helpers\n")
    (pkg / "scan_engine.py").write_text("from .engine_pkg14 import cli\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    assert not any(c == "engine-cli-boundary" for c, _ in findings.errors)


def test_importfrom_reexport_through_sibling_package_still_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`engine_pkg15/__init__.py` does `from .frontend import cli`, where
    `frontend` is a *subpackage* (`frontend/__init__.py`), not a plain
    module file — and `frontend/__init__.py` re-exports the real submodule
    from its own parent via `from .. import cli` (relative-import level 2,
    since `frontend`'s own directory is one level deeper than
    `engine_pkg15`'s). Importing `engine_pkg15` still transitively reaches
    the real `cli.py` submodule, so this must still be flagged — the sibling
    chase must recognize a package facade, not only a plain module file."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    engine_pkg = pkg / "engine_pkg15"
    engine_pkg.mkdir()
    (engine_pkg / "cli.py").write_text("# real CLI adapter\n")
    frontend_pkg = engine_pkg / "frontend"
    frontend_pkg.mkdir()
    (frontend_pkg / "__init__.py").write_text("from .. import cli\n")
    (engine_pkg / "__init__.py").write_text("from .frontend import cli\n")
    (pkg / "scan_engine.py").write_text("from .engine_pkg15 import cli\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    errors = [m for c, m in findings.errors if c == "engine-cli-boundary"]
    assert len(errors) == 1
    assert "scan_engine.py" in errors[0]


def test_absolute_import_alias_of_the_real_submodule_still_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`import abicheck.engine_pkg16.cli as cli` is a plain `ast.Import`,
    but with an explicit `asname` whose dotted source resolves to the exact
    real submodule file being checked — Python binds `cli` directly to that
    submodule object, the same as `from . import cli` would. Must still be
    flagged, not treated as an unconditional shadow."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    engine_pkg = pkg / "engine_pkg16"
    engine_pkg.mkdir()
    (engine_pkg / "cli.py").write_text("# real CLI adapter\n")
    (engine_pkg / "__init__.py").write_text("import abicheck.engine_pkg16.cli as cli\n")
    (pkg / "scan_engine.py").write_text("from .engine_pkg16 import cli\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    errors = [m for c, m in findings.errors if c == "engine-cli-boundary"]
    assert len(errors) == 1
    assert "scan_engine.py" in errors[0]


def test_absolute_import_alias_of_an_unrelated_module_not_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`import abicheck.engine_pkg17.other as cli` has an explicit `asname`
    too, but its dotted source resolves to a *different* file (`other.py`,
    not `cli.py`) — a genuine shadow, unlike the self-reference case above.
    Must NOT be flagged."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    engine_pkg = pkg / "engine_pkg17"
    engine_pkg.mkdir()
    (engine_pkg / "cli.py").write_text("# unrelated CLI-shaped submodule\n")
    (engine_pkg / "other.py").write_text("# an unrelated engine submodule\n")
    (engine_pkg / "__init__.py").write_text(
        "import abicheck.engine_pkg17.other as cli\n"
    )
    (pkg / "scan_engine.py").write_text("from .engine_pkg17 import cli\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    assert not any(c == "engine-cli-boundary" for c, _ in findings.errors)


def test_importfrom_reexport_through_sibling_direct_from_submodule_still_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`engine_pkg18/__init__.py` does `from .frontend import cli`, where
    `frontend.py` itself does `from .cli import main as cli` — importing
    directly FROM the real submodule (not re-exporting a name bound to it).
    Python must load/execute `cli.py` to resolve `main` out of it, so this
    reaches the real submodule too, one hop removed. Must still be
    flagged — the sibling chase must recognize a direct-from-submodule
    import at each hop, not only a bare `from . import cli`-shaped one."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    engine_pkg = pkg / "engine_pkg18"
    engine_pkg.mkdir()
    (engine_pkg / "cli.py").write_text("def main() -> None:\n    pass\n")
    (engine_pkg / "frontend.py").write_text("from .cli import main as cli\n")
    (engine_pkg / "__init__.py").write_text("from .frontend import cli\n")
    (pkg / "scan_engine.py").write_text("from .engine_pkg18 import cli\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    errors = [m for c, m in findings.errors if c == "engine-cli-boundary"]
    assert len(errors) == 1
    assert "scan_engine.py" in errors[0]


def test_absolute_reexport_of_a_different_real_cli_module_still_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`engine_pkg19/__init__.py` does `from abicheck.cli import main as
    cli` — an absolute import of a *different* genuine first-party CLI
    module (the top-level `abicheck/cli.py`), not `engine_pkg19`'s own
    `cli.py` submodule. `engine_pkg19` also happens to have its own,
    unrelated `cli.py` file on disk, so `from .engine_pkg19 import cli`
    resolves to the real `abicheck/cli.py` module (shadowing
    `engine_pkg19`'s own submodule) — still a genuine engine->CLI-frontend
    dependency, just reached through a different real module than the one
    literally named `engine_pkg19/cli.py`. Must still be flagged."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli.py").write_text("def main() -> None:\n    pass\n")
    engine_pkg = pkg / "engine_pkg19"
    engine_pkg.mkdir()
    (engine_pkg / "cli.py").write_text("# unrelated, never actually reached\n")
    (engine_pkg / "__init__.py").write_text("from abicheck.cli import main as cli\n")
    (pkg / "scan_engine.py").write_text("from .engine_pkg19 import cli\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    errors = [m for c, m in findings.errors if c == "engine-cli-boundary"]
    assert len(errors) == 1
    assert "scan_engine.py" in errors[0]


def test_destructuring_assignment_shadows_real_submodule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`cli, other = values` (tuple destructuring) inside `__init__.py`
    binds `cli` to an ordinary attribute of `values`, exactly the same as
    a plain `cli = value` assignment would — Python doesn't treat a
    destructured name any differently from a directly-assigned one. A
    real, unrelated `cli.py` submodule sitting alongside it is shadowed
    the same way. Must NOT be flagged."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    engine_pkg = pkg / "engine_pkg20"
    engine_pkg.mkdir()
    (engine_pkg / "cli.py").write_text("# unrelated CLI-shaped submodule\n")
    (engine_pkg / "__init__.py").write_text("cli, other = get_two_things()\n")
    (pkg / "scan_engine.py").write_text("from .engine_pkg20 import cli\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    assert not any(c == "engine-cli-boundary" for c, _ in findings.errors)


def test_importfrom_directly_from_the_real_submodule_still_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`__init__.py` doing `from .cli import main as cli` imports a symbol
    directly FROM the real `cli.py` submodule (not a re-export via some
    other file) — Python must load/execute `cli.py` to resolve `main` out
    of it, regardless of which symbol is pulled or that it's locally
    renamed. Must still be flagged, even though the binding is an
    `ImportFrom` naming a specific symbol rather than the bare `from .
    import cli` self-reference."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    engine_pkg = pkg / "engine_pkg10"
    engine_pkg.mkdir()
    (engine_pkg / "cli.py").write_text("def main() -> None:\n    pass\n")
    (engine_pkg / "__init__.py").write_text("from .cli import main as cli\n")
    (pkg / "scan_engine.py").write_text("from .engine_pkg10 import cli\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    errors = [m for c, m in findings.errors if c == "engine-cli-boundary"]
    assert len(errors) == 1
    assert "scan_engine.py" in errors[0]


def test_importfrom_reexport_of_ordinary_symbol_shadows_real_submodule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`from .api import cli` inside `__init__.py` binds `cli` to an
    ordinary symbol re-exported from an unrelated module — not the real
    `cli.py` submodule sitting alongside it. `from .pkg import cli` then
    resolves to that re-exported symbol (Python checks the already-executed
    package attribute before ever importing a same-named submodule), so
    this must be treated as a shadow, not a real submodule import — even
    though the binding is an `ImportFrom`, not an `Assign`."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    engine_pkg = pkg / "engine_pkg4"
    engine_pkg.mkdir()
    (engine_pkg / "api.py").write_text("def cli() -> None:\n    pass\n")
    (engine_pkg / "__init__.py").write_text("from .api import cli\n")
    (engine_pkg / "cli.py").write_text("# unrelated CLI-shaped submodule\n")
    (pkg / "scan_engine.py").write_text("from .engine_pkg4 import cli\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    assert not any(c == "engine-cli-boundary" for c, _ in findings.errors)


def test_importfrom_reexport_through_sibling_module_still_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`engine_pkg/__init__.py` does `from .frontend import cli`, where
    `frontend.py` (a plain sibling module, not the package root) is the one
    that actually does `from . import cli` — importing the package still
    transitively reaches the real CLI submodule, one hop removed from
    `__init__.py` itself, so this must still be flagged."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    engine_pkg = pkg / "engine_pkg6"
    engine_pkg.mkdir()
    (engine_pkg / "cli.py").write_text("# real CLI adapter\n")
    (engine_pkg / "frontend.py").write_text("from . import cli\n")
    (engine_pkg / "__init__.py").write_text("from .frontend import cli\n")
    (pkg / "scan_engine.py").write_text("from .engine_pkg6 import cli\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    errors = [m for c, m in findings.errors if c == "engine-cli-boundary"]
    assert len(errors) == 1
    assert "scan_engine.py" in errors[0]


def test_importfrom_reexport_through_sibling_under_a_renamed_local_name_still_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same transitive chase must follow renaming at each hop:
    `frontend.py` imports the real submodule under a private local name
    (`from . import cli as _cli`), and `__init__.py` re-exports *that*
    name as `cli` (`from .frontend import _cli as cli`). The local names
    differ at every hop, but the chain still terminates at a literal
    `from . import cli` naming the real submodule, so this must still be
    flagged."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    engine_pkg = pkg / "engine_pkg7"
    engine_pkg.mkdir()
    (engine_pkg / "cli.py").write_text("# real CLI adapter\n")
    (engine_pkg / "frontend.py").write_text("from . import cli as _cli\n")
    (engine_pkg / "__init__.py").write_text("from .frontend import _cli as cli\n")
    (pkg / "scan_engine.py").write_text("from .engine_pkg7 import cli\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    errors = [m for c, m in findings.errors if c == "engine-cli-boundary"]
    assert len(errors) == 1
    assert "scan_engine.py" in errors[0]


def test_importfrom_sibling_reexporting_a_different_submodule_not_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`frontend.py` re-exports a *different* real submodule
    (`othername.py`) under the local name `cli` — not the actual `cli.py`
    submodule sitting alongside it. Must NOT be flagged: the chain
    terminates at `from . import othername as cli`, whose source name
    (`othername`) never matches the original `cli` being chased."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    engine_pkg = pkg / "engine_pkg8"
    engine_pkg.mkdir()
    (engine_pkg / "cli.py").write_text("# unrelated CLI-shaped submodule\n")
    (engine_pkg / "othername.py").write_text("# an unrelated engine submodule\n")
    (engine_pkg / "frontend.py").write_text("from . import othername as cli\n")
    (engine_pkg / "__init__.py").write_text("from .frontend import cli\n")
    (pkg / "scan_engine.py").write_text("from .engine_pkg8 import cli\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    assert not any(c == "engine-cli-boundary" for c, _ in findings.errors)


def test_importfrom_reexport_chain_beyond_max_hops_not_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A re-export chain longer than `_MAX_REEXPORT_HOPS` is a deliberate,
    documented gap — conservatively left unflagged rather than chased
    forever, per this module's false-negative-over-false-positive default.
    Pins that the depth guard actually bounds the chase (this test would
    hang or stack-overflow without it on a cyclic/very-deep chain, and
    would false-positive without the guard on a chain shaped like this
    one, which genuinely does terminate at the real submodule six hops
    out — one more than `_MAX_REEXPORT_HOPS` allows)."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    engine_pkg = pkg / "engine_pkg9"
    engine_pkg.mkdir()
    (engine_pkg / "cli.py").write_text("# real CLI adapter\n")
    hops = ["hop1", "hop2", "hop3", "hop4", "hop5", "hop6"]
    (engine_pkg / f"{hops[0]}.py").write_text("from . import cli\n")
    for prev, cur in zip(hops, hops[1:]):
        (engine_pkg / f"{cur}.py").write_text(f"from .{prev} import cli\n")
    (engine_pkg / "__init__.py").write_text(f"from .{hops[-1]} import cli\n")
    (pkg / "scan_engine.py").write_text("from .engine_pkg9 import cli\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    assert not any(c == "engine-cli-boundary" for c, _ in findings.errors)


def test_importfrom_aliasing_a_different_submodule_shadows_real_submodule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`from . import othername as cli` imports a *different* real
    submodule and binds it under the name `cli` — not the actual `cli.py`
    submodule sitting alongside it. Must still be treated as a shadow: the
    bound object is `othername`, not `cli`."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    engine_pkg = pkg / "engine_pkg5"
    engine_pkg.mkdir()
    (engine_pkg / "othername.py").write_text("# an unrelated engine submodule\n")
    (engine_pkg / "__init__.py").write_text("from . import othername as cli\n")
    (engine_pkg / "cli.py").write_text("# unrelated CLI-shaped submodule\n")
    (pkg / "scan_engine.py").write_text("from .engine_pkg5 import cli\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    assert not any(c == "engine-cli-boundary" for c, _ in findings.errors)


def test_annotation_only_init_declaration_does_not_suppress_real_submodule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare annotation with no value (`cli: object`) in `__init__.py`
    creates no runtime attribute — it only records an `__annotations__`
    entry — so it must not be treated as shadowing a same-named `cli.py`
    submodule. `from .pkg import cli` still imports the real submodule in
    this shape, and the gate must still flag it."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    engine_pkg = pkg / "engine_pkg2"
    engine_pkg.mkdir()
    (engine_pkg / "__init__.py").write_text("cli: object\n")
    (engine_pkg / "cli.py").write_text("# real CLI adapter\n")
    (pkg / "scan_engine.py").write_text("from .engine_pkg2 import cli\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    errors = [m for c, m in findings.errors if c == "engine-cli-boundary"]
    assert len(errors) == 1
    assert "scan_engine.py" in errors[0]


def test_genuine_nested_cli_adapter_alias_still_flagged_with_init_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The attribute-shadowing guard must not blind the gate to a genuine
    submodule import just because the package happens to carry an
    `__init__.py` — only an actual same-named top-level binding inside it
    should suppress the finding."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    compat = pkg / "compat"
    compat.mkdir()
    (compat / "__init__.py").write_text("")
    (compat / "cli.py").write_text("# real CLI adapter\n")
    (pkg / "scan_engine.py").write_text("from .compat import cli\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    errors = [m for c, m in findings.errors if c == "engine-cli-boundary"]
    assert len(errors) == 1
    assert "scan_engine.py" in errors[0]


def test_allowlisted_site_is_not_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A site named in the allowlist is silently accepted (Phase-0 baseline)."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "scan_engine.py").write_text("import click\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(
        gate,
        "ENGINE_CLI_BOUNDARY_ALLOWLIST",
        frozenset({"abicheck/scan_engine.py::import click::1"}),
    )

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    assert not any(c == "engine-cli-boundary" for c, _ in findings.errors)


def test_new_violation_in_an_allowlisted_file_is_still_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The allowlist is occurrence-scoped, not file-scoped — a *second*,
    unlisted identically-shaped import in an otherwise-allowlisted file must
    still fail (only the first `import click` is allowlisted)."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "scan_engine.py").write_text("import click\nimport click\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(
        gate,
        "ENGINE_CLI_BOUNDARY_ALLOWLIST",
        frozenset({"abicheck/scan_engine.py::import click::1"}),
    )

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    errors = [m for c, m in findings.errors if c == "engine-cli-boundary"]
    assert len(errors) == 1
    assert ":2:" in errors[0]


def test_multi_alias_import_flags_every_prohibited_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single ``import`` statement naming more than one prohibited alias
    (`import click, abicheck.cli_new`) must not have the second alias masked
    by the first — each is its own violation, so an allowlisted `import
    click` cannot silently absorb a later-added `cli_*` import riding on the
    same statement."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "scan_engine.py").write_text("import click, abicheck.cli_new\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(
        gate,
        "ENGINE_CLI_BOUNDARY_ALLOWLIST",
        frozenset({"abicheck/scan_engine.py::import click::1"}),
    )

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    errors = [m for c, m in findings.errors if c == "engine-cli-boundary"]
    # "import click" is allowlisted; "import abicheck.cli_new" is not, and
    # must still be flagged even though it shares an AST node with the
    # allowlisted alias.
    assert len(errors) == 1
    assert "abicheck.cli_new" in errors[0]


def test_allowlist_key_is_stable_across_an_unrelated_edit_above_the_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of occurrence-based keys: an edit that shifts the
    import's line number (a new docstring, a blank line, an unrelated
    function added above it) must NOT require rewriting the allowlist."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    key = "abicheck/scan_engine.py::import click::1"
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset({key}))

    (pkg / "scan_engine.py").write_text("import click\n")
    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    assert not any(c == "engine-cli-boundary" for c, _ in findings.errors)

    # Ten unrelated lines inserted above the import — a line-number key
    # would now point at nothing (or a different node) and this would fail.
    (pkg / "scan_engine.py").write_text(
        '"""A new module docstring."""\n\n' + "\n" * 8 + "import click\n"
    )
    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    assert not any(c == "engine-cli-boundary" for c, _ in findings.errors)


# ── Out of scope: frontends and non-engine modules are never flagged ────────


def test_cli_module_importing_click_is_not_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real frontend (``cli*.py``) is on the *other* side of this boundary
    and may import ``click``/``cli_*`` freely — only engine modules are
    covered."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_dump_helpers.py").write_text(
        "import click\nfrom .cli_scan_helpers import foo\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    assert not any(c == "engine-cli-boundary" for c, _ in findings.errors)


def test_engine_module_importing_service_is_not_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An engine module importing another engine/service module (not
    ``click``/``cli_*``) is ordinary, legal composition."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "scan_engine.py").write_text(
        "from .service_input_resolution import resolve_side_snapshot\n"
        "from .checker_types import DiffResult\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    assert not any(c == "engine-cli-boundary" for c, _ in findings.errors)


def test_unrelated_third_party_package_named_abicheck_is_not_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`from vendor.abicheck.cli import helper` names an unrelated
    third-party `vendor.abicheck` package, not this repo's own `abicheck` —
    only a *root* `abicheck` component is this repo's package. Covers both
    the absolute `ImportFrom` branch and its `ast.Import` sibling
    (`import vendor.abicheck.cli`), which has its own, separate root-scope
    check and could independently regress without this second assertion."""
    import scripts.engine_cli_boundary as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "scan_engine.py").write_text("from vendor.abicheck.cli import helper\n")
    (pkg / "service_widget.py").write_text("import vendor.abicheck.cli\n")
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = Findings()
    gate.check_engine_cli_boundary(findings)
    assert not any(c == "engine-cli-boundary" for c, _ in findings.errors)


# ── `_is_engine_module` scope itself ─────────────────────────────────────────


@pytest.mark.parametrize(
    "rel, expected",
    [
        ("abicheck/scan_engine.py", True),
        ("abicheck/service.py", True),
        ("abicheck/service_scan.py", True),
        ("abicheck/buildsource/inline.py", True),
        ("abicheck/buildsource/source_extractors/clang.py", True),
        ("abicheck/artifact_plan.py", True),
        # `artifact_*.py` is the documented family — unlike `service*.py`
        # (which intentionally also matches bare `service.py`), a bare
        # `artifact.py`/`artifacts.py` is NOT an engine module.
        ("abicheck/artifact.py", False),
        ("abicheck/artifacts.py", False),
        # ADR-061 Phase 3's migrated home for that same artifact-application
        # contract (`artifact_plan.py` -> `workflows/artifact/contracts.py`):
        # the whole package tree is engine-layer, not just this one file.
        ("abicheck/workflows/artifact/contracts.py", True),
        ("abicheck/workflows/artifact/__init__.py", True),
        ("abicheck/workflows/artifact/resolve.py", True),
        # A sibling `workflows` package not under `workflows/artifact/` is
        # NOT covered by this predicate -- the rest of `workflows/` isn't
        # (yet) part of the engine-cli-boundary contract.
        ("abicheck/workflows/aggregate/execute.py", False),
        ("abicheck/cli.py", False),
        ("abicheck/cli_dump_helpers.py", False),
        ("abicheck/appcompat.py", False),
        ("abicheck/service.py.txt", False),
        ("scripts/service_helper.py", False),
    ],
)
def test_is_engine_module_scope(rel: str, expected: bool) -> None:
    from scripts.engine_cli_boundary import _is_engine_module

    assert _is_engine_module(rel) is expected
