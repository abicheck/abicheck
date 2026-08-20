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
(``scripts/check_ai_readiness.py``) — Phase 0 of
``docs/contribute/plans/duplication-and-convergence-assessment.md``.

The check ERRORs if an engine-layer module (``scan_engine.py``,
``service*.py``, ``buildsource/**/*.py``) imports ``click`` or a ``cli_*``
sibling module — the CLI is a frontend adapter over the engine, not the
reverse. Pre-existing violations are recorded in
``ENGINE_CLI_BOUNDARY_ALLOWLIST`` (allowlist-and-shrink, mirroring
``IMPORT_CYCLE_ALLOWLIST``'s own design); this file both pins that the real
repository has no *unlisted* violation and that the detection logic itself
actually catches every way an engine module can cross the boundary.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.check_ai_readiness import (  # noqa: E402
    ENGINE_CLI_BOUNDARY_ALLOWLIST,
    Findings,
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
    import scripts.check_ai_readiness as gate

    seen: set[str] = set()
    for path in sorted(gate.PKG.rglob("*.py")):
        rel = gate._rel(path)
        if not gate._is_engine_module(rel):
            continue
        try:
            tree = gate.ast.parse(gate._read(path), filename=rel)
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
        id="scan_engine-import-click",
    ),
    pytest.param(
        "service_widget.py",
        "def go():\n    import click\n    return click\n",
        id="service-lazy-import-click",
    ),
    pytest.param(
        "service_widget.py",
        "from click import echo\n",
        id="service-from-click-import",
    ),
    pytest.param(
        "service_widget.py",
        "from .cli_dump_helpers import _gated_source_label\n",
        id="service-relative-cli-submodule",
    ),
    pytest.param(
        "service_widget.py",
        "from . import cli_scan_helpers\n",
        id="service-relative-import-cli-name",
    ),
    pytest.param(
        "scan_engine.py",
        "from abicheck.cli_buildsource import embed_build_source\n",
        id="scan_engine-absolute-cli-submodule",
    ),
    pytest.param(
        "scan_engine.py",
        "import abicheck.cli_dump_helpers\n",
        id="scan_engine-absolute-import-dotted",
    ),
    pytest.param(
        "service_widget.py",
        "from abicheck import cli_dump_helpers\n",
        id="service-from-abicheck-import-cli-name",
    ),
    pytest.param(
        "artifact_plan.py",
        "import click\n",
        id="artifact-module-import-click",
    ),
    pytest.param(
        "scan_engine.py",
        "from .compat import cli\n",
        id="scan_engine-nested-cli-adapter-relative-alias",
    ),
    pytest.param(
        "scan_engine.py",
        "from .compat.cli import compat_group\n",
        id="scan_engine-nested-cli-adapter-relative-submodule",
    ),
    pytest.param(
        "scan_engine.py",
        "import abicheck.compat.cli\n",
        id="scan_engine-nested-cli-adapter-absolute-dotted",
    ),
    pytest.param(
        "service_widget.py",
        "from abicheck.compat import cli\n",
        id="service-nested-cli-adapter-absolute-alias",
    ),
    pytest.param(
        "service_widget.py",
        "from abicheck.compat.cli import compat_group\n",
        id="service-nested-cli-adapter-absolute-submodule",
    ),
]


@pytest.mark.parametrize("filename, source", _ENGINE_VIOLATION_CASES)
def test_gate_flags_violation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    source: str,
) -> None:
    """The gate is not a no-op: each way of crossing the boundary is caught."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / filename).write_text(source)
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = gate.Findings()
    gate.check_engine_cli_boundary(findings)
    errors = [m for c, m in findings.errors if c == "engine-cli-boundary"]
    assert len(errors) == 1
    assert filename in errors[0]


def test_allowlisted_site_is_not_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A site named in the allowlist is silently accepted (Phase-0 baseline)."""
    import scripts.check_ai_readiness as gate

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

    findings = gate.Findings()
    gate.check_engine_cli_boundary(findings)
    assert not any(c == "engine-cli-boundary" for c, _ in findings.errors)


def test_new_violation_in_an_allowlisted_file_is_still_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The allowlist is occurrence-scoped, not file-scoped — a *second*,
    unlisted identically-shaped import in an otherwise-allowlisted file must
    still fail (only the first `import click` is allowlisted)."""
    import scripts.check_ai_readiness as gate

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

    findings = gate.Findings()
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
    import scripts.check_ai_readiness as gate

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

    findings = gate.Findings()
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
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    key = "abicheck/scan_engine.py::import click::1"
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset({key}))

    (pkg / "scan_engine.py").write_text("import click\n")
    findings = gate.Findings()
    gate.check_engine_cli_boundary(findings)
    assert not any(c == "engine-cli-boundary" for c, _ in findings.errors)

    # Ten unrelated lines inserted above the import — a line-number key
    # would now point at nothing (or a different node) and this would fail.
    (pkg / "scan_engine.py").write_text(
        '"""A new module docstring."""\n\n' + "\n" * 8 + "import click\n"
    )
    findings = gate.Findings()
    gate.check_engine_cli_boundary(findings)
    assert not any(c == "engine-cli-boundary" for c, _ in findings.errors)


# ── Out of scope: frontends and non-engine modules are never flagged ────────


def test_cli_module_importing_click_is_not_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real frontend (``cli*.py``) is on the *other* side of this boundary
    and may import ``click``/``cli_*`` freely — only engine modules are
    covered."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_dump_helpers.py").write_text(
        "import click\nfrom .cli_scan_helpers import foo\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = gate.Findings()
    gate.check_engine_cli_boundary(findings)
    assert not any(c == "engine-cli-boundary" for c, _ in findings.errors)


def test_engine_module_importing_service_is_not_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An engine module importing another engine/service module (not
    ``click``/``cli_*``) is ordinary, legal composition."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "scan_engine.py").write_text(
        "from .service_input_resolution import resolve_side_snapshot\n"
        "from .checker_types import DiffResult\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "ENGINE_CLI_BOUNDARY_ALLOWLIST", frozenset())

    findings = gate.Findings()
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
        ("abicheck/cli.py", False),
        ("abicheck/cli_dump_helpers.py", False),
        ("abicheck/appcompat.py", False),
        ("abicheck/service.py.txt", False),
        ("scripts/service_helper.py", False),
    ],
)
def test_is_engine_module_scope(rel: str, expected: bool) -> None:
    from scripts.check_ai_readiness import _is_engine_module

    assert _is_engine_module(rel) is expected
