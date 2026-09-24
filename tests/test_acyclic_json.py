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

"""``storage.acyclic_json``: the collector pause is invisible except in time.

Two contracts: the parse returns exactly what ``json.loads`` returns, and
the caller's collector setting is restored on every exit path -- a pause
that leaked would silently turn cyclic collection off for the rest of the
process.
"""

from __future__ import annotations

import gc
import json
from collections.abc import Iterator

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.storage.acyclic_json import gc_paused, loads_acyclic

_json = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | st.text(),
    lambda inner: (
        st.lists(inner, max_size=4)
        | st.dictionaries(st.text(max_size=5), inner, max_size=4)
    ),
    max_leaves=20,
)


@pytest.fixture(autouse=True)
def _restore_gc() -> Iterator[None]:
    was = gc.isenabled()
    yield
    (gc.enable if was else gc.disable)()


@settings(max_examples=200, deadline=None)
@given(_json)
def test_same_value_as_json_loads(value: object) -> None:
    text = json.dumps(value)
    assert loads_acyclic(text) == json.loads(text)
    assert loads_acyclic(text.encode()) == json.loads(text)


@pytest.mark.parametrize("enabled_before", [True, False])
@pytest.mark.parametrize("raises", [True, False])
def test_the_callers_setting_is_restored(enabled_before: bool, raises: bool) -> None:
    (gc.enable if enabled_before else gc.disable)()
    seen_inside: list[bool] = []
    try:
        with gc_paused():
            seen_inside.append(gc.isenabled())
            if raises:
                raise ValueError("boom")
    except ValueError:
        assert raises
    assert seen_inside == [False]
    assert gc.isenabled() is enabled_before


def test_a_malformed_document_raises_and_still_restores() -> None:
    gc.enable()
    with pytest.raises(json.JSONDecodeError):
        loads_acyclic("{not json")
    assert gc.isenabled()


def test_nested_pauses_restore_outermost_setting() -> None:
    gc.enable()
    with gc_paused():
        with gc_paused():
            assert not gc.isenabled()
        assert not gc.isenabled()
    assert gc.isenabled()


def test_overlapping_pauses_on_two_threads_restore_once_the_last_ends() -> None:
    # A enters, B enters, A leaves, B leaves: collection must stay paused
    # until B leaves, and must be back on afterwards. Restoring per pause
    # re-enabled it under B, then left it disabled for good once B restored
    # the "disabled" state it had recorded on entry.
    import threading

    gc.enable()
    a_in, b_in, a_out = threading.Event(), threading.Event(), threading.Event()
    seen: list[bool] = []

    def first() -> None:
        with gc_paused():
            a_in.set()
            b_in.wait(5)
        a_out.set()

    def second() -> None:
        a_in.wait(5)
        with gc_paused():
            b_in.set()
            a_out.wait(5)
            seen.append(gc.isenabled())

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert seen == [False]
    assert gc.isenabled()
