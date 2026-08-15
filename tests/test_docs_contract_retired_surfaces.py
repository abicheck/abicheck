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

"""Unit tests for check_docs_contract.py's retired-surface sweep.

Split out of ``test_docs_contract.py`` purely to keep that file under the
AI-readiness file-size cap; the module loader and monkeypatch conventions
are identical. The sweep is the check that flags a manual page still naming
a retired CLI flag/command/file by its dead spelling, over both the
hand-authored ``docs/`` tree and the ``examples/case*/README.md`` generator
sources behind the published case pages.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_GATE_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "check_docs_contract.py"
)
_spec = importlib.util.spec_from_file_location("check_docs_contract", _GATE_PATH)
assert _spec and _spec.loader
dc = importlib.util.module_from_spec(_spec)
sys.modules["check_docs_contract"] = dc
_spec.loader.exec_module(dc)


def test_retired_surfaces_flags_dead_path_outside_allowed_pages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    (tmp_path / "use").mkdir()
    (tmp_path / "use" / "page.md").write_text(
        "# Page\n\nSet `--source-abi-cache` to reuse the L4 cache.\n",
        encoding="utf-8",
    )
    f = dc.Findings()
    dc._check_retired_surfaces(f)
    assert len(f.warnings) == 1
    assert "use/page.md" in f.warnings[0][1]
    assert "--source-abi-cache" in f.warnings[0][1]


def test_retired_surfaces_allows_its_own_allowlisted_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    (tmp_path / "use").mkdir()
    (tmp_path / "use" / "build-evidence-setup.md").write_text(
        "# Page\n\n`--source-abi-cache` (removed, historical framing).\n",
        encoding="utf-8",
    )
    f = dc.Findings()
    dc._check_retired_surfaces(f)
    assert f.warnings == []


def test_retired_surfaces_exempts_adr_and_plans_trees(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    (tmp_path / "contribute" / "adr").mkdir(parents=True)
    (tmp_path / "contribute" / "adr" / "001-x.md").write_text(
        "# ADR\n\nSee `mcp_server.py` (removed).\n", encoding="utf-8"
    )
    f = dc.Findings()
    dc._check_retired_surfaces(f)
    assert f.warnings == []


def test_retired_surfaces_ignores_unrelated_pages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    (tmp_path / "use").mkdir()
    (tmp_path / "use" / "page.md").write_text(
        "# Page\n\nOrdinary content with no retired surface names.\n",
        encoding="utf-8",
    )
    f = dc.Findings()
    dc._check_retired_surfaces(f)
    assert f.warnings == []


def test_retired_surfaces_flags_dead_path_inside_a_fenced_command_example(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unlike _check_stale_process_language, this check must NOT blank
    fenced code before scanning -- a stale command inside a ```bash example
    is exactly the worst place to miss one, since a reader is likely to
    copy-paste it verbatim."""
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    (tmp_path / "use").mkdir()
    (tmp_path / "use" / "page.md").write_text(
        "# Page\n\n```bash\nabicheck-mcp --version\n```\n",
        encoding="utf-8",
    )
    f = dc.Findings()
    dc._check_retired_surfaces(f)
    assert len(f.warnings) == 1
    assert "use/page.md" in f.warnings[0][1]
    assert "abicheck-mcp" in f.warnings[0][1]


def test_retired_surfaces_flags_bare_top_level_flag_spelling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The registry's sub-option patterns (--source-abi-cache-dir, etc.)
    don't cover the bare --source-abi/--source-graph spellings a
    copy-pasted `collect --source-abi` invocation would use."""
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    (tmp_path / "use").mkdir()
    (tmp_path / "use" / "page.md").write_text(
        "# Page\n\n```bash\nabicheck collect --source-abi\n```\n",
        encoding="utf-8",
    )
    f = dc.Findings()
    dc._check_retired_surfaces(f)
    assert len(f.warnings) == 1
    assert "--source-abi" in f.warnings[0][1]


def test_retired_surfaces_flags_a_second_independent_occurrence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A later, independent bare `--source-abi` mention must still be
    flagged even when an earlier `--source-abi-cache-dir` occurrence already
    consumed the first `.find()` hit for the shorter pattern -- overlap
    suppression should only swallow a match nested inside another match's
    span, never a distinct, later occurrence."""
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    (tmp_path / "use").mkdir()
    (tmp_path / "use" / "page.md").write_text(
        "# Page\n\nSet `--source-abi-cache-dir` for caching.\n\n"
        "Elsewhere, pass `--source-abi` on its own.\n",
        encoding="utf-8",
    )
    f = dc.Findings()
    dc._check_retired_surfaces(f)
    assert len(f.warnings) == 2


def test_retired_surfaces_exempts_historical_lifecycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    (tmp_path / "use").mkdir()
    (tmp_path / "use" / "page.md").write_text(
        "---\nlifecycle: historical\n---\n\n# Page\n\n`mcp_server.py` (removed).\n",
        encoding="utf-8",
    )
    f = dc.Findings()
    dc._check_retired_surfaces(f)
    assert f.warnings == []


def test_retired_surfaces_exempts_migration_lifecycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    (tmp_path / "use").mkdir()
    (tmp_path / "use" / "page.md").write_text(
        "---\nlifecycle: migration\n---\n\n# Page\n\n`abicheck-mcp` (removed).\n",
        encoding="utf-8",
    )
    f = dc.Findings()
    dc._check_retired_surfaces(f)
    assert f.warnings == []


@pytest.fixture(autouse=True)
def _isolated_examples_tree(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Point the retired-surface sweep's examples arm at an empty tree.

    ``_check_retired_surfaces`` reads ``examples/case*/README.md`` alongside
    ``docs/`` (those READMEs are the generator source for the published case
    pages, which carry the generated marker and are skipped). Every test here
    already redirects ``DOCS`` to a fixture tree; without the same redirect
    for ``EXAMPLES`` they would keep scanning the real catalogue, so an
    unrelated stale flag in one case README would fail assertions about a
    fixture page. Tests exercising the examples arm override this.
    """
    empty = tmp_path / "_no_examples"
    empty.mkdir()
    monkeypatch.setattr(dc, "EXAMPLES", empty)


def test_retired_surfaces_scans_example_case_readmes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The generator source is swept, not just the generated page.

    `gen_examples_docs.py` publishes each `examples/caseNN_*/README.md` into
    `docs/reference/examples/`, where the generated marker makes the sweep
    skip it -- so scanning only `docs/` let a retired flag in a case README
    reproduce into a public page on the next regeneration while this guard
    stayed green, which is exactly what happened to Case 148's `--compile-db`
    recommendation (Codex review).
    """
    monkeypatch.setattr(dc, "DOCS", tmp_path / "docs")
    (tmp_path / "docs").mkdir()
    examples = tmp_path / "examples"
    (examples / "case999_demo").mkdir(parents=True)
    (examples / "case999_demo" / "README.md").write_text(
        "# Case 999\n\nRun `abicheck dump lib.so --source-abi-cache /tmp/c`.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dc, "EXAMPLES", examples)
    f = dc.Findings()
    dc._check_retired_surfaces(f)
    assert len(f.warnings) == 1, f.warnings
    # Keyed repo-relative, so an allowlist entry is unambiguous about which
    # tree it exempts -- a docs-relative key could never spell this path.
    assert "examples/case999_demo/README.md" in f.warnings[0][1]


def test_retired_surfaces_ignores_non_case_example_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Only the per-case READMEs are generator sources; examples/README.md is
    # itself partly generated from ground_truth.json and is not published as
    # a case page.
    monkeypatch.setattr(dc, "DOCS", tmp_path / "docs")
    (tmp_path / "docs").mkdir()
    examples = tmp_path / "examples"
    examples.mkdir()
    (examples / "README.md").write_text(
        "Historic note: `--source-abi-cache` used to exist.\n", encoding="utf-8"
    )
    monkeypatch.setattr(dc, "EXAMPLES", examples)
    f = dc.Findings()
    dc._check_retired_surfaces(f)
    assert f.warnings == []
