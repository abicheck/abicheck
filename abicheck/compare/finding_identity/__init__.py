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

"""Canonical identity for flat (L0-L2) ABI findings (ADR-049 Phase 2).

Preference order (most to least specific):

1. **canonical** -- a verified mangled symbol name (Itanium ``_Z...`` or
   MSVC ``?...``), the same signal ``diff_symbols.py`` already keys its
   old/new function and variable maps by. Never a bare name that merely
   rode in the mangled field (``extern "C"`` producers report
   ``mangled == name`` deliberately).
2. **normalized** -- a normalized qualified-name + kind + parameter-type
   signature, for declarations with no real mangling (C-linkage symbols,
   or a flat finding whose only name-like field is a type name rather than
   a mangled symbol).
3. **reduced** -- a source-relative identity (file + name) alias, promoted
   to a low-confidence synthetic ``sha256`` primary id only when nothing
   more specific is available at all.

This module generalizes, rather than replaces, the several hand-rolled
matching fallbacks already scattered through ``diff_symbols.py`` (the
mangled-primary + name-based extern-C fallback in ``_diff_functions``,
``_synthetic_ctor_scope``'s key parsing) into one reusable, documented,
independently-tested primitive -- following the same "most specific
available identity, ambiguity-safe fallback" principle ADR-045 already
established for flat type matching (``diff_helpers.TypeMap``) and ADR-048
established for L5 source-graph nodes (``buildsource/entity_identity.py``).
This is the L0-L2 flat-finding analogue of those two, not a third
independent implementation of the
underlying principle -- and deliberately does not import
``buildsource.entity_identity`` (the optional L3-L5 layer must depend on
this core package, never the other way around).

**Wired.** Every flat old/new join now goes through this module:

- ``diff_filtering.py``'s ``_deduplicate_cross_detector`` uses
  :func:`resolve_change_identity` as its cross-detector dedup key,
  replacing a hand-rolled ``(change_category, symbol)`` tuple.
- ``diff_symbols.py``'s function and variable *matching*
  (``_diff_functions``/``_match_old_function``/``_diff_variables``) joins
  through :class:`SymbolIdentityIndex`, replacing a second hand-rolled name
  multimap plus an inline ``len(candidates) == 1`` count.

That second wiring waited for the piece this module was missing rather than
for the refactor to get smaller: :func:`resolve_function_identity` resolves
*one* declaration's identity and has no notion of "this identity is
ambiguous within its own snapshot, so do not join on it" -- the whole of
what made the ``extern "C"`` fallback safe. :class:`SymbolIdentityIndex`
supplies it, so the matching engine states the rule instead of
re-implementing it. Matching behavior is deliberately unchanged -- the
hand-tuned join logic (elf-only-mode, unconfirmed-parameter/LLP64
threading, virtual-method-addition, inline transitions, hidden friends) is
untouched, pinned by the golden/FP-rate/tier-accuracy/detector-oracle suites.

NEVER invents a fact a producer did not supply: a tier is only claimed
when the corresponding input field is actually present.
"""

from .change import resolve_change_identity
from .mangled import is_real_mangled_name, normalize_mangled_name
from .report_ids import (
    MissingContractFinding,
    missing_contract_finding,
    missing_contract_kind,
    report_canonical_finding_id,
    report_finding_id,
)
from .symbols import (
    IDENTITY_TIER_CANONICAL,
    IDENTITY_TIER_NORMALIZED,
    IDENTITY_TIER_REDUCED,
    FindingIdentity,
    IdentityMatch,
    SymbolIdentityIndex,
    normalized_signature,
    resolve_function_identity,
    resolve_symbol_identity,
    resolve_variable_identity,
)

__all__ = [
    "IDENTITY_TIER_CANONICAL",
    "IDENTITY_TIER_NORMALIZED",
    "IDENTITY_TIER_REDUCED",
    "FindingIdentity",
    "IdentityMatch",
    "MissingContractFinding",
    "SymbolIdentityIndex",
    "is_real_mangled_name",
    "missing_contract_finding",
    "missing_contract_kind",
    "normalize_mangled_name",
    "normalized_signature",
    "report_canonical_finding_id",
    "report_finding_id",
    "resolve_change_identity",
    "resolve_function_identity",
    "resolve_symbol_identity",
    "resolve_variable_identity",
]
