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

"""``Function.is_compiler_generated`` round-trip (schema v27) — closes the
castxml L4 extractor bug documented in ``AGENTS.md``'s "PR C" known-gaps
entry; see that entry and ``Function.is_compiler_generated``'s own docstring
for the full account. Split out of ``test_serialization_roundtrip.py`` to
keep that module under its test-file size cap.
"""

from __future__ import annotations

from abicheck.model import Function
from abicheck.serialization import snapshot_from_dict
from tests.test_serialization_roundtrip import _make_snap, _minimal_dict, _round_trip


class TestFunctionIsCompilerGeneratedRoundTrip:
    def test_true_survives_roundtrip(self) -> None:
        fn = Function(
            name="operator=",
            mangled="_ZN6WidgetaSERKS_",
            return_type="Widget&",
            is_compiler_generated=True,
        )
        snap = _make_snap(functions=[fn])
        reloaded = _round_trip(snap)
        assert reloaded.functions[0].is_compiler_generated is True

    def test_false_survives_roundtrip(self) -> None:
        fn = Function(
            name="sum",
            mangled="_ZNK6Widget3sumEv",
            return_type="int",
            is_compiler_generated=False,
        )
        snap = _make_snap(functions=[fn])
        reloaded = _round_trip(snap)
        assert reloaded.functions[0].is_compiler_generated is False

    def test_defaults_to_none_when_absent(self) -> None:
        """A pre-v27 snapshot dict predating this field must still load, as
        None ("not captured") -- not a confirmed True/False either way."""
        d = _minimal_dict(
            functions=[{"name": "f", "mangled": "_Z1fv", "return_type": "void"}]
        )
        reloaded = snapshot_from_dict(d)
        assert reloaded.functions[0].is_compiler_generated is None
