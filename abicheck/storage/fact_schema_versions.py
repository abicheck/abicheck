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

"""Per-field ``Fact[T]`` schema-version thresholds (ADR-063 Phase 0/5).

The one place the "which ``schema_version`` did *this* field's own
``<field>_fact`` sibling start being persisted at" answer lives. A leaf
module with no first-party imports at all, because two modules need these
numbers and neither may import the other: ``fact_codec.py`` decodes with
them, and ``fact_backfill.py`` (the case-(a) legacy-load corrections) gates
on them. Keeping them in ``fact_codec`` and importing them back from
``fact_backfill`` was a real import cycle the ``import-cycle-growth``
AI-readiness gate rejects -- correctly: a shared constant belongs in a leaf
both sides depend on, not in whichever module happened to define it first.

Every threshold is documented at its own definition; see
``serialization.SCHEMA_VERSION``'s history comment for the full version log.
"""

from __future__ import annotations

# The schema_version this phase bumped SCHEMA_VERSION to when it started
# persisting a *_fact sibling for every legacy field it emits (serialization.py).
_FACT_FIELDS_SCHEMA_VERSION = 26

# ADR-063 Phase 5: the schema_version RecordType.is_final_fact started being
# persisted at — independent of _FACT_FIELDS_SCHEMA_VERSION above, since a
# document between the two thresholds (v26..v29) genuinely never carried
# this key at all, the same way a pre-v26 document never carried any *_fact
# key. See decode_fact's own docstring for why this threshold matters.
_MIN_SCHEMA_VERSION_FOR_IS_FINAL_FACT = 30

# ADR-063 Phase 5 (second batch): the schema_version RecordType's remaining
# case-(b) *_fact siblings (is_abstract/data_size_bits/is_standard_layout/
# is_trivially_copyable/qualified_name/source_header) started being
# persisted at — one shared threshold, since all six land together in the
# same schema bump. Same reasoning as _MIN_SCHEMA_VERSION_FOR_IS_FINAL_FACT
# above: a document below this version never carried these keys at all.
_MIN_SCHEMA_VERSION_FOR_RECORDTYPE_CASE_B_FACTS = 32

# ADR-063 Phase 5 (third batch): the schema_version EnumType's own
# qualified_name_fact/source_header_fact siblings started being persisted
# at.
_MIN_SCHEMA_VERSION_FOR_ENUMTYPE_FACTS = 33

# ADR-063 Phase 5 (fourth batch): the schema_version Variable's own
# source_header_fact/alignment_bits_fact/elf_binding_fact siblings started
# being persisted at.
_MIN_SCHEMA_VERSION_FOR_VARIABLE_CASE_B_FACTS = 34

# ADR-063 Phase 5 (fifth batch): the schema_version Function's own ten
# case-(b) *_fact siblings started being persisted at.
_MIN_SCHEMA_VERSION_FOR_FUNCTION_CASE_B_FACTS = 35

# ADR-063 Phase 5 (sixth batch): the schema_version AbiSnapshot's own
# ast_resolved_standard_fact sibling started being persisted at.
_MIN_SCHEMA_VERSION_FOR_SNAPSHOT_CASE_B_FACTS = 36

# ADR-063 Phase 5 (eighth batch): the schema_version TypeField's own
# case-(a) is_const_fact/is_volatile_fact/is_mutable_fact siblings started
# being persisted at.
_MIN_SCHEMA_VERSION_FOR_TYPEFIELD_CV_FACTS = 38

# The same threshold for TypeField's other two case-(a) fields (`default`,
# `deprecated`) -- they land in the same schema bump, but each is guarded by
# its own reliability flag (clang_field_initializer_facts_reliable /
# clang_deprecation_facts_reliable), so they are named separately here
# rather than folded into the CV constant above.
_MIN_SCHEMA_VERSION_FOR_TYPEFIELD_VALUE_FACTS = 38

# ADR-063 Phase 5 (ninth batch): the schema_version the `deprecated` family
# (Function/Variable/RecordType/EnumType -- TypeField's own landed one batch
# earlier, at v38) and EnumType.is_scoped started being persisted at. All
# five are case (a), guarded by the one flag that already covers them:
# AbiSnapshot.clang_deprecation_facts_reliable.
_MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS = 39

# ADR-063 Phase 5 (tenth batch): the schema_version Param.is_restrict_fact
# and Variable.access_fact started being persisted at -- the last two
# entries of this phase's own KNOWN_UNCONVERTED_ELIGIBLE_FACTS allowlist,
# guarded by clang_restrict_facts_reliable and
# castxml_var_access_facts_reliable respectively.
_MIN_SCHEMA_VERSION_FOR_LAST_CASE_A_FACTS = 40
