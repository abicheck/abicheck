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

"""ChangeKindRegistry pickle round-trip re-validation.

Split out of test_architecture_refactor.py once that file reached the
architecture gate's 1200-line test-file cap (see its own module docstring
for the split precedent this follows).
"""

from __future__ import annotations

import pickle

import pytest

from abicheck.change_registry import ChangeKindMeta, ChangeKindRegistry, Verdict


def test_registry_pickle_revalidates_on_load():
    """Unpickling a registry re-runs ``__init__``'s own validation.

    Codex review, PR #882, fresh evidence: pickle's default protocol
    restores an instance via ``cls.__new__(cls)`` plus a raw ``__dict__``
    update, bypassing ``__init__`` (and therefore ``_validate_entry()``/the
    duplicate-key check) entirely. A pickle holding state today's
    validation would reject — e.g. one produced by an older revision
    before a given rule existed, or one built by directly poking
    ``_entries`` — would load as a fully "real" ``ChangeKindRegistry``
    without ever being checked. Fixed via ``ChangeKindRegistry.__reduce__``,
    which makes unpickling reconstruct through ``ChangeKindRegistry(entries)``
    exactly like any other construction path.
    """
    # A registry the constructor legitimately accepts round-trips.
    ok_entry = ChangeKindMeta("ok_kind", Verdict.BREAKING, impact="i")
    reg = ChangeKindRegistry([ok_entry])
    rehydrated = pickle.loads(pickle.dumps(reg))
    assert set(rehydrated.entries) == {"ok_kind"}
    assert rehydrated.impact_text() == {"ok_kind": "i"}
    assert rehydrated is not reg

    # State the constructor would reject (empty impact) must be rejected
    # on unpickle too, not silently restored via the default protocol's
    # __init__-bypassing __new__ + __dict__ update.
    bad_entry = ChangeKindMeta("bad_kind", Verdict.BREAKING)  # impact=""
    bad_reg = object.__new__(ChangeKindRegistry)
    bad_reg._entries = {"bad_kind": bad_entry}
    payload = pickle.dumps(bad_reg)
    with pytest.raises(ValueError, match="impact must be non-empty"):
        pickle.loads(payload)


def test_registry_setstate_revalidates_a_pre_reduce_legacy_pickle():
    """A pickle written *before* ``__reduce__`` existed is also revalidated.

    Codex review, PR #882, fresh evidence: ``__reduce__`` only governs
    pickles written under this revision — a real production ``REGISTRY``
    pickle written by an older revision (before ``__reduce__`` existed)
    still carries the default protocol's raw ``__dict__`` payload, which
    ``ChangeKindRegistry.__setstate__`` must intercept instead. Simulated
    directly (as the existing ``ChangeKindMeta.__setstate__`` legacy-pickle
    tests do) since Python's unpickling machinery decides whether to call
    ``__setstate__`` by checking the *current* class, not by re-executing
    the writer's original pickle bytes — so calling it directly on a
    freshly ``__new__``-created instance exercises the exact code path a
    real legacy pickle would hit.
    """
    ok_entry = ChangeKindMeta("ok_kind", Verdict.BREAKING, impact="i")
    restored = object.__new__(ChangeKindRegistry)
    restored.__setstate__({"_entries": {"ok_kind": ok_entry}})
    assert set(restored.entries) == {"ok_kind"}

    bad_entry = ChangeKindMeta("bad_kind", Verdict.BREAKING)  # impact=""
    restored_bad = object.__new__(ChangeKindRegistry)
    with pytest.raises(ValueError, match="impact must be non-empty"):
        restored_bad.__setstate__({"_entries": {"bad_kind": bad_entry}})


def test_registry_setstate_refuses_to_mutate_an_already_initialized_instance():
    """``__setstate__`` is a public method, callable directly on a live registry.

    Codex review, PR #882, fresh evidence: calling it on an
    already-constructed instance (not only the blank instance the
    unpickler creates) would silently replace that instance's
    ``_entries`` in place — on the production ``REGISTRY`` this would
    desync it from every classification set derived from it at import
    time. Guarded the same way ``ChangeKindMeta.__setstate__`` guards
    its own instance.
    """
    ok_entry = ChangeKindMeta("ok_kind", Verdict.BREAKING, impact="i")
    reg = ChangeKindRegistry([ok_entry])
    with pytest.raises(TypeError, match="already-initialized"):
        reg.__setstate__({"_entries": {}})
    # The guard must reject before doing anything -- the live registry's
    # own state is untouched.
    assert set(reg.entries) == {"ok_kind"}
