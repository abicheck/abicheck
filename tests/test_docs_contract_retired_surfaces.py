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

"""Unit tests for check_docs_contract.py's two surface-drift sweeps.

Split out of ``test_docs_contract.py`` purely to keep that file under the
AI-readiness file-size cap; the module loader and monkeypatch conventions
are identical. Both sweeps run over the same target set -- the hand-authored
``docs/`` tree plus the ``examples/case*/README.md`` generator sources behind
the published case pages:

* ``_check_retired_surfaces`` -- a page still naming a retired CLI
  flag/command/file by its dead spelling;
* ``_check_config_keys_as_cli_operands`` -- a documented command line passing
  a ``.abicheck.yml`` key as argv, which is what a mechanical
  flag-to-config-key rewrite produces when it reaches an example instead of
  prose.
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

    Both sweeps read ``examples/case*/README.md`` and
    ``tests/scenarios/*.yaml`` alongside ``docs/``. Every test here already
    redirects ``DOCS`` to a fixture tree; without the same redirect for those
    two they would keep scanning the real trees, so an unrelated stale flag in
    one case README or scenario would fail assertions about a fixture page.
    Tests exercising those arms override this.
    """
    empty = tmp_path / "_no_examples"
    empty.mkdir()
    monkeypatch.setattr(dc, "CASES", empty)
    no_scenarios = tmp_path / "_no_scenarios"
    no_scenarios.mkdir()
    monkeypatch.setattr(dc, "SCENARIOS", no_scenarios)


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
    cases = tmp_path / "catalog" / "cases"
    (cases / "case999_demo").mkdir(parents=True)
    (cases / "case999_demo" / "README.md").write_text(
        "# Case 999\n\nRun `abicheck dump lib.so --source-abi-cache /tmp/c`.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dc, "CASES", cases)
    f = dc.Findings()
    dc._check_retired_surfaces(f)
    assert len(f.warnings) == 1, f.warnings
    # Keyed repo-relative, so an allowlist entry is unambiguous about which
    # tree it exempts -- a docs-relative key could never spell this path.
    assert "catalog/cases/case999_demo/README.md" in f.warnings[0][1]


def test_retired_surfaces_ignores_non_case_example_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Only the per-case READMEs are generator sources; examples/README.md is
    # itself partly generated from ground_truth.json and is not published as
    # a case page.
    monkeypatch.setattr(dc, "DOCS", tmp_path / "docs")
    (tmp_path / "docs").mkdir()
    cases = tmp_path / "catalog" / "cases"
    cases.mkdir(parents=True)
    (cases / "README.md").write_text(
        "Historic note: `--source-abi-cache` used to exist.\n", encoding="utf-8"
    )
    monkeypatch.setattr(dc, "CASES", cases)
    f = dc.Findings()
    dc._check_retired_surfaces(f)
    assert f.warnings == []


# --- config keys passed as CLI operands -----------------------------------


def _page(tmp_path: Path, body: str) -> None:
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "page.md").write_text(body, encoding="utf-8")


def test_a_config_key_in_a_documented_command_is_flagged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The exact shape that shipped: a flag demoted to a config-only key,
    mechanically rewritten everywhere it was named -- correct in the prose
    naming the key, wrong in every command line that used to pass the flag,
    where Click reads it as two unexpected positional operands and exits 64
    (Codex review)."""
    monkeypatch.setattr(dc, "DOCS", tmp_path / "docs")
    _page(
        tmp_path,
        "# Page\n\n```bash\n"
        "abicheck compare old.json new.json severity.addition: error\n```\n",
    )
    f = dc.Findings()
    dc._check_config_keys_as_cli_operands(f)
    assert len(f.warnings) == 1, f.warnings
    assert "severity.addition" in f.warnings[0][1]
    assert "docs/page.md:4" in f.warnings[0][1]


def test_a_backslash_continued_command_is_joined_before_matching(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Every real recipe wraps; a per-line scan would miss the continuation
    # lines, which is where the operands actually sat in five of the eight
    # shipped instances.
    monkeypatch.setattr(dc, "DOCS", tmp_path / "docs")
    _page(
        tmp_path,
        "# Page\n\n```bash\nabicheck compare old.so new.so \\\n"
        "  --suppress s.yaml \\\n"
        "  suppression.strict: true\n```\n",
    )
    f = dc.Findings()
    dc._check_config_keys_as_cli_operands(f)
    assert len(f.warnings) == 1, f.warnings
    assert "suppression.strict" in f.warnings[0][1]


def test_a_config_key_in_extra_args_is_flagged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # `extra-args` is raw argv by another name, so the same token fails the
    # same way -- one step removed from any `abicheck` line to match on.
    monkeypatch.setattr(dc, "DOCS", tmp_path / "docs")
    _page(
        tmp_path,
        "# Page\n\n```yaml\n"
        "extra-args: 'suppression.strict: true'\n```\n",
    )
    f = dc.Findings()
    dc._check_config_keys_as_cli_operands(f)
    assert len(f.warnings) == 1, f.warnings
    assert "extra-args" in f.warnings[0][1]


def test_the_same_key_in_a_config_block_is_not_flagged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The half that must stay quiet, and the reason the check is scoped to
    invocations: in a YAML config block, or in prose naming the key, this
    exact token is the correct spelling. A check that flagged it everywhere
    would be unusable on the very pages that document these keys."""
    monkeypatch.setattr(dc, "DOCS", tmp_path / "docs")
    _page(
        tmp_path,
        "# Page\n\nSet `severity.addition: error` to gate on additions.\n\n"
        "```yaml\nseverity:\n  addition: error\n```\n\n"
        "```bash\nabicheck compare old.json new.json --config .abicheck.yml\n```\n",
    )
    f = dc.Findings()
    dc._check_config_keys_as_cli_operands(f)
    assert f.warnings == []


def test_prose_naming_the_tool_is_not_read_as_a_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # "abicheck reads .abicheck.yml ..." is a sentence, not an invocation --
    # the subcommand requirement is what keeps it out.
    monkeypatch.setattr(dc, "DOCS", tmp_path / "docs")
    _page(
        tmp_path,
        "# Page\n\nabicheck resolves severity.addition: error from config.\n",
    )
    f = dc.Findings()
    dc._check_config_keys_as_cli_operands(f)
    assert f.warnings == []


def test_a_real_flag_value_is_not_mistaken_for_a_config_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # `--write json=out.json`, `--header old=include/`, a version like
    # `v1.2:` -- none is a `key.subkey:` operand, and flagging one would make
    # the check fire on correct recipes.
    monkeypatch.setattr(dc, "DOCS", tmp_path / "docs")
    _page(
        tmp_path,
        "# Page\n\n```bash\nabicheck compare old.json new.json "
        "--write json=out.json --header old=include/ --ast-frontend new=clang\n```\n",
    )
    f = dc.Findings()
    dc._check_config_keys_as_cli_operands(f)
    assert f.warnings == []


def test_example_case_readmes_are_swept_for_operands_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Same target set as the retired-surface sweep, for the same reason: a
    # case README is the generator source for a published page.
    monkeypatch.setattr(dc, "DOCS", tmp_path / "docs")
    (tmp_path / "docs").mkdir()
    cases = tmp_path / "catalog" / "cases"
    (cases / "case999_demo").mkdir(parents=True)
    (cases / "case999_demo" / "README.md").write_text(
        "```bash\nabicheck compare a.json b.json scope.show_redundant: true\n```\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dc, "CASES", cases)
    f = dc.Findings()
    dc._check_config_keys_as_cli_operands(f)
    assert len(f.warnings) == 1, f.warnings
    assert "catalog/cases/case999_demo/README.md" in f.warnings[0][1]


def test_the_real_repo_has_no_config_key_operands() -> None:
    # The smoke half: eight of these shipped across five pages before a
    # reviewer read one.
    f = dc.Findings()
    dc._check_config_keys_as_cli_operands(f)
    assert f.warnings == [], f.warnings


def test_retired_surfaces_scans_the_scenario_catalog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A scenario's ``flow:`` is a command a reader is meant to run.

    The catalogue's own structural tests check that a flow *has* an automated
    counterpart, not that the command it prints still parses -- so a scenario
    kept advertising a removed `scan --compile-db` while both those tests and
    this sweep stayed green (Codex review).
    """
    monkeypatch.setattr(dc, "DOCS", tmp_path / "docs")
    (tmp_path / "docs").mkdir()
    scenarios = tmp_path / "tests" / "scenarios"
    scenarios.mkdir(parents=True)
    (scenarios / "gating.yaml").write_text(
        "- id: SC-X\n  flow:\n"
        "    - abicheck dump lib.so --source-abi-cache /tmp/c\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dc, "SCENARIOS", scenarios)
    f = dc.Findings()
    dc._check_retired_surfaces(f)
    assert len(f.warnings) == 1, f.warnings
    assert "tests/scenarios/gating.yaml" in f.warnings[0][1]


def test_the_real_scenario_catalog_is_in_the_target_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wiring half: the real catalogue is actually reached.

    The autouse fixture isolates ``SCENARIOS`` for every other test here, so
    without this one a regression that dropped the arm entirely would leave
    the whole file green.
    """
    monkeypatch.undo()
    keys = {rel for _, rel in dc._retired_surface_scan_targets()}
    scenario_keys = {k for k in keys if k.startswith("tests/scenarios/")}
    assert scenario_keys, sorted(keys)[:5]
    assert "tests/scenarios/ci_gating.yaml" in scenario_keys
