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

"""``Fact[T]`` encode/decode/legacy-backfill helpers for ``serialization.py``.

Split out into a ``storage``-owned leaf module rather than inlined in
``serialization.py`` (ADR-063 Phase 0, schema v26): that module is already
at this repo's 2000-line AI-readiness hard cap, and this encode/decode
concern only depends on ``model`` — exactly what ADR-061's ``storage``
layer owns — the same way `snapshot_io.py` carries the storage-envelope
concern out of `serialization.py`'s neighbours.
"""

from __future__ import annotations

__all__ = [
    "decode_function_facts",
    "explicit_instantiation_value",
    "surface_facts_or_legacy_bridge",
]

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ..model import (
    ExplicitInstantiation,
    Fact,
    SymbolBinding,
)

# ADR-061's 800-line production ceiling: the case-(a) legacy-load
# corrections live in ``fact_backfill.py`` and are re-exported here, so
# ``serialization.py`` and every test importing them from this module are
# unaffected by that split. One-directional: ``fact_backfill`` imports the
# per-field schema-version thresholds from the ``fact_schema_versions.py``
# leaf, never from here, so this re-export forms no cycle.
from .fact_backfill import (
    CaseAFactRule as CaseAFactRule,
    apply_case_a_fact_backfill as apply_case_a_fact_backfill,
    apply_legacy_fact_backfill as apply_legacy_fact_backfill,
    evidenced_producers as evidenced_producers,
)
from .fact_decode import (
    abi_fundamental_from_json,
    decode_fact,
    decode_fact_with_legacy_presence,
)
from .fact_payload_validation import (
    validated_identities,
    validated_template_param_defaults,
)
from .fact_schema_versions import (
    _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS,
    _MIN_SCHEMA_VERSION_FOR_EXPLICIT_INSTANTIATION_FACT,
    _MIN_SCHEMA_VERSION_FOR_FUNCTION_CASE_B_FACTS,
    _MIN_SCHEMA_VERSION_FOR_FUNCTION_TEMPLATE_PARAM_DEFAULTS,
    _MIN_SCHEMA_VERSION_FOR_INLINE_FACT,
    _MIN_SCHEMA_VERSION_FOR_RETURN_BY_VALUE_FACT,
    _MIN_SCHEMA_VERSION_FOR_SURFACE_FACTS,
    _MIN_SCHEMA_VERSION_FOR_TYPE_ABI_FACTS,
    _MIN_SCHEMA_VERSION_FOR_TYPE_IDENTITY_FACTS,
)

if TYPE_CHECKING:
    pass


def surface_facts_or_legacy_bridge(
    owner_dict: dict[str, Any], decoded: dict[str, Any]
) -> dict[str, Any]:
    """Turn a *missing* surface-fact key back into ``None``.

    A missing/``null`` key must read as ``None`` at *every* schema version
    for these three fields, not as ``Fact.not_collected()`` the way the
    case-(a)/(b) fields do -- and that difference is deliberate. The hazard
    those fields guard against is a caller-supplied legacy default
    (``bases=[]``) being read as a confirmed value; these three have no
    legacy scalar sibling at all. What they have instead is ``visibility``,
    which every document carries, and ``model/surface_facts.py``'s bridge
    derives all three from it -- marked ``PARTIAL``, with a
    ``derived-from-legacy-visibility`` diagnostic -- precisely when they are
    ``None``. Keeping ``not_collected()`` here would *discard* that real (if
    weaker) evidence for a v46+ document whose producer simply did not set
    these fields, e.g. a snapshot built through the typed API or by a test,
    silently turning "public" into "nothing established".
    """
    for key in (
        "declared_in_headers_fact",
        "in_public_contract_fact",
        "binary_exported_fact",
    ):
        if not owner_dict.get(key):
            decoded[key] = None
    return decoded


def explicit_instantiation_value(
    fact: Fact[Any] | None,
) -> Fact[ExplicitInstantiation] | None:
    """Rebuild ``Function``/``Variable.explicit_instantiation_fact``'s value as
    an :class:`ExplicitInstantiation` member (JSON carries the string). A missing
    key reads as ``None`` before v65 and ``NOT_COLLECTED`` from it on."""
    if fact is not None and fact.value is not None:
        return replace(fact, value=ExplicitInstantiation(fact.value))
    return fact


def decode_function_facts(f: dict[str, Any], schema_version: int) -> dict[str, Any]:
    """Decode every ``Function`` ``Fact[...]`` sibling from one function dict.

    One call, spread into the ``Function(**decode_function_facts(f), ...)``
    constructor call, mirroring :func:`decode_record_facts`/
    :func:`decode_variable_facts`. ``elf_binding_fact`` needs the same
    ``SymbolBinding`` reconstruction :func:`decode_variable_facts` performs,
    for the identical reason (``Function.elf_binding`` shares the same
    ``.value``-accessing reader convention as ``Variable.elf_binding``).
    """
    elf_binding_fact = decode_fact(
        f.get("elf_binding_fact"),
        schema_version,
        min_schema_version=_MIN_SCHEMA_VERSION_FOR_FUNCTION_CASE_B_FACTS,
    )
    if elf_binding_fact is not None and elf_binding_fact.value is not None:
        elf_binding_fact = replace(
            elf_binding_fact, value=SymbolBinding(elf_binding_fact.value)
        )
    return surface_facts_or_legacy_bridge(
        f,
        {
            "contract_attributes_fact": decode_fact(
                f.get("contract_attributes_fact"),
                schema_version,
                min_schema_version=_MIN_SCHEMA_VERSION_FOR_FUNCTION_CASE_B_FACTS,
            ),
            "is_explicit_fact": decode_fact(
                f.get("is_explicit_fact"),
                schema_version,
                min_schema_version=_MIN_SCHEMA_VERSION_FOR_FUNCTION_CASE_B_FACTS,
            ),
            "is_hidden_friend_fact": decode_fact(
                f.get("is_hidden_friend_fact"),
                schema_version,
                min_schema_version=_MIN_SCHEMA_VERSION_FOR_FUNCTION_CASE_B_FACTS,
            ),
            "source_header_fact": decode_fact(
                f.get("source_header_fact"),
                schema_version,
                min_schema_version=_MIN_SCHEMA_VERSION_FOR_FUNCTION_CASE_B_FACTS,
            ),
            "is_variadic_fact": decode_fact(
                f.get("is_variadic_fact"),
                schema_version,
                min_schema_version=_MIN_SCHEMA_VERSION_FOR_FUNCTION_CASE_B_FACTS,
            ),
            "exception_spec_fact": decode_fact(
                f.get("exception_spec_fact"),
                schema_version,
                min_schema_version=_MIN_SCHEMA_VERSION_FOR_FUNCTION_CASE_B_FACTS,
            ),
            "is_override_fact": decode_fact(
                f.get("is_override_fact"),
                schema_version,
                min_schema_version=_MIN_SCHEMA_VERSION_FOR_FUNCTION_CASE_B_FACTS,
            ),
            "hidden_friend_owner_fact": decode_fact(
                f.get("hidden_friend_owner_fact"),
                schema_version,
                min_schema_version=_MIN_SCHEMA_VERSION_FOR_FUNCTION_CASE_B_FACTS,
            ),
            "elf_binding_fact": elf_binding_fact,
            "is_compiler_generated_fact": decode_fact(
                f.get("is_compiler_generated_fact"),
                schema_version,
                min_schema_version=_MIN_SCHEMA_VERSION_FOR_FUNCTION_CASE_B_FACTS,
            ),
            "deprecated_fact": decode_fact_with_legacy_presence(
                f,
                "deprecated",
                schema_version,
                _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS,
            ),
            "is_inline_fact": decode_fact_with_legacy_presence(
                f,
                "is_inline",
                schema_version,
                _MIN_SCHEMA_VERSION_FOR_INLINE_FACT,
            ),
            "declared_in_headers_fact": decode_fact(
                f.get("declared_in_headers_fact"),
                schema_version,
                min_schema_version=_MIN_SCHEMA_VERSION_FOR_SURFACE_FACTS,
            ),
            "in_public_contract_fact": decode_fact(
                f.get("in_public_contract_fact"),
                schema_version,
                min_schema_version=_MIN_SCHEMA_VERSION_FOR_SURFACE_FACTS,
            ),
            "binary_exported_fact": decode_fact(
                f.get("binary_exported_fact"),
                schema_version,
                min_schema_version=_MIN_SCHEMA_VERSION_FOR_SURFACE_FACTS,
            ),
            "return_type_identities_fact": validated_identities(
                decode_fact(
                    f.get("return_type_identities_fact"),
                    schema_version,
                    min_schema_version=_MIN_SCHEMA_VERSION_FOR_TYPE_IDENTITY_FACTS,
                )
            ),
            "return_type_abi_fact": decode_fact(
                f.get("return_type_abi_fact"),
                schema_version,
                min_schema_version=_MIN_SCHEMA_VERSION_FOR_TYPE_ABI_FACTS,
                value_decoder=abi_fundamental_from_json,
            ),
            "template_param_defaults_fact": validated_template_param_defaults(
                decode_fact(
                    f.get("template_param_defaults_fact"),
                    schema_version,
                    _MIN_SCHEMA_VERSION_FOR_FUNCTION_TEMPLATE_PARAM_DEFAULTS,
                )
            ),
            "return_by_value_fact": decode_fact(
                f.get("return_by_value_fact"),
                schema_version,
                min_schema_version=_MIN_SCHEMA_VERSION_FOR_RETURN_BY_VALUE_FACT,
            )
            if f.get("return_by_value_fact") is not None
            else None,
            "explicit_instantiation_fact": explicit_instantiation_value(
                decode_fact(
                    f.get("explicit_instantiation_fact"),
                    schema_version,
                    _MIN_SCHEMA_VERSION_FOR_EXPLICIT_INSTANTIATION_FACT,
                )
            ),
        },
    )
