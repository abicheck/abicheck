# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""``extraction_contract_from_dict``'s ``profile_fields``/``scope_fields``
mapping fields must never manufacture a fingerprint value that was not
actually written to disk.

The bug class (Codex review, PR #974): both fields used to build via
``{str(k): str(v) for k, v in raw.items()}``. Two distinct keys sharing one
``str()`` spelling (``1`` and ``"1"``) silently collapsed into a single
entry, and a non-string value coerced into plausible-looking fingerprint
text with no signal anything was wrong -- exactly the class of defect this
package's own ``AGENTS.md`` invariant 6 exists to rule out ("never coerce a
value a decision reads"), since these two fields feed ADR-050's
comparability gate directly. The fix drops a malformed key/value pair
instead of stringifying it, matching this same function's own established
degrade-to-empty contract for a malformed ``profile_fingerprint``/
``scope_fingerprint`` scalar.
"""

from __future__ import annotations

from typing import Any

import pytest

from abicheck.storage.snapshot_load_normalization import extraction_contract_from_dict


class TestExtractionContractFieldsNeverCoerce:
    def test_a_well_formed_mapping_round_trips_unchanged(self) -> None:
        contract = extraction_contract_from_dict(
            {"profile_fields": {"std": "c++17"}, "scope_fields": {"abi": "itanium"}}
        )
        assert contract is not None
        assert contract.profile_fields == {"std": "c++17"}
        assert contract.scope_fields == {"abi": "itanium"}

    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param({1: "c++17"}, id="int-key"),
            pytest.param({"std": 17}, id="int-value"),
            pytest.param({None: "c++17"}, id="none-key"),
            pytest.param({"std": None}, id="none-value"),
            pytest.param({("std",): "c++17"}, id="tuple-key"),
        ],
    )
    def test_a_malformed_pair_is_dropped_not_stringified(self, raw: dict) -> None:
        contract = extraction_contract_from_dict({"profile_fields": raw})
        assert contract is not None
        # The one invariant this whole module exists to protect: nothing
        # here ever contains a value manufactured by `str()` from a
        # non-string input -- the field is either the real string that was
        # written, or the pair is simply absent.
        assert contract.profile_fields == {}

    def test_a_real_string_key_never_collides_with_its_int_twin(self) -> None:
        """The exact collapse this bug produced: `1` and `"1"` used to
        become one dict entry, with iteration order picking the survivor."""
        contract = extraction_contract_from_dict(
            {"profile_fields": {1: "int-form", "1": "str-form"}}
        )
        assert contract is not None
        # The int-keyed pair is dropped; the genuinely string-keyed one
        # survives untouched -- never a value neither pair actually wrote.
        assert contract.profile_fields == {"1": "str-form"}

    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param("not-a-dict", id="string"),
            pytest.param(["std", "c++17"], id="list"),
            pytest.param(42, id="int"),
            pytest.param(None, id="none"),
        ],
    )
    def test_a_non_dict_container_degrades_to_empty(self, raw: Any) -> None:
        contract = extraction_contract_from_dict({"profile_fields": raw})
        assert contract is not None
        assert contract.profile_fields == {}

    def test_a_valid_pair_survives_alongside_a_dropped_one(self) -> None:
        """Dropping is per-pair, not whole-field: one malformed entry must
        not discard its well-formed siblings."""
        contract = extraction_contract_from_dict(
            {"profile_fields": {"std": "c++17", 1: "bogus", "abi": "itanium"}}
        )
        assert contract is not None
        assert contract.profile_fields == {"std": "c++17", "abi": "itanium"}
