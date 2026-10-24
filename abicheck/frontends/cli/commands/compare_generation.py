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

"""``compare --require-same-generation`` (ADR-062 D2, plan H1 step 4).

The flag is consumed here, not threaded through ``run_compare``'s parameter
list: it is stored on the click context's ``meta`` and read where the
single-pair ``CompareRequest`` is built, whose
``require_same_generation`` field makes ``classify_resolved_pair`` refuse a
cross-generation pair with a ``ValidationError`` (a usage error, exit 64).
A directory/package operand is refused up front: its per-library fan-out
does not build that request.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

__all__ = ["REQUIRE_SAME_GENERATION_META", "enter_require_same_generation"]

#: ``click.Context.meta`` key carrying the flag to the request builder.
REQUIRE_SAME_GENERATION_META = "abicheck.compare.require_same_generation"


def enter_require_same_generation(ctx: click.Context, kwargs: dict[str, Any]) -> None:
    """Pop the flag from *kwargs*, refuse it for set operands, record it."""
    if not kwargs.pop("require_same_generation", False):
        return
    from ....cli_resolve import classify_compare_operand

    for operand in (kwargs.get("old_input"), kwargs.get("new_input")):
        if operand is not None and classify_compare_operand(Path(operand)) in {
            "directory",
            "package",
        }:
            raise click.UsageError(
                "--require-same-generation is not supported for "
                "directory/package (release) comparisons: compare the "
                "specific library's snapshots individually to use it."
            )
    ctx.meta[REQUIRE_SAME_GENERATION_META] = True
