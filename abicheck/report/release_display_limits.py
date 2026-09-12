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

"""How many findings a release *summary* shows per library, and where.

A one-value leaf, extracted so the modules that need it -- the resolver
(``cli_compare_release_matrix``) and the Markdown renderer that applies the
cap (``cli_compare_receipt._release_md_library_findings``, reached from
``cli_compare_release_helpers``) -- can both depend on it without depending
on each other. A function-local import between those two was a real new
import cycle the ``import-cycle-growth`` gate rejects, and ``AGENTS.md``'s
own rule for that shape is to move the shared thing to a leaf, not to
extend the cycle allowlist.

This is a **presentation** limit and nothing else. It bounds what a human
summary renders; it never bounds the machine document, which carries every
finding.

Plan slice 7m retired the two ways that used to be *decided*
(``compare --max-findings-per-library`` and an
``ABICHECK_MAX_RELEASE_FINDINGS_PER_LIBRARY`` environment variable), leaving
a single automatic rule. The flag's own help text already told the reader
that raising it changes nothing but how much of one document is itemized,
and pointed at the retired ``--output-dir`` for the complete artifact -- so
the answer no longer depends on a decision:

* a **machine** export carries every finding, always. Nothing asks for
  truncation, so nothing is truncated (which is what the old
  ``release_findings_cap_is_explicit`` gate already achieved by default);
* a **human** summary is bounded at :data:`MAX_RELEASE_FINDINGS_PER_LIBRARY`,
  with the same ``findings_truncated_kinds`` disclosure of what was cut;
* the uncapped **per-library** reports are one export away
  (``-o json=reports/``), which is where the flag's help text already sent
  anyone who wanted them.

The environment variable retired with the flag rather than surviving it:
Phase 7l adopted, as a standing constraint on this whole phase, that a
demoted flag lands in ``.abicheck.yml`` and never in an undocumented
variable -- keeping the variable as the last remaining override would have
made this the "hidden CLI" that constraint exists to prevent.
"""

from __future__ import annotations

__all__ = ["MAX_RELEASE_FINDINGS_PER_LIBRARY"]

#: Findings rendered per library in a release *summary* before the render
#: notes that the rest were omitted. Matches the display cap the retired
#: ``scan --against`` summary used, and is no longer overridable: see this
#: module's docstring.
MAX_RELEASE_FINDINGS_PER_LIBRARY = 10
