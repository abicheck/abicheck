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

"""Bug classes about *which evidence a decision is read from*.

Not about the analysis being wrong, and not about a guard's predicate
being mis-stated (``manifest_guards.py``): about a signal that answers two
questions at once, so every consumer reads whichever one its author had in
mind and the other one by accident. No test of either question alone can
see the defect -- the signal is right for one reading of it, every time.

Its own module for the reason ``manifest_guards.py``/
``manifest_report.py``/``manifest_tool_surface.py`` are: ``manifest.py``
sits at its ``architecture/debt.yaml`` ``no_growth`` baseline and is an
assembly point over per-axis entry lists, so a new class goes in a sibling
rather than growing it.
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

__all__ = ["EVIDENCE_BUG_CLASSES"]


EVIDENCE_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="evidence.independent_facts_conflated_into_one_signal",
        invariant=(
            "A declaration's presence in the source surface and its "
            "availability as a dynamic export are independent facts. "
            "Neither may be inferred from the other, and absent evidence "
            "for one may never be read as a negative answer for it. "
            "Concretely, over the nine (declared: yes/no/unknown) x "
            "(exported: yes/no/unknown) quadrants on each side: a "
            "source-removal event requires positive evidence that the new "
            "side no longer declares it, an export-axis event requires "
            "positive evidence on both sides that the export went away "
            "while the declaration stayed, and every quadrant whose "
            "deciding fact is unknown emits neither -- it falls back to "
            "the pre-existing behaviour rather than inventing an answer. "
            "The general shape, of which `Visibility` is one instance: "
            "when one signal encodes two questions, every consumer "
            "answers whichever one its author had in mind and the other "
            "one by accident, and no test of either question alone can "
            "see it."
        ),
        fixed_by=(),
        seed_tests=(
            "tests/test_declaration_surface_properties.py",
            "tests/test_declared_vs_exported_end_to_end.py",
            "tests/test_diff_namespaces.py",
        ),
        public_surfaces=("python-api", "cli"),
        axes={
            "declared": ("yes", "no", "unknown"),
            "exported": ("yes", "no", "unknown"),
            "direction": ("old->new", "new->old", "self"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The two facts are produced only by the header-AST "
                    "backends (castxml/clang) and the export-table "
                    "synthesis paths. A DWARF-derived or PDB-derived "
                    "declaration carries neither, so every consumer there "
                    "still falls back to the conflated `Visibility` proxy "
                    "-- correct-by-construction (no evidence, no change in "
                    "behaviour) but not yet an improvement for those "
                    "tiers. Separately, the audit of the 39 guard sites "
                    "moved only the source-axis ones (`diff_namespaces`) "
                    "onto the declaration fact; the rest were verified to "
                    "mean the *conjunction* and now say so via "
                    "`in_exported_public_api`, which is honest but leaves "
                    "a real question open -- under `-fvisibility=hidden` a "
                    "header declaration that is not exported is usually an "
                    "internal helper, so widening those sites needs a "
                    "public-annotation signal the model does not carry "
                    "yet."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
)
