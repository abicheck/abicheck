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

"""Cross-format ``--show-only`` correlation cleanup.

Leaf module (imports only from ``checker``) split out of
``reporter_markdown.py`` to keep that file under the AI-readiness file-size
hard cap -- ``_suppress_dangling_correlation_notes`` is called by every
report format (markdown, JSON, SARIF, HTML, JUnit), not just markdown, so it
belongs in its own shared home rather than growing the markdown renderer.
"""

from __future__ import annotations

import copy
import dataclasses
from collections.abc import Sequence

from .checker import Change


def _suppress_dangling_correlation_notes(changes: Sequence[Change]) -> list[Change]:
    """Rendering-local view of *changes* with ``Change.correlated_change_kind``
    cleared on any change whose named target is not itself present in
    *changes*.

    ``--show-only`` can keep a ``LAYOUT_UNVERIFIABLE`` finding while
    filtering out the co-reported ``TYPE_VTABLE_CHANGED`` its
    ``correlated_change_kind`` names -- rendering "See also:
    type_vtable_changed finding for the same symbol" with nothing in the
    *visible* report to actually see (Codex review, fresh evidence). Same
    shape of gap for the older ADR-041 pairing
    (``PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`` <-> an
    ``inline_body_changed``/``template_body_changed``/
    ``public_typedef_target_changed`` on the same public entry), which
    correlates by ``symbol`` instead (its producer,
    ``buildsource.source_graph_findings``, never sets ``qualified_name``).
    A source with ``qualified_name`` set is matched *only* by it, never
    falling back to ``symbol`` too -- an ``or`` across both would accept an
    unrelated same-leaf-name record's own surviving finding as "the same
    target" (e.g. an unrelated ``ns2::Foo``'s own ``TYPE_VTABLE_CHANGED``
    making ``ns1::Foo``'s dangling reference to its own, filtered-out one
    read as still present -- Codex review, fresh evidence).

    Called by every format that independently applies ``--show-only`` to its
    own filtered ``changes`` list before rendering ``correlated_change_kind``
    -- markdown's three views (default/leaf/root-cause), JSON/root-cause-JSON
    (``reporter.py``), SARIF (``sarif.py``), the HTML report
    (``html_report.py``), and JUnit XML (``junit_report.py``). A gap first
    fixed only in markdown left the other four formats independently able to
    render the same dangling reference (Codex review, fresh evidence: caught
    one commit after the markdown-only fix landed).

    Never mutates the original ``Change`` objects (shared across every
    format, each of which applies its own filter, if any, independently);
    only a shallow copy handed to the calling renderer is touched. That copy
    shares the *same* ``impact_assessment`` object as the original -- when a
    reachability-aware suppression made ``MarkReachability`` cache one
    (``impact.engine.assess_change()`` prefers a cached assessment's own
    ``correlated_change_kind`` over the flat field once one exists), clearing
    only the flat field would leave the copy's nested JSON/SARIF
    ``impact_assessment`` block still publishing the stale reference (Codex
    review, fresh evidence). ``ImpactAssessment`` is frozen, so this replaces
    it with a corrected copy on the *copy* only -- never mutating the cached
    object in place, which would also corrupt the original's cache. Never
    changes a finding's presence, kind, or severity -- purely a display-time
    fix for what would otherwise be a dangling cross-reference.
    """
    present_by_qualified_name = {
        (c.qualified_name, c.kind.value) for c in changes if c.qualified_name
    }
    present_by_symbol = {(c.symbol, c.kind.value) for c in changes}

    result: list[Change] = []
    for c in changes:
        correlated = getattr(c, "correlated_change_kind", None)
        if c.qualified_name:
            # A source with a qualified identity (the vtable/layout pairing)
            # must be matched *only* by it -- falling back to the bare
            # symbol here would accept an unrelated same-leaf-name record's
            # own surviving finding as "the same target" (e.g. ns2::Foo's
            # own TYPE_VTABLE_CHANGED making ns1::Foo's dangling reference
            # to its own, filtered-out one read as still present -- Codex
            # review, fresh evidence).
            visible = (c.qualified_name, correlated) in present_by_qualified_name
        else:
            # No qualified identity available (the ADR-041 pairing, whose
            # producer -- buildsource.source_graph_findings -- never sets
            # qualified_name) -- symbol is the only identity this source
            # correlates by, so it's the only one to check.
            visible = (c.symbol, correlated) in present_by_symbol
        if correlated and not visible:
            c2 = copy.copy(c)
            c2.correlated_change_kind = None
            if c2.impact_assessment is not None:
                c2.impact_assessment = dataclasses.replace(
                    c2.impact_assessment, correlated_change_kind=None
                )
            result.append(c2)
        else:
            result.append(c)
    return result
