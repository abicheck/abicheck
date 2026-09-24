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

"""``persisted_content_provably_differs``: the cheap check ahead of the
content digest.

It may answer "different" only when the two snapshots really serialize
differently. The oracle is the real serializer (``snapshot_content_digest``),
never the field list the check reads, so a field that the writer normalizes
or drops would fail here instead of silently suppressing the
same-content warning.
"""

from __future__ import annotations

import dataclasses

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.storage.snapshot_digest_cache import digest_scope
from abicheck.storage.snapshot_encode import (
    _VERBATIM_STR_FIELDS,
    _uncached_snapshot_content_digest as digest,
    persisted_content_provably_differs,
    same_persisted_content,
)
from abicheck.workflows.gate import snapshot_identity_digests


def _snap(**kw: object) -> AbiSnapshot:
    base = AbiSnapshot(
        library="libx.so.1",
        version="1.0",
        functions=[
            Function(
                name="f",
                mangled="_Z1fv",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
        ],
    )
    return dataclasses.replace(base, **kw)


_text = st.text(max_size=12)


@settings(max_examples=150, deadline=None)
@given(st.sampled_from(_VERBATIM_STR_FIELDS), _text, _text)
def test_every_listed_field_changes_the_serialized_content(
    field: str, a: str, b: str
) -> None:
    old, new = _snap(**{field: a}), _snap(**{field: b})
    if persisted_content_provably_differs(old, new):
        assert digest(old) != digest(new)
    assert persisted_content_provably_differs(old, new) == (a != b)


_REQUIRED = ("library", "version")  # the provenance section rejects None here
_fields = st.sampled_from(_VERBATIM_STR_FIELDS).flatmap(
    lambda f: st.tuples(st.just(f), _text if f in _REQUIRED else st.none() | _text)
)


@settings(max_examples=150, deadline=None)
@given(
    st.lists(_fields, max_size=4).map(dict),
    st.lists(_fields, max_size=4).map(dict),
    st.booleans(),
)
def test_never_claims_difference_for_equal_content(
    old_kw: dict[str, str | None], new_kw: dict[str, str | None], extra_fn: bool
) -> None:
    old = _snap(**old_kw)
    new = _snap(**new_kw)
    if extra_fn:
        new.functions.append(Function(name="g", mangled="_Z1gv", return_type="int"))
    if digest(old) == digest(new):
        assert not persisted_content_provably_differs(old, new)


def test_identical_content_still_gets_both_digests() -> None:
    # The same-content warning must still fire: the prefilter must not hide
    # the one case the digests exist to detect.
    with digest_scope():
        old_digest, new_digest = snapshot_identity_digests(_snap(), _snap())
    assert old_digest is not None and old_digest == new_digest
    assert same_persisted_content(_snap(), _snap())


@pytest.mark.parametrize("field", _VERBATIM_STR_FIELDS)
def test_a_differing_field_skips_both_digests(field: str) -> None:
    assert snapshot_identity_digests(_snap(**{field: "a"}), _snap(**{field: "b"})) == (
        None,
        None,
    )
    assert not same_persisted_content(_snap(**{field: "a"}), _snap(**{field: "b"}))
