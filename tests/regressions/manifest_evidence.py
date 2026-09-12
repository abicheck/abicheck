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

"""Bug classes about *which evidence* a decision was read from.

A sibling of `manifest_guards.py`/`manifest_report.py`/
`manifest_tool_surface.py` (see `manifest.py` for what this registry is and
is not). The classes here are not about a wrong computation over the right
input: they are about a detector being handed, or reaching for, an
observation that does not answer the question it is asking.
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

__all__ = ["EVIDENCE_BUG_CLASSES"]


EVIDENCE_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="evidence.export_presence_as_declaration_presence",
        invariant=(
            "Three independent observations about a declaration -- (a) a "
            "header this run parsed declares it, (b) it belongs to the "
            "promised public contract for this run's scope/contract "
            "selection, (c) the artifact's export table carries a symbol "
            "for it -- are answered separately, from the evidence that "
            'actually bears on each, and each keeps a real "unknown". A '
            "detector filters on the one it needs: a source-declaration "
            "population is built from (a)/(b) and never from (c), so a "
            "version-script or -fvisibility change that stops exporting a "
            "byte-identical declaration is reported as the binary-axis "
            "change it is and never as a removed source API; a promised, "
            "declared, unexported inline function is in the public surface "
            "rather than misfiled out of it. Weaker evidence narrows the "
            "conclusion: a snapshot with no headers answers (a) unknown, "
            "never False, and discarding header evidence (an evidence-depth "
            "projection) returns it to unknown rather than asserting its "
            "negation."
        ),
        # Reported against a real comparison alongside the three
        # name-shape defects PR #1231 fixed; recorded as its own gap at the
        # time (it needed a model/schema change, not a call-site patch) and
        # closed by the split this entry registers.
        fixed_by=(1231,),
        seed_tests=("tests/test_surface_fact_split.py",),
        axes={
            "fact": ("declared_in_headers", "in_public_contract", "binary_exported"),
            "state": ("true", "false", "unknown"),
            "producer": ("header-ast", "debug-info", "export-table", "projection"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The seed test enumerates the whole 3x7 fact domain and "
                    "both real detector outcomes through `checker.compare`, "
                    "but every snapshot it builds is constructed in-process. "
                    "The producers are covered at their own boundary "
                    "(`export_symbol_identity`, "
                    "`extract.surface_fact_producers`, the provenance pass) "
                    "rather than by a real castxml/clang dump of a library "
                    "whose two builds differ only in a version script -- the "
                    "exact shape the original report came from -- so a "
                    "regression in how a *live* header-AST dump populates "
                    "the three facts would be caught by the integration "
                    "lanes, not by this class's own generalized test. "
                    "Adding that case needs a fixture pair with a version "
                    "script, which the marker lanes (`integration`) own."
                ),
                reference=(
                    "docs/contribute/known-gaps.md, the `Visibility.PUBLIC` entry"
                ),
            ),
        ),
    ),
)
