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

"""``compare``'s bundle-facts operand-classification dispatch decision (CLI
cleanup phase two, PR I).

A small, standalone leaf module for one call -- the same reason
``compare_bundle_facts_rejections.py``/``options/bundle_facts.py`` already
live outside ``compare.py`` itself: that module sits at the architecture
800-line production cap with no headroom for another inline block.
:func:`resolve_bundle_compare_dispatch` is ``compare_cmd``'s entire bundle-
facts routing decision -- classify both operands
(``workflows.bundle_compare_operand``, which is deliberately ``click``-free)
and translate the one unsupported combination (a stored NEW_INPUT) into a
``click.UsageError`` here, at the CLI boundary.
"""

from __future__ import annotations

from pathlib import Path

import click


def resolve_bundle_compare_dispatch(old_input: Path, new_input: Path) -> bool:
    """Classify *old_input*/*new_input* for ``compare``'s bundle-facts
    routing. Returns ``True`` when OLD_INPUT is a stored BundleFacts
    document (the caller should dispatch to ``compare_bundle_facts.
    dispatch()``); raises ``click.UsageError`` when NEW_INPUT is one
    instead (live/stored and stored/stored are not yet implemented -- see
    ``workflows/bundle_compare_operand.py``'s own docstring)."""
    from ....workflows.bundle_compare_operand import classify_bundle_compare_operands

    operands = classify_bundle_compare_operands(old_input, new_input)
    if operands.new_is_stored:
        raise click.UsageError(
            f"'{new_input}' looks like a stored BundleFacts document (from "
            "a prior --bundle-facts-out) -- comparing against a stored "
            "NEW_INPUT is not yet supported; NEW_INPUT must be a live "
            "library/directory/package. If you meant to compare two stored "
            "bundle-facts documents, or swap old/new, neither combination "
            "is implemented yet."
        )
    return operands.old_is_stored
