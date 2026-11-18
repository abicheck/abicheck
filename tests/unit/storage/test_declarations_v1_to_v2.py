"""The ``declarations`` v1 -> v2 identity migration (generation 82).

The step must accept a v1 payload that cannot carry ``initializer_hex``, and
refuse one that does, since only a v2 writer emits the key.
"""

from __future__ import annotations

import pytest

from abicheck.storage.dto_declarations_migration import declarations_v1_to_v2


def test_a_v1_payload_without_the_key_is_unchanged() -> None:
    payload = {"variables": [{"name": "g", "mangled": "g", "type": "int"}]}
    assert declarations_v1_to_v2(payload) is payload


def test_a_v1_payload_carrying_the_key_is_refused() -> None:
    payload = {"variables": [{"name": "g", "initializer_hex": "03000000"}]}
    with pytest.raises(ValueError, match="initializer_hex"):
        declarations_v1_to_v2(payload)


def test_a_payload_without_variables_is_unchanged() -> None:
    assert declarations_v1_to_v2({}) == {}
