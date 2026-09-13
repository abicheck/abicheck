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

"""Bug classes about *configuration input*: the values a run is given.

A themed sibling of `manifest.py`, following the same per-area split
`manifest_guards.py`/`manifest_report.py`/`manifest_tool_surface.py`/
`manifest_evidence.py` already establish. The question these classes share
is what a supplied value means -- across front ends, across consumers, and
across the whole domain of values a user might plausibly write.
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

CONFIG_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="config.env_flag_value_domain",
        invariant=(
            "Every ABICHECK_* boolean environment knob answers the same "
            "value domain: 1/true/yes/on is on, 0/false/no/off is off "
            "(case- and whitespace-insensitive) whichever way the knob's own "
            "default points, and unset/empty/unrecognized resolves to that "
            "default -- never to its opposite. One registered parser, no "
            "per-call-site token set."
        ),
        fixed_by=(1278,),
        seed_tests=("tests/test_env_flags.py",),
        axes={
            "default_polarity": ("opt-in", "opt-out"),
            "value": (
                "unset",
                "empty",
                "whitespace",
                "1/true/yes/on",
                "0/false/no/off",
                "mixed-case",
                "arbitrary-text",
            ),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Two ABICHECK_* booleans (ALLOW_AST_FALLBACK, "
                    "ALLOW_UNSUPPORTED_CASTXML) are ALSO reachable as Click "
                    "`envvar=` flags, where Click's own BOOL conversion -- "
                    "not this parser -- reads them, and it rejects an "
                    "unrecognized value as a usage error instead of falling "
                    "back to the default. The two agree on all ten tokens; "
                    "they diverge only on arbitrary text, and only on the "
                    "commands still registering those flags. Unifying means "
                    "a custom Click ParamType, which is a change to the "
                    "option layer rather than to the readers this class "
                    "covers."
                ),
                reference="docs/contribute/plans/one-comparison-product.md#phase-7l",
            ),
        ),
    ),
)
