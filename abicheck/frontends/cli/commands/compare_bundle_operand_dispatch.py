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
and translate the one remaining unsupported combination (a stored NEW_INPUT
paired with a *live* OLD_INPUT -- "live/stored") into a ``click.UsageError``
here, at the CLI boundary.

**Three of the four operand shapes now have a real execution engine**
(``bundle_side_input.compare_release_against_bundle_facts`` for stored/live,
``workflows.bundle_stored_pair_compare.compare_stored_bundle_facts_pair`` for
stored/stored, plain ``run_compare``/the live release fan-out for live/live -- see
``compare_bundle_facts.py``'s own ``dispatch()`` for how the two stored-
OLD_INPUT drivers are selected). **Live/stored (a live OLD_INPUT compared
against a stored NEW_INPUT) is the one shape still rejected outright** --
see ``bundle_side_input.py``'s own module docstring for exactly what
implementing it would take (the mirror image of the stored/live driver, with
every OLD-side extraction option -- ``--header old=``/``--include old=``/
``--ast-frontend old=``/``--devel-pkg old=``/
``--bundle-facts-library-manifest`` -- newly meaningful and every NEW-side
one of those newly rejected, which ``compare_bundle_facts_rejections.py``
does not yet parameterize for).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from ....workflows.bundle_compare_operand import BundleCompareRequest


def resolve_bundle_compare_dispatch(old_input: Path, new_input: Path) -> BundleCompareRequest:
    """Classify *old_input*/*new_input* for ``compare``'s bundle-facts
    routing. Returns the
    :class:`~abicheck.workflows.bundle_compare_operand.BundleCompareRequest`
    classification (the caller dispatches to ``compare_bundle_facts.
    dispatch()`` whenever ``.any_stored`` is true, forwarding ``.new_is_
    stored`` so that module can select the stored/live vs. stored/stored
    driver); raises ``click.UsageError`` for the one remaining unimplemented
    shape -- a stored NEW_INPUT paired with a *live* OLD_INPUT (see this
    module's own docstring)."""
    from ....workflows.bundle_compare_operand import classify_bundle_compare_operands

    operands = classify_bundle_compare_operands(old_input, new_input)
    if operands.new_is_stored and not operands.old_is_stored:
        raise click.UsageError(
            f"'{new_input}' looks like a stored BundleFacts document (from "
            "a prior --bundle-facts-out) and "
            f"'{old_input}' does not -- comparing a live OLD_INPUT against "
            "a stored NEW_INPUT is not yet supported. Either swap old/new "
            "(if you meant to compare the stored document against a live "
            "baseline), or capture OLD_INPUT with --bundle-facts-out first "
            "so both sides are stored."
        )
    return operands
