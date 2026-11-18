"""The ``declarations`` v2 -> v3 identity migration (generation 85).

The step must accept a v2 payload that cannot carry ``member_kind_fact``, and
refuse one that does, since only a v3 writer emits the key.
"""

from __future__ import annotations

import pytest

from abicheck.storage.dto_declarations_migration import declarations_v2_to_v3


def test_a_v2_payload_without_the_key_is_unchanged() -> None:
    payload = {"functions": [{"name": "f", "mangled": "_Z1fv"}]}
    assert declarations_v2_to_v3(payload) is payload


def test_a_v2_payload_carrying_the_key_is_refused() -> None:
    payload = {"functions": [{"name": "f", "member_kind_fact": {"v": "instance"}}]}
    with pytest.raises(ValueError, match="member_kind_fact"):
        declarations_v2_to_v3(payload)


def test_a_payload_without_functions_is_unchanged() -> None:
    assert declarations_v2_to_v3({}) == {}
