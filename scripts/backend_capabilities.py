#!/usr/bin/env python3
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

"""backend_capabilities.py — the L2 header-backend x ABI-fact capability matrix.

G31 Phase D asks for "a backend capability matrix (CastXML vs. direct-clang
vs. hybrid, which facts/edges each can and cannot see)". This module is that
matrix's source of truth: a hand-curated, pure-stdlib data structure (the same
pattern as ``platform_capabilities.py``/``evidence_tiers.py``), rendered into
``docs/reference/header-backend-capabilities.md`` by
``scripts/gen_backend_capability_matrix.py``.

**Why the data lives here and not in prose.** A hand-typed table of "which
backend populates which field" is exactly the kind of documentation that goes
stale silently — the G31 plan records several rounds where a fact's real
backend coverage had moved on while a doc (or the plan itself) still described
the old world. So this module carries two halves that a test cross-checks
against each other:

1. :data:`FACT_ROWS` — the curated claim, one row per field of the six
   declaration dataclasses, with the reasoning a table cell can't hold.
2. :func:`scan_backend_evidence` — the *observed* answer, read straight out of
   ``dumper_castxml.py``/``dumper_clang.py`` (plus any entity-parsing modules
   ADR-061 D9 has since split out of either facade under
   ``extract/headers/{castxml,clang}/``) with :mod:`ast`: which keyword each
   backend actually passes when it constructs one of those dataclasses, and
   whether the value is a real expression or a hardcoded literal (a
   ``size_bits=None`` or ``is_opaque=False`` is *not* extraction).

``tests/test_backend_capability_matrix.py`` asserts the two agree, and that
every model field is covered exactly once — so a backend that starts (or stops)
populating a fact fails a test rather than quietly contradicting the published
matrix.

The hybrid column is **derived, never hand-typed** — see
:func:`hybrid_capability`.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
PKG_DIR = REPO_DIR / "abicheck"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backend_capability_model import (  # noqa: E402  (re-exported)
    _COMPANION as _COMPANION,
    _DYNSYM as _DYNSYM,
    _FULL as _FULL,
    _NONE as _NONE,
    _OTHER as _OTHER,
    _PARTIAL as _PARTIAL,
    _PROVENANCE_PASS as _PROVENANCE_PASS,
    _RANK as _RANK,
    Capability as Capability,
    FactRow as FactRow,
    hybrid_capability as hybrid_capability,
)
from backend_capability_rows import (  # noqa: E402  (re-exported)
    FACT_ROWS as FACT_ROWS,
)
from backend_capability_scan import (  # noqa: E402  (re-exported; see that module)
    COVERED_MODEL_CLASSES as COVERED_MODEL_CLASSES,
    Evidence as Evidence,
    castxml_evidence as castxml_evidence,
    clang_evidence as clang_evidence,
    scan_backend_evidence as scan_backend_evidence,
)


def rows_for(owner: str) -> tuple[FactRow, ...]:
    """Every row describing one declaration dataclass, in declaration order."""
    return tuple(r for r in FACT_ROWS if r.owner == owner)
