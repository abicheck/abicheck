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

"""JUnit ``<testcase>`` ``<properties>`` writers (ADR-061 ``report`` layer).

``junit_report.py`` is a pre-ADR-061 flat module at its own debt baseline, so
the property writers live here rather than growing it: each answers "what
extra facts does this testcase carry", which is report-projection work, and
they share one rule that must not drift -- a testcase carries **at most one**
``<properties>`` block, because every consumer (this repo's own tests
included) reads it through a single ``tc.find("properties")`` and never sees
a second.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..checker_types import Change


def testcase_properties(tc: ET.Element) -> ET.Element:
    """The testcase's one ``<properties>`` block, created if absent.

    Extracted from :func:`~abicheck.junit_report._add_correlation_property` so the two property
    writers cannot drift on the two rules that block encodes: a testcase
    carries at most one ``<properties>`` element (every consumer reads it
    via a single ``tc.find("properties")``), and a freshly created one is
    *inserted first*, because the JUnit XSD requires it to precede any
    ``<failure>``/``<error>``/``<skipped>`` a primary change may already
    have added.
    """
    props = tc.find("properties")
    if props is None:
        props = ET.Element("properties")
        tc.insert(0, props)
    return props


def add_demangled_symbol_property(tc: ET.Element, change: Change) -> None:
    """Append ``abicheck.demangled_symbol`` when *change*'s symbol demangles.

    Codex review, PR #1284: plan slice 7o resolves demangling per format --
    human formats demangle their text, machine formats keep the raw mangled
    symbol and carry the readable one in a field beside it. JUnit had no
    such field, so a JUnit-only consumer was left with the mangled name and
    (the retired ``--view demangle`` gone) no way to ask for the other. The
    testcase ``name`` stays the exact mangled symbol -- it is a stable test
    identity consumers key on across runs -- and the readable name travels
    as a property, which is JUnit's own extension point for exactly this.
    """
    from ..reporter import resolve_demangled_symbol

    demangled = resolve_demangled_symbol(change)
    if not demangled:
        return
    prop = ET.SubElement(testcase_properties(tc), "property")
    prop.set("name", "abicheck.demangled_symbol")
    prop.set("value", demangled)
