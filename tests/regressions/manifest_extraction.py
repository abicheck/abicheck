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

"""Bug classes about *what a header-AST backend lets into the snapshot*.

A themed sibling of `manifest.py` (see there for what this registry is and
is not), split out because `manifest.py` sits at its `architecture/debt.yaml`
`no_growth` baseline. The classes here are about a backend's declaration
lists admitting something that is not part of the parsed surface -- a wrong
answer that shows up as a fabricated identity or a refused dump, not as a
wrong computation over the right declarations.
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

__all__ = ["EXTRACTION_BUG_CLASSES"]


EXTRACTION_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="extraction.function_local_declaration_leaks_into_surface",
        invariant=(
            "A declaration local to a function body (a local typedef, "
            "alias, enum, class, or any member of a local class, at any "
            "nesting depth) never enters a header-AST snapshot's "
            "declaration lists, whichever backend parsed it: it has no "
            "linkage and no namespace-scope identity to give it. Both "
            "backends therefore agree on the declaration set for the same "
            "header."
        ),
        fixed_by=(1350,),
        seed_tests=("tests/test_castxml_function_local_decls.py",),
        axes={"frontend": ("castxml", "clang")},
        known_gaps=(
            KnownGap(
                description=(
                    "A local class returned by value from an inline "
                    "function (a 'Voldemort type') has an ABI-relevant "
                    "layout, but neither backend records it: a layout "
                    "change there is visible only as the function's "
                    "return-type spelling, not as a type change."
                ),
                reference="abicheck/extract/headers/castxml/context.py",
            ),
        ),
    ),
    BugClass(
        id="scoping.declaration_kind_forwarded_unfiltered",
        invariant=(
            "Dump-time dependency scoping (and --exclude-header) applies one "
            "header-origin rule to every declaration kind a snapshot "
            "carries: an entry whose declaring header is a dependency is "
            "either dropped or retained by a stated reference rule, never "
            "carried through verbatim because the scoping pass's closing "
            "dataclasses.replace did not name its field. Unknown origin "
            "means kept, never dropped."
        ),
        fixed_by=(1001,),
        seed_tests=(
            "tests/test_flat_map_dependency_scope.py",
            "tests/test_exclude_header_flat_maps_integration.py",
        ),
        public_surfaces=("cli",),
        axes={
            "frontend": ("castxml", "clang"),
            "kind": ("constant", "typedef", "typedef_qualified", "semantic_ir"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Attribution is runtime-only, so scoping a *loaded* "
                    "snapshot (whose maps decode as plain dicts) still "
                    "leaves its constants/typedefs untouched; nothing "
                    "enumerates AbiSnapshot fields to prove no further "
                    "kind is forwarded unfiltered."
                ),
                reference="abicheck/model/declaration_headers.py",
            ),
        ),
    ),
)
