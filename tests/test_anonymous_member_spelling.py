# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""A field's anonymous member type is compared by identity, not by which reader
spelled it.

The same anonymous union member can be written ``<unnamed-tag>`` by one reader
and ``Outer::<unnamed-type-u>`` by another. The spelling alone must never report
``type_field_type_changed``; a real change of the member's type still does. The
expected verdicts are written out here, not derived from the implementation.
"""

from __future__ import annotations

import pytest

from abicheck.diff_types import _field_type_genuinely_changed

_SAME_MEMBER_SPELLINGS = [
    "<unnamed-tag>",
    "<anonymous>",
    "Outer::<unnamed-type-f>",
    "_TP_CALLBACK_ENVIRON_V3::<unnamed-type-u>",
]


@pytest.mark.parametrize("old", _SAME_MEMBER_SPELLINGS)
@pytest.mark.parametrize("new", _SAME_MEMBER_SPELLINGS)
def test_spellings_of_one_anonymous_member_are_not_a_type_change(
    old: str, new: str
) -> None:
    assert _field_type_genuinely_changed(old, new, cv_facts_reliable=True) is False


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("<anonymous union>", "<anonymous struct>"),
        ("<anonymous struct>", "<anonymous union>"),
        ("<unnamed-tag>", "Foo"),
        ("Foo", "<unnamed-type-u>"),
        ("int", "long"),
    ],
)
def test_a_real_member_type_change_is_still_reported(old: str, new: str) -> None:
    assert _field_type_genuinely_changed(old, new, cv_facts_reliable=True) is True
