# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""``extraction_contract_from_dict``'s ``profile_fields``/``scope_fields``
mapping fields must never manufacture -- or silently omit -- a value that
does not accurately reflect what was written to disk.

The bug class (Codex review, PR #974) went through two wrong fixes before
landing here:

1. Both fields originally built via ``{str(k): str(v) for k, v in
   raw.items()}``. Two distinct keys sharing one ``str()`` spelling (``1``
   and ``"1"``) silently collapsed into a single entry, and a non-string
   value coerced into plausible-looking fingerprint text with no signal
   anything was wrong.
2. The first fix stopped coercing but *dropped* a malformed key/value pair
   instead of rejecting the field outright -- which made a malformed
   ``profile_fields``/``scope_fields`` indistinguishable from one that
   legitimately has fewer entries, so a comparability carve-out reading a
   still-present key never learned the document was corrupt (fresh Codex
   review).

Both are exactly the class this package's own ``AGENTS.md`` invariant 6
exists to rule out ("never coerce a value a decision reads... reject
instead"), since these two fields feed ADR-050's comparability gate
directly. The fix raises ``TypeError`` for a *present* but wrong-shaped
field -- container or per-pair -- and reserves the quiet ``{}`` degrade for
a field that is genuinely absent (missing key or explicit ``null``), the
same "no evidence" spelling every other optional contract field already
accepts.
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
    def test_a_malformed_pair_rejects_the_whole_field(self, raw: dict) -> None:
        # The one invariant this whole module exists to protect: a
        # malformed pair must never be silently absorbed -- neither
        # stringified nor dropped -- because either way a decision reading
        # the resulting mapping cannot tell the document was corrupt.
        with pytest.raises(TypeError):
            extraction_contract_from_dict({"profile_fields": raw})

    def test_a_real_string_key_never_collides_with_its_int_twin(self) -> None:
        """The exact collapse the original bug produced: `1` and `"1"` used
        to become one dict entry, with iteration order picking the
        survivor. The fix must not accept this input at all."""
        with pytest.raises(TypeError):
            extraction_contract_from_dict(
                {"profile_fields": {1: "int-form", "1": "str-form"}}
            )

    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param("not-a-dict", id="string"),
            pytest.param(["std", "c++17"], id="list"),
            pytest.param(42, id="int"),
        ],
    )
    def test_a_present_non_mapping_container_is_rejected(self, raw: Any) -> None:
        # Present but the wrong shape entirely -- must fail the load, not
        # read as "no fields".
        with pytest.raises(TypeError):
            extraction_contract_from_dict({"profile_fields": raw})

    def test_an_absent_field_degrades_to_empty(self) -> None:
        # Genuinely missing (the key never written) is the one legitimate
        # "no evidence" case -- distinct from a field that IS present but
        # malformed, which must raise instead.
        contract = extraction_contract_from_dict({})
        assert contract is not None
        assert contract.profile_fields == {}
        assert contract.scope_fields == {}

    def test_an_explicit_null_field_degrades_to_empty(self) -> None:
        # The same "no evidence" spelling every other optional contract
        # field already accepts (profile_fingerprint/scope_fingerprint).
        contract = extraction_contract_from_dict(
            {"profile_fields": None, "scope_fields": None}
        )
        assert contract is not None
        assert contract.profile_fields == {}
        assert contract.scope_fields == {}

    def test_one_malformed_field_does_not_corrupt_its_well_formed_sibling(
        self,
    ) -> None:
        # profile_fields and scope_fields are independent doors: a
        # malformed profile_fields must not affect scope_fields parsing at
        # all (the exception simply propagates before scope_fields is
        # reached, so this asserts the exception itself carries the right
        # field name rather than a generic message).
        with pytest.raises(TypeError, match="profile_fields"):
            extraction_contract_from_dict(
                {"profile_fields": {1: "bogus"}, "scope_fields": {"abi": "itanium"}}
            )
