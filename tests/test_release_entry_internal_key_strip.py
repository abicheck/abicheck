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

"""No internal stash key survives into a rendered release entry.

`_strip_diff_results_and_adjust_verdict` popped six named keys. A member
entry is then serialized straight to JSON, so a *seventh* stash key --
added by any later change, for any reason -- becomes
``TypeError: Object of type X is not JSON serializable`` raised at the very
end of a release run, after every member's comparison has already been
paid for. That is not hypothetical: it is what `_old_junit_inventory` did
to a real six-member run, and the named list had the same trap waiting for
whatever key came next.

So the invariant under test is the *class*, not that key: **no key
beginning with `_` reaches a rendered entry, whatever it is called and
whatever it holds** — asserted with adversarially-chosen keys the
implementation has never seen, and with a value that is deliberately
un-serializable so a regression fails loudly rather than rendering
something plausible.
"""

from __future__ import annotations

import json

import pytest

from abicheck.cli_compare_release_matrix import (
    _strip_diff_results_and_adjust_verdict,
    strip_internal_keys,
)


class _NotSerializable:
    """Stands in for an AbiSnapshot/SymbolInventory/DiffResult: json.dumps
    refuses it, which is the failure mode this guards."""


def _entry(**extra: object) -> dict[str, object]:
    entry: dict[str, object] = {"library": "libfoo.so", "verdict": "compatible"}
    entry.update(extra)
    return entry


#: Deliberately including keys no implementation has ever named, alongside
#: the real ones, so passing cannot mean "the list grew by one".
INTERNAL_KEYS = [
    "_diff_result",
    "_old_snapshot",
    "_new_snapshot",
    "_old_bundle_evidence",
    "_new_bundle_evidence",
    "_bundle_key",
    "_old_junit_inventory",
    "_pattern_modulations_text",
    "_scope_ledger_text",
    "_suppression_audit_text",
    "_a_key_no_implementation_has_ever_seen",
    "_",
    "_x",
    "_future_stash_2099",
]


class TestInternalKeysAreStripped:
    @pytest.mark.parametrize("key", INTERNAL_KEYS)
    def test_each_key_is_removed_whatever_it_is_called(self, key):
        entries = [_entry(**{key: _NotSerializable()})]
        _strip_diff_results_and_adjust_verdict(entries, [], "compatible")
        assert key not in entries[0]

    def test_every_internal_key_at_once_and_the_result_serializes(self):
        """The end-to-end property: the rendered entry is JSON-encodable.

        This is the assertion that actually corresponds to the observed
        failure — a per-key `not in` check passes against a strip that
        leaves some *other* unserializable value behind.
        """
        entries = [_entry(**{k: _NotSerializable() for k in INTERNAL_KEYS})]
        _strip_diff_results_and_adjust_verdict(entries, [], "compatible")
        assert not [k for k in entries[0] if k.startswith("_")]
        json.dumps(entries[0])  # raises TypeError on regression

    def test_public_keys_are_untouched(self):
        """A vacuity guard: a strip that emptied the entry would pass every
        assertion above while destroying the report."""
        entries = [
            _entry(
                _old_snapshot=_NotSerializable(),
                findings=[{"kind": "func_removed"}],
                annotations=[],
                impact_table_view=None,
            )
        ]
        _strip_diff_results_and_adjust_verdict(entries, [], "compatible")
        assert entries[0] == {
            "library": "libfoo.so",
            "verdict": "compatible",
            "findings": [{"kind": "func_removed"}],
            "annotations": [],
            "impact_table_view": None,
        }

    def test_several_members_are_each_stripped(self):
        entries = [
            _entry(library=f"lib{i}.so", _old_snapshot=_NotSerializable())
            for i in range(6)
        ]
        _strip_diff_results_and_adjust_verdict(entries, [], "compatible")
        assert all("_old_snapshot" not in e for e in entries)
        json.dumps(entries)

    def test_an_entry_with_no_internal_keys_is_unchanged(self):
        entries = [_entry()]
        _strip_diff_results_and_adjust_verdict(entries, [], "compatible")
        assert entries[0] == {"library": "libfoo.so", "verdict": "compatible"}


class TestTheRuleOnItsOwn:
    """``strip_internal_keys`` is the rule; the release pass is one caller.

    Tested directly as well as through that caller, per AGENTS.md's
    primitive-level guidance: a reusable predicate-shaped helper should
    state its own contract, not inherit it from whichever caller happens
    to exercise it today.
    """

    @pytest.mark.parametrize("key", INTERNAL_KEYS)
    def test_it_removes_any_underscore_prefixed_key(self, key):
        entry = {key: _NotSerializable(), "kept": 1}
        strip_internal_keys(entry)
        assert entry == {"kept": 1}

    @pytest.mark.parametrize(
        "key", ["library", "verdict", "findings", "a_b", "x_", "no_leading"]
    )
    def test_it_keeps_any_key_without_the_prefix(self, key):
        """Including keys that merely *contain* an underscore."""
        entry = {key: "value"}
        strip_internal_keys(entry)
        assert entry == {key: "value"}

    def test_it_is_idempotent(self):
        entry = {"_x": 1, "keep": 2}
        strip_internal_keys(entry)
        strip_internal_keys(entry)
        assert entry == {"keep": 2}

    def test_it_mutates_in_place_and_returns_nothing(self):
        entry = {"_x": 1}
        assert strip_internal_keys(entry) is None
        assert entry == {}

    def test_an_empty_entry_is_left_alone(self):
        entry: dict[str, object] = {}
        strip_internal_keys(entry)
        assert entry == {}
