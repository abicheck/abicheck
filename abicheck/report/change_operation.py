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

"""Is a finding an addition, a removal, or a modification?

One classifier, shared by ``ShowOnlyFilter``'s ``added``/``removed``/
``changed`` action tokens and the JSON report's structured per-finding
``operation`` field (schema 2.3), so the two cannot drift apart.

Mostly a suffix rule over the ``ChangeKind`` value, with an explicit
override table for the names it cannot read. The one structural subtlety is
that a kind's name carries *two* independent things -- what was observed and
what evidence saw it -- and only the first is an operation. See
:data:`_EVIDENCE_TIER_SUFFIXES`.

Split out of ``reporter_markdown.py``, which carries a ``no_growth``
baseline in ``architecture/debt.yaml``; classifying a kind is not that
module's rendering job in any case.
"""

from __future__ import annotations

# Kind-name suffixes that identify an additive vs. a removal finding — shared
# between ShowOnlyFilter's "added"/"removed"/"changed" action tokens and the
# JSON report's structured per-finding "operation" field (schema 2.3), so the
# two never drift apart.
_ADDED_SUFFIXES = ("_added", "_added_compatible")
_REMOVED_SUFFIXES = (
    "_removed",
    "_deleted",
    "_elf_fallback",
    "_const_overload",
)

# Suffixes naming the *evidence tier* a finding rests on, not its operation.
# Stripped before the operation suffixes are applied, so the operation word
# that precedes them decides.
#
# ``_elf_only`` used to sit in ``_REMOVED_SUFFIXES`` instead, which was
# correct only while ``func_removed_elf_only`` was the family's sole member:
# adding ``func_added_elf_only`` made the same suffix mean both operations at
# once, and the heuristic reported a new export as a removal. Stripping fixes
# the class rather than the instance -- any future ``*_added_elf_only`` /
# ``*_removed_elf_only`` pair resolves correctly with no new entry here.
_EVIDENCE_TIER_SUFFIXES = ("_elf_only",)

# Kinds whose name doesn't end in one of the suffixes above but still name a concrete symbol/entity appearing or disappearing (Codex review on #557: operation_for_kind() reported these as "modified"). Checked before the suffix rule. Deliberately does NOT include kinds naming a *property* gained/lost on an entity that still exists — e.g. the "*_lost_*" family (`field_lost_const`, `func_lost_inline`, ...) or the "*_introduced" family (`vptr_introduced`, `static_tls_introduced`, ...): those are trait changes on a persisting entity, which is what "modified" means here, not an addition/removal of the entity itself.
_OPERATION_OVERRIDES: dict[str, str] = {
    # Ends in "_added_compat", not "_added"/"_added_compatible".
    "symbol_version_required_added_compat": "added",
    # Ends in "_removed_without_replacement", not "_removed".
    "experimental_removed_without_replacement": "removed",
    # Ends in "_deleted_dwarf", not "_deleted".
    "func_deleted_dwarf": "removed",
    # ADR-065 S3: a component leaves (or joins) the release's declared
    # component set. Ends in "_retired"/"_introduced", neither of which is a
    # suffix the heuristic below knows -- and both are genuinely an added/
    # removed *entity*, not a property change on a persisting one.
    "support_promise_component_retired": "removed",
    "support_promise_component_introduced": "added",
    # A whole ISA-dispatch family's concrete symbols vanish (case83), not a
    # property change on a persisting symbol.
    "cpu_dispatch_isa_dropped": "removed",
    # A stable name is added alongside the still-present experimental alias
    # (case99) -- without the dedicated detector this would just be a plain
    # func_added; ADDITION_KINDS already classifies it as an addition
    # (Codex review on #557).
    "experimental_graduated": "added",
    # These four end in "_added" but each names a trait *gained by an
    # existing, persisting function* ("Function became virtual: {name}",
    # "noexcept specifier added: {name}", "Function became variadic (gained
    # ...): {name}" -- verified against their diff_symbols.py descriptions
    # and change_registry.py entries, none of which set is_addition=True /
    # belong to ADDITION_KINDS) -- the same "*_lost_*"/"*_introduced" trait-
    # change pattern above, just spelled with "_added" (Codex review, PR
    # #557). `func_pure_virtual_added` ("Function became pure virtual:
    # {name}") is the identical pattern applied to its sibling kind
    # `func_virtual_became_pure`, which already classifies correctly as
    # "modified" since it doesn't end in "_added".
    "func_noexcept_added": "modified",
    "func_virtual_added": "modified",
    "func_variadic_added": "modified",
    "func_pure_virtual_added": "modified",
    # A field inserted into an existing struct/class shifts every
    # subsequent field's offset -- this modifies the *layout of the
    # existing type*, not merely a new field appearing in isolation.
    # `type_field_added_compatible` (append-at-end, no offset shift) is the
    # dedicated addition-kind carve-out and is unaffected by this override
    # (it doesn't end in plain "_added"). (Codex review, PR #557.)
    "type_field_added": "modified",
    # The identical layout-modification pattern applied to virtual methods
    # instead of fields: a new virtual method on an already-existing class
    # grows/relayouts the vtable (gains a hidden vtable pointer if it had
    # none, or a new slot otherwise), breaking derived classes compiled
    # against the old layout -- KDE's "do not add virtuals to a non-leaf
    # class" rule. Not in ADDITION_KINDS (Codex review, PR #557).
    "virtual_method_added": "modified",
    # More of the same trait-gained-by-a-persisting-entity pattern, found on
    # a second audit pass (Codex review, PR #557): a constructor/conversion
    # operator gaining `explicit` (`ctor_explicit_added`), a template
    # parameter that was defaulted/deduced becoming mandatory
    # (`mandatory_template_param_added`), a Python-visible function gaining
    # a new *required* parameter (`python_api_parameter_added`), and a
    # function gaining a semantic contract attribute like nonnull/noreturn
    # (`func_contract_attribute_added`) all describe an already-existing
    # callable/template's signature or contract changing, not a new one
    # appearing. None of these four is in ADDITION_KINDS either.
    "ctor_explicit_added": "modified",
    "mandatory_template_param_added": "modified",
    "python_api_parameter_added": "modified",
    "func_contract_attribute_added": "modified",
    # Removed-side counterparts of the trait-change pattern: these end in
    # plain "_removed" (so the suffix rule alone reports "removed"), but
    # each names a trait *lost by* an entity that still exists — mirroring
    # `func_noexcept_added`/`func_variadic_added`/etc. above, just the
    # opposite direction of the same specifier gain/loss (Codex review, PR
    # #557).
    "func_noexcept_removed": "modified",
    "func_variadic_removed": "modified",
    "func_contract_attribute_removed": "modified",
    "ctor_explicit_removed": "modified",
    # A third audit pass turned up more of the same (Codex review, PR #557):
    # `func_virtual_removed` ("Vtable entry removed" -- the sibling of
    # `func_virtual_added` above, an existing function losing its
    # virtual-ness) and `param_default_value_removed`/
    # `python_api_default_removed` (an existing parameter of an existing
    # function/method losing its default value, making a previously
    # optional argument mandatory) all describe a trait lost by a
    # persisting entity, not the entity itself disappearing.
    "func_virtual_removed": "modified",
    "param_default_value_removed": "modified",
    "python_api_default_removed": "modified",
}


def operation_for_kind(kind_val: str) -> str:
    """Classify a ``ChangeKind.value`` string into "added"/"removed"/"modified".

    A kind is "added"/"removed" when it is listed in ``_OPERATION_OVERRIDES``
    or its name -- with any ``_EVIDENCE_TIER_SUFFIXES`` stripped first, since
    those name where a finding's evidence came from and not what it observed
    -- ends with one of the corresponding suffixes above; every
    other kind (parameter/type/layout changes, renames, trait gained/lost on
    a persisting entity, etc.) is "modified".
    """
    override = _OPERATION_OVERRIDES.get(kind_val)
    if override is not None:
        return override
    stem = kind_val
    for suffix in _EVIDENCE_TIER_SUFFIXES:
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    if any(stem.endswith(s) for s in _ADDED_SUFFIXES):
        return "added"
    if any(stem.endswith(s) for s in _REMOVED_SUFFIXES):
        return "removed"
    return "modified"
