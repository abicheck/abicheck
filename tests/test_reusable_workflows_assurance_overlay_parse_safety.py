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

"""Two Codex review findings (fresh evidence, PR #1222) on
``actions/check-target/action.yml``'s "Generate assurance-overlay config"
step's explicit-``build-config`` handling branch -- both about how that
branch's ``yaml.safe_load(fh)`` call and its result are handled, distinct
from ``TestAssuranceOverlayGenerationExecuted`` in
``tests/test_reusable_workflows_require_complete_analysis.py`` (which
covers *schema*-validation failures on an already-parsed mapping, not
*parse* failures or non-mapping documents). Split into its own module
purely to keep that already-near-the-cap file under
``architecture/debt.yaml``'s test-file line limit -- this repo's own "move
responsibility, don't trim to fit" convention (root ``AGENTS.md``).

**Finding 1 (P1, escaping):** a malformed explicit ``build-config`` (e.g.
bad indentation) fails inside the bare ``yaml.safe_load(fh)`` call itself,
before ``_validate_or_exit``/``_gha_escape`` ever run. PyYAML's own error
``Mark`` embeds the source's filename verbatim in the exception's
``str()``, so an unguarded ``raise``/``SystemExit`` here could put
attacker-controlled text (a filename containing a newline followed by a
workflow command) on the runner's output completely unescaped -- the same
``trust_boundary.shell_workflow_injection`` class already fixed twice
elsewhere on this branch (``action/run.sh``'s own ``_gha_escape``,
``actions/check-target/validate-inputs.sh``'s own ``_fail``, see
``TestAssuranceOverlayEscapesWorkflowCommandInjection`` in
``tests/test_action_check_target_assurance_validation.py``). Fixed by
wrapping the parse in a ``try``/``except`` and routing the message through
the existing ``_gha_escape`` helper, mirroring the sources-root branch's
own pre-existing identical guard
(``test_malformed_sources_root_yaml_fails_loud`` in
``tests/test_reusable_workflows_require_complete_analysis.py``).

**Finding 2 (P2, non-mapping handling):** when the explicit (or
checkout-discovered) config parses to a YAML scalar or list, the branch
used to reject it outright, whereas ``load_build_config()`` -- the real
CLI's own loading path -- deliberately treats every non-mapping document as
an empty ``BuildConfig()`` and proceeds. Adding
``analysis-assurance-complete: true`` therefore turned an otherwise-accepted
invocation into an operational failure purely as a side effect of this
overlay's own generation. Fixed to handle a non-mapping document as an
empty base, matching both the ordinary CLI and the sources-root branch's
own pre-existing non-mapping handling
(``TestApplySourcesRootConfigBlocks.
test_non_mapping_sources_doc_also_clears_every_named_block`` in
``tests/test_action_config_overlay.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from _assurance_overlay_exec import _run_overlay, _written_overlay
from _workflow_exec import make_workspace, requires_newline_in_filenames


def _run_explicit(tmp_path: Path, base_config_yaml: str) -> Any:
    workspace = make_workspace(tmp_path)
    config_path = workspace / "build-config.yml"
    config_path.write_text(base_config_yaml, encoding="utf-8")
    return _run_overlay(workspace, {"BASE_CONFIG": str(config_path)})


class TestAssuranceOverlayNonMappingExplicitDocument:
    """Finding 2: a non-mapping explicit ``build-config`` document must
    merge as an empty base and succeed, not fail the step."""

    def test_list_document_is_treated_as_an_empty_base(self, tmp_path: Path) -> None:
        result = _run_explicit(tmp_path, "- just\n- a\n- list\n")
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written == {"assurance": {"require_complete": True}}

    def test_scalar_string_document_is_treated_as_an_empty_base(
        self, tmp_path: Path
    ) -> None:
        result = _run_explicit(tmp_path, "just-a-string\n")
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written == {"assurance": {"require_complete": True}}

    def test_scalar_boolean_document_is_treated_as_an_empty_base(
        self, tmp_path: Path
    ) -> None:
        result = _run_explicit(tmp_path, "true\n")
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written == {"assurance": {"require_complete": True}}

    def test_non_mapping_document_still_merges_with_other_overlay_behavior(
        self, tmp_path: Path
    ) -> None:
        """An empty base from a non-mapping document is genuinely empty --
        it must not, for example, retain some ghost of the rejected
        document's own top-level keys."""
        result = _run_explicit(tmp_path, "[1, 2, 3]\n")
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert list(written.keys()) == ["assurance"]


class TestAssuranceOverlayDiscoveredNonMappingDocument:
    """Finding 2 also names the checkout-discovered branch: a
    non-mapping *discovered* ``.abicheck.yml`` (no explicit ``build-config``
    given at all) must merge as an empty base too, for the identical
    reason -- the discovered-config branch shares the same
    ``load_build_config()`` contract as the explicit one."""

    def test_discovered_scalar_document_is_treated_as_an_empty_base(
        self, tmp_path: Path
    ) -> None:
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text("true\n", encoding="utf-8")
        result = _run_overlay(workspace, {"BASE_CONFIG": ""})
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written == {"assurance": {"require_complete": True}}

    def test_discovered_list_document_is_treated_as_an_empty_base(
        self, tmp_path: Path
    ) -> None:
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text("- a\n- b\n", encoding="utf-8")
        result = _run_overlay(workspace, {"BASE_CONFIG": ""})
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written == {"assurance": {"require_complete": True}}


class TestAssuranceOverlayExplicitConfigParseFailureEscaping:
    """Finding 1: a YAML *parse* failure (not a schema-validation failure,
    which ``TestAssuranceOverlayGenerationExecuted.
    test_malformed_assurance_block_fails_loud_not_silently_discarded``
    already covers) on the explicit ``build-config`` path must fail loud
    through the existing ``_gha_escape`` helper, never a raw traceback."""

    def test_malformed_yaml_fails_loud(self, tmp_path: Path) -> None:
        # A tab character where YAML requires spaces for indentation is
        # rejected by PyYAML's scanner (a genuine parse failure, not a
        # schema violation) -- reaches `yaml.safe_load` before
        # `_validate_or_exit` is ever called.
        result = _run_explicit(tmp_path, "foo:\n\tbar: 1\n")
        assert result.returncode != 0
        assert "::error::" in result.stderr
        assert "config-path" not in result.outputs

    @requires_newline_in_filenames
    def test_malformed_yaml_escapes_workflow_command_injection(
        self, tmp_path: Path
    ) -> None:
        """Proven end to end against a REAL file named with an embedded
        newline + ``::add-mask::`` line, per this repo's own "assert
        behavior, not text" rule (#705 -> #758) -- the same pattern
        ``TestAssuranceOverlayEscapesWorkflowCommandInjection`` in
        ``tests/test_action_check_target_assurance_validation.py`` already
        established for ``validate-inputs.sh``'s own ``_fail``. PyYAML's
        own error ``Mark`` embeds the source's filename verbatim in the
        exception's ``str()``, so a malicious filename reaches the
        unguarded ``yaml.safe_load`` failure before any escaping runs."""
        workspace = make_workspace(tmp_path)
        evil_name = "build-config.yml\n::add-mask::pwned"
        evil_path = workspace / evil_name
        evil_path.write_text("foo:\n\tbar: 1\n", encoding="utf-8")
        result = _run_overlay(workspace, {"BASE_CONFIG": str(evil_path)})
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "::error::" in combined
        assert "config-path" not in result.outputs
        # The embedded newline in the malicious filename must never reach
        # the annotation stream unescaped -- every line but the first must
        # NOT itself start a new `::`-prefixed workflow command (a real
        # second `::add-mask::` line would mean the injection landed).
        lines = combined.splitlines()
        assert not any(line.startswith("::") for line in lines[1:])
        assert "%0A::add-mask::pwned" in combined


class TestAssuranceOverlayDiscoveredConfigParseFailureEscaping:
    """The checkout-discovered branch shares the identical unguarded
    ``yaml.safe_load`` call site as the explicit branch above -- fixed the
    same way, and covered here so the fix cannot regress independently on
    either branch."""

    def test_malformed_discovered_yaml_fails_loud_escaped(self, tmp_path: Path) -> None:
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text("foo:\n\tbar: 1\n", encoding="utf-8")
        result = _run_overlay(workspace, {"BASE_CONFIG": ""})
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "::error::" in combined
        assert "config-path" not in result.outputs
        lines = combined.splitlines()
        assert not any(line.startswith("::") for line in lines[1:])
