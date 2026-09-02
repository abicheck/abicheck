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

"""``reclassify.py``'s import-cycle-workaround removal (ADR-063 D10,
implementation plan Phase 9).

Split into its own file rather than appended to ``tests/test_reclassify.py``
(Codex review, PR #1002): that module is a ``no_growth``-debt-tracked
legacy test module (``architecture/debt.yaml``), so any addition there fails
`scripts/check_architecture.py`'s `debt-no-growth` check outright, per
AGENTS.md's "Files that are large — edit carefully" section -- the fix is
to give new content a properly-scoped module, not to raise the baseline.
"""

from __future__ import annotations

import ast
import inspect

import abicheck.reclassify as reclassify_module


class TestNoImportlibWorkaround:
    """ADR-063 D10 (implementation plan Phase 9): the cycle
    ``policy_file -> reclassify -> suppression -> checker_types ->
    policy_file`` that motivated ``_suppression_cls()``'s runtime
    ``importlib.import_module`` resolution no longer exists — ``reclassify.py``
    imports ``abicheck.policy.selectors`` (a leaf module with zero dependency
    on ``suppression.py``/``checker_types.py``/``policy_file.py``/
    ``finding_identity.py``) statically instead. Confirmed to fail against
    the pre-Phase-9 code, which had exactly one such call
    (``_suppression_cls()``, per that module's own docstring)."""

    def test_reclassify_module_contains_no_importlib_import_module_call(self) -> None:
        source = inspect.getsource(reclassify_module)
        tree = ast.parse(source)
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "import_module"
        ]
        assert not calls, (
            "reclassify.py must not resolve Suppression (or anything else) "
            "via importlib.import_module -- the import cycle that workaround "
            "existed for no longer exists (ADR-063 D10)"
        )

    def test_reclassify_module_does_not_import_importlib(self) -> None:
        source = inspect.getsource(reclassify_module)
        tree = ast.parse(source)
        imports_importlib = any(
            isinstance(node, ast.Import) and any(a.name == "importlib" for a in node.names)
            for node in ast.walk(tree)
        )
        assert not imports_importlib
