# Copyright 2026 Nikolay Petrov
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

"""Split out of :mod:`abicheck.storage.snapshot_codec` (ADR-061 gap E) purely
to keep that module under the ADR-061 new-file production line ceiling --
a mechanical extraction, not a redesign. Every rule, comment, and Codex-review
citation below is unchanged from its original home in
``decode_snapshot``.

Computes the seven ``*_facts_reliable`` flags :class:`~abicheck.model.
snapshot.AbiSnapshot` carries -- each answers whether a fact this snapshot
persists is a real determination or a stale, real-but-WRONG scalar left
over from before some backend started actually populating it (see
``storage.snapshot_schema_versions``'s own v19-v25 history entries for the
full "real but WRONG" reasoning). ``decode_reliability_flags`` returns a
plain ``dict[str, bool]`` keyed by the exact ``AbiSnapshot`` constructor
keyword each flag fills, so ``storage.snapshot_codec.decode_snapshot`` can
spread it straight into the ``AbiSnapshot(...)`` call with ``**``.
"""

from __future__ import annotations

from typing import Any

from .fact_schema_versions import _MIN_SCHEMA_VERSION_FOR_PARAM_KIND_FACT
from .snapshot_schema_versions import (
    _MIN_SCHEMA_VERSION_FOR_CASTXML_VAR_ACCESS_FACTS,
    _MIN_SCHEMA_VERSION_FOR_CLANG_DEPRECATION_FACTS,
    _MIN_SCHEMA_VERSION_FOR_CLANG_FIELD_INITIALIZER_FACTS,
    _MIN_SCHEMA_VERSION_FOR_CLANG_RESTRICT_FACTS,
    _MIN_SCHEMA_VERSION_FOR_CLANG_VA_LIST_FACTS,
    _MIN_SCHEMA_VERSION_FOR_CLANG_VTABLE_FACTS,
    _MIN_SCHEMA_VERSION_FOR_CV_FACTS,
)


def decode_reliability_flags(
    d: dict[str, Any],
    *,
    from_headers: bool,
    ast_producer_value: str | None,
    schema_version: int,
) -> dict[str, bool]:
    if "header_cv_facts_reliable" in d:
        # Trust an explicit marker over re-deriving from schema_version: a
        # load -> snapshot_to_dict -> (save) -> load round-trip always
        # re-stamps schema_version to the CURRENT SCHEMA_VERSION (it
        # describes the writing tool's format capability, not the
        # snapshot's true field-fact origin), so re-deriving purely from
        # schema_version on a reserialized legacy snapshot would silently
        # flip an already-known-unreliable snapshot's stale, real-but-wrong
        # cv facts back to "reliable" — reintroducing the exact false
        # FIELD_BECAME_CONST/VOLATILE/TYPE_FIELD_TYPE_CHANGED positives this
        # flag exists to prevent (Codex review, PR #582).
        header_cv_facts_reliable_value = bool(d["header_cv_facts_reliable"])
    else:
        header_cv_facts_reliable_value = (
            not from_headers
            or ast_producer_value == "clang"
            or schema_version >= _MIN_SCHEMA_VERSION_FOR_CV_FACTS
        )

    if "clang_deprecation_facts_reliable" in d:
        # Same round-trip-stability reasoning as header_cv_facts_reliable
        # above: trust an explicit marker over re-deriving from
        # schema_version, since a load -> save -> load round-trip always
        # re-stamps schema_version to the CURRENT SCHEMA_VERSION.
        clang_deprecation_facts_reliable_value = bool(
            d["clang_deprecation_facts_reliable"]
        )
    else:
        # Only the clang/hybrid producer path is affected -- a castxml (or
        # a from-scratch, non-header) snapshot's own deprecated/is_scoped
        # values were always reliable (G28 Phase 1), regardless of
        # schema_version (v19 above).
        clang_deprecation_facts_reliable_value = (
            not from_headers
            or ast_producer_value != "clang"
            or schema_version >= _MIN_SCHEMA_VERSION_FOR_CLANG_DEPRECATION_FACTS
        )

    if "clang_field_initializer_facts_reliable" in d:
        # Same explicit-marker-wins reasoning as the two flags above.
        clang_field_initializer_facts_reliable_value = bool(
            d["clang_field_initializer_facts_reliable"]
        )
    else:
        # Unlike clang_deprecation_facts_reliable, this covers "hybrid" too,
        # not just "clang" (Codex review, fresh evidence, second round): a
        # pre-v20 hybrid merge's clang-only-appended fields never had
        # `default` provenance stamped at all (only `deprecated` was), so an
        # absent entry for one of those fields on a legacy hybrid snapshot is
        # real-but-WRONG data, same as a legacy pure-clang snapshot's
        # unconditional None -- see AbiSnapshot.clang_field_initializer_
        # facts_reliable's own docstring for the full reasoning, including
        # why a MATCHED field's own provenance is unaffected either way.
        #
        # `ast_producer_value == "castxml"` (Codex review, fresh evidence,
        # third round), not `not in ("clang", "hybrid")`: a snapshot
        # persisted before `ast_producer` was tracked at all (e.g. schema
        # v9) has `ast_producer_value is None`, which `not in (...)` treated
        # as "definitely not clang/hybrid" -- i.e. reliable -- when it is
        # exactly the reverse. `ast_producer` has always had exactly three
        # real producers (`"clang"`/`"castxml"`/`"hybrid"`, verified against
        # every write site), so a `None` here means "unknown," not
        # "castxml." This function's own consumer,
        # `default_value_representation_unreliable`
        # (diff_default_value_reliability.py), already treats a per-
        # declaration producer of `None` as clang-family risk for the exact
        # same reason -- only `"castxml"` is excluded there too. Reproduced
        # empirically: loading a from_headers=True, schema-v9 dict with no
        # `ast_producer` key yielded `clang_field_initializer_facts_reliable
        # =True` before this fix, silently trusting a legacy direct-clang
        # snapshot's pre-stabilization `"expr:"` fingerprint and reporting a
        # false PARAM_DEFAULT_VALUE_CHANGED/FIELD_DEFAULT_INITIALIZER_CHANGED
        # against an unchanged default after upgrading. Safe for a genuine
        # legacy castxml snapshot too: castxml never produces an `"expr:"`-
        # prefixed value (it keeps the verbatim source expression instead),
        # so the reliability flag is never even consulted for one -- both
        # gate functions above check the VALUE's own `"expr:"` prefix first.
        clang_field_initializer_facts_reliable_value = (
            not from_headers
            or ast_producer_value == "castxml"
            or schema_version >= _MIN_SCHEMA_VERSION_FOR_CLANG_FIELD_INITIALIZER_FACTS
        )

    if "clang_vtable_facts_reliable" in d:
        # Same explicit-marker-wins reasoning as the flags above.
        clang_vtable_facts_reliable_value = bool(d["clang_vtable_facts_reliable"])
    else:
        # Only the direct-clang ("clang") producer path is affected, same as
        # clang_deprecation_facts_reliable above -- not "hybrid" too: the
        # vtable/vptr reconstruction lives entirely in dumper_clang_vtable.py,
        # a direct-clang-backend-only module never invoked by the hybrid
        # merge path, so a legacy hybrid snapshot's own vtable facts came
        # from castxml (dumper_hybrid.py's "prefer castxml" merge policy)
        # and carry no equivalent false-reliability risk. A castxml (or
        # non-header) snapshot's own vtable extraction predates this field
        # entirely, so it's always reliable regardless of schema version.
        clang_vtable_facts_reliable_value = (
            not from_headers
            or ast_producer_value != "clang"
            or schema_version >= _MIN_SCHEMA_VERSION_FOR_CLANG_VTABLE_FACTS
        )

    if "clang_restrict_facts_reliable" in d:
        # Same explicit-marker-wins reasoning as the flags above.
        clang_restrict_facts_reliable_value = bool(d["clang_restrict_facts_reliable"])
    else:
        # Covers "hybrid" as well as "clang" -- and, like
        # clang_field_initializer_facts_reliable above, spells that as
        # `== "castxml"` rather than `not in ("clang", "hybrid")` so a
        # snapshot persisted before `ast_producer` was tracked at all (its
        # value here is None, i.e. UNKNOWN, not "castxml") is treated as
        # possibly clang-family rather than silently trusted. A hybrid
        # merge keeps castxml's `params` verbatim for every matched
        # function, so only its clang-ONLY appended functions carry clang's
        # blanket-False parameters -- but that is enough to need the flag,
        # exactly as the pre-v20 hybrid clang-only-append case did for
        # field initializers. See AbiSnapshot.clang_restrict_facts_reliable.
        clang_restrict_facts_reliable_value = (
            not from_headers
            or ast_producer_value == "castxml"
            or schema_version >= _MIN_SCHEMA_VERSION_FOR_CLANG_RESTRICT_FACTS
        )

    if "clang_va_list_facts_reliable" in d:
        # Same explicit-marker-wins reasoning as the flags above.
        clang_va_list_facts_reliable_value = bool(d["clang_va_list_facts_reliable"])
    else:
        # Unlike clang_restrict_facts_reliable, this does NOT special-case
        # "hybrid" as trusted — `diff_symbols._diff_param_va_list` excludes
        # "hybrid" from its producer gate entirely (Codex review; see
        # AbiSnapshot.clang_va_list_facts_reliable's own docstring for why),
        # so this flag's value is consulted only for a "clang" snapshot. The
        # `== "castxml"` spelling still matters for treating an untracked
        # pre-`ast_producer` snapshot (None here) as possibly clang-family
        # rather than silently trusted.
        clang_va_list_facts_reliable_value = (
            not from_headers
            or ast_producer_value == "castxml"
            or schema_version >= _MIN_SCHEMA_VERSION_FOR_CLANG_VA_LIST_FACTS
        )

    if "castxml_var_access_facts_reliable" in d:
        # Same explicit-marker-wins reasoning as the flags above.
        castxml_var_access_facts_reliable_value = bool(
            d["castxml_var_access_facts_reliable"]
        )
    else:
        # Inverted producer spelling from the clang-side flags above: this
        # fact is castxml-only, so it's `== "clang"` (rather than
        # `== "castxml"`) that means "not this producer, therefore
        # trusted-by-irrelevance" — an untracked pre-`ast_producer` snapshot
        # (None here) is treated as possibly castxml rather than silently
        # trusted, same principle as the others. See
        # AbiSnapshot.castxml_var_access_facts_reliable's own docstring for
        # why "hybrid" is NOT treated as trusted-by-irrelevance either.
        castxml_var_access_facts_reliable_value = (
            not from_headers
            or ast_producer_value == "clang"
            or schema_version >= _MIN_SCHEMA_VERSION_FOR_CASTXML_VAR_ACCESS_FACTS
        )

    if "param_kind_facts_reliable" in d:
        # Same explicit-marker-wins reasoning as the flags above.
        param_kind_facts_reliable_value = bool(d["param_kind_facts_reliable"])
    else:
        # Both header-AST backends are affected equally (unlike the
        # producer-specific flags above) -- neither ever determined a
        # parameter's indirection kind before schema v45, so there is no
        # `ast_producer_value == "..."` disjunct narrowing this to one
        # producer family. A non-header (or already-v45+) document is
        # unaffected either way.
        param_kind_facts_reliable_value = (
            not from_headers
            or schema_version >= _MIN_SCHEMA_VERSION_FOR_PARAM_KIND_FACT
        )

    return {
        "header_cv_facts_reliable": header_cv_facts_reliable_value,
        "clang_deprecation_facts_reliable": clang_deprecation_facts_reliable_value,
        "clang_field_initializer_facts_reliable": (
            clang_field_initializer_facts_reliable_value
        ),
        "clang_vtable_facts_reliable": clang_vtable_facts_reliable_value,
        "clang_restrict_facts_reliable": clang_restrict_facts_reliable_value,
        "clang_va_list_facts_reliable": clang_va_list_facts_reliable_value,
        "castxml_var_access_facts_reliable": castxml_var_access_facts_reliable_value,
        "param_kind_facts_reliable": param_kind_facts_reliable_value,
    }
