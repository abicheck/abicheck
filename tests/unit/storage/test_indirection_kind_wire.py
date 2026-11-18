"""The ABI fact's ``indirection`` kind on the wire: legacy decode and migration.

A v3 snapshot's ABI fact has no ``indirection`` key. It must decode as ``none``
(the pre-kind meaning), and the v2 -> v3 declarations migration must accept
such a payload unchanged and refuse one that already carries the key, since a
v3 writer never wrote it.
"""

from __future__ import annotations

from typing import Any

import pytest

from abicheck.model.abi_fundamental import Indirection
from abicheck.storage.dto import SECTION_SCHEMA_VERSIONS, migration_source_versions
from abicheck.storage.dto_declarations_migration import (
    DECLARATIONS_MIGRATIONS,
    declarations_v2_to_v3,
)
from abicheck.storage.fact_decode import abi_fundamental_from_json, decode_abi_slot_fact

_V3_FACT = {"encoding": "unsigned", "size": 8, "align": 8, "lanes": 0}
_V4_FACT = {**_V3_FACT, "indirection": "pointer"}


def test_a_legacy_fact_without_the_kind_decodes_as_none() -> None:
    assert abi_fundamental_from_json(_V3_FACT).indirection is Indirection.NONE


def test_a_slot_fact_from_a_v3_document_decodes_as_none() -> None:
    raw = {"status": "present", "value": _V3_FACT, "diagnostics": [], "producer": None}
    decoded = decode_abi_slot_fact(raw, 69)
    assert decoded is not None
    assert decoded.value.indirection is Indirection.NONE


def test_a_pointer_fact_with_the_kind_decodes_as_a_pointer() -> None:
    assert abi_fundamental_from_json(_V4_FACT).indirection is Indirection.POINTER


def test_the_declarations_section_is_at_v3_and_reaches_it_from_v2() -> None:
    assert SECTION_SCHEMA_VERSIONS["declarations"] == 3
    assert 2 in migration_source_versions("declarations")
    assert DECLARATIONS_MIGRATIONS[2] is declarations_v2_to_v3


def _payload_with_fact(fact: dict[str, Any]) -> dict[str, Any]:
    return {"functions": [{"name": "f", "return_type_abi_fact": {"value": fact}}]}


def test_a_v2_payload_without_the_kind_migrates_unchanged() -> None:
    payload = _payload_with_fact(_V3_FACT)
    assert declarations_v2_to_v3(payload) == payload


def test_a_v2_payload_carrying_the_kind_is_refused() -> None:
    with pytest.raises(ValueError, match="indirection"):
        declarations_v2_to_v3(_payload_with_fact(_V4_FACT))
