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

"""A closure's identity must survive a genuinely RAW, pre-strip on-disk
baseline, not just the already-stripped "legacy" shape
``tests/test_lambda_identity_ordinal.py`` covers.

Split out of that file (Codex review: the added block pushed it past the
architecture gate's 1200-line test-file cap) rather than folded into it --
same fixture family, same closure-identity invariant, but exercising the
``storage.snapshot_load_normalization`` on-load migration specifically.

Reported: ``strip_anonymous_type_location`` is only ever applied at
header-extraction time (the two header-mode dumpers' own parsing code), never
on the ``snapshot_from_dict`` load path. A baseline written before that
normalizer existed -- or by a dumper build that never called it -- still
carries the fully raw ``(lambda at <checkout-root>/<header>:<line>:<col>)``
spelling on disk. ``renumber_anonymous_closure_identities``'s own marker
regex requires the colon that only the STRIPPED spelling has right after the
marker keyword, so loading such a baseline left its closures completely
unrenumbered: still absolute-path-and-line-tainted, while a freshly dumped
snapshot of the identical, unedited declaration is fully stripped and
ordinal-renumbered -- a spurious type/func removed+added pair purely from
where/when the baseline was produced, not from any real ABI change. Fixed by
``snapshot_from_dict`` calling ``storage.snapshot_load_normalization.
normalize_anonymous_type_spellings_on_load`` immediately before renumbering.
"""

from __future__ import annotations

import json
from pathlib import Path

from hypothesis import given, settings, strategies as st

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.model import AbiSnapshot, Function, RecordType
from abicheck.name_classification import strip_anonymous_type_location
from abicheck.qualified_name_segments import renumber_anonymous_closure_identities
from abicheck.serialization import load_snapshot, snapshot_from_dict
from abicheck.storage.snapshot_load_normalization import (
    normalize_anonymous_type_spellings_on_load,
)


def _closure(header: str, line: int, col: int) -> str:
    """Already-stripped closure marker, matching a freshly dumped snapshot's
    own spelling (the dumpers strip at extraction time, before this text
    would ever reach a snapshot field)."""
    return strip_anonymous_type_location(f"(lambda at /src/x/{header}:{line}:{col})")


def _record(name: str, qualified: str | None = None) -> RecordType:
    return RecordType(name=name, kind="class", qualified_name=qualified, size_bits=8)


def _raw_closure(
    header: str, line: int, col: int, root: str = "/home/build/src"
) -> str:
    """A genuinely RAW, pre-``strip_anonymous_type_location`` closure marker
    -- what a baseline written before that normalizer existed (or one
    produced by a dumper build that predates it) actually carries on disk,
    unlike this module's own ``_closure`` helper above which already applies
    the strip before the fixture is ever built."""
    return f"(lambda at {root}/{header}:{line}:{col})"


class TestRawPreStripBaselinesAreNormalizedOnLoad:
    """A baseline persisted by an abicheck build from *before*
    ``strip_anonymous_type_location`` existed (or a dumper build that never
    called it) still carries the fully raw ``(lambda at
    <checkout-root>/<header>:<line>:<col>)`` spelling on disk -- not the
    intermediate ``(lambda:<basename>:<line>:<col>)`` form every "legacy"
    fixture in ``test_lambda_identity_ordinal.py`` already starts from.
    """

    def _raw_legacy_dict(
        self, line: int, header: str = "task_group.h", root: str = "/home/build/src"
    ) -> dict:
        owner = f"tbb::detail::d1::raii_guard<{_raw_closure(header, line, 26, root)}>"
        return {
            "library": "libtbb.so",
            "version": "2021.13.0",
            "schema_version": 25,
            "types": [
                {
                    "name": owner.rsplit("::", 1)[-1],
                    "qualified_name": owner,
                    "kind": "class",
                    "size_bits": 8,
                }
            ],
            "functions": [
                {
                    "name": "raii_guard::raii_guard",
                    "mangled": f"__abicheck_ctor__{owner}()",
                    "return_type": "void",
                }
            ],
        }

    def test_loading_a_raw_pre_strip_snapshot_strips_and_renumbers_it(self) -> None:
        loaded = snapshot_from_dict(self._raw_legacy_dict(522))
        qualified = loaded.types[0].qualified_name
        assert qualified is not None
        assert "#" in qualified
        assert " at " not in qualified

    def test_loading_a_raw_pre_strip_snapshot_from_a_real_json_file_on_disk(
        self, tmp_path: Path
    ) -> None:
        """Same fixture as above, but through the REAL public loading
        boundary (`load_snapshot` -> `snapshot_io.read_snapshot_text` ->
        `json.loads` -> `snapshot_from_dict`) against an actual file on
        disk, not a hand-built Python dict handed directly to the internal
        function -- the exact "real dependency" gap ADR-059 SS12 warns
        about for a serialization-boundary fix (a test against only the
        in-memory shortcut can pass identically before and after the bug)."""
        path = tmp_path / "onetbb_old.abi.json"
        path.write_text(json.dumps(self._raw_legacy_dict(522)), encoding="utf-8")

        loaded = load_snapshot(path)

        qualified = loaded.types[0].qualified_name
        assert qualified is not None
        assert "#" in qualified
        assert " at " not in qualified
        assert "/home/build/src" not in qualified
        assert ":522:" not in qualified

    def test_raw_pre_strip_baseline_agrees_with_a_fresh_dump_across_line_and_root_drift(
        self,
    ) -> None:
        legacy_baseline = snapshot_from_dict(self._raw_legacy_dict(522))

        # A fresh dump: checked out to a DIFFERENT root, with an unrelated
        # earlier edit shifting the same, unedited closure to a new line --
        # exactly what the real dumper produces (strip, then renumber),
        # simulated the same way this module's own `_closure` helper does
        # for the intermediate-form fixtures above.
        fresh_bare_name = (
            "raii_guard<"
            f"{_raw_closure('task_group.h', 539, 26, root='/ci/checkout/src')}>"
        )
        fresh_owner = f"tbb::detail::d1::{fresh_bare_name}"
        fresh = AbiSnapshot(
            library="libtbb.so",
            version="2022.3.0",
            types=[_record(fresh_bare_name, qualified=fresh_owner)],
            functions=[
                Function(
                    name="raii_guard::raii_guard",
                    mangled=f"__abicheck_ctor__{fresh_owner}()",
                    return_type="void",
                )
            ],
        )
        normalize_anonymous_type_spellings_on_load(fresh)
        renumber_anonymous_closure_identities(fresh)

        assert legacy_baseline.types[0].qualified_name == fresh.types[0].qualified_name
        assert legacy_baseline.functions[0].mangled == fresh.functions[0].mangled

        result = compare(legacy_baseline, fresh)
        noisy_kinds = {
            ChangeKind.FUNC_REMOVED,
            ChangeKind.FUNC_ADDED,
            ChangeKind.TYPE_REMOVED,
            ChangeKind.TYPE_ADDED,
        }
        assert not ({c.kind for c in result.changes} & noisy_kinds)

    @given(
        old_line=st.integers(min_value=1, max_value=5000),
        new_line=st.integers(min_value=1, max_value=5000),
        old_root=st.sampled_from(["/home/build/src", "/home/alice/onetbb", "/a"]),
        new_root=st.sampled_from(["/ci/checkout/src", "/home/bob/onetbb-2", "/b/c/d"]),
        header=st.sampled_from(["task_group.h", "flow_graph.h", "concurrent_queue.h"]),
    )
    @settings(max_examples=50)
    def test_property_no_phantom_findings_for_any_line_or_root_drift(
        self, old_line: int, new_line: int, old_root: str, new_root: str, header: str
    ) -> None:
        """General invariant (not just the one reported line/root pair
        above): a raw pre-strip baseline compared against a fresh dump of
        the identical, unedited closure-parameterized declaration must
        never manufacture a removed/added pair, regardless of which lines
        or checkout roots either side happens to use."""
        owner_old = f"raii_guard<{_raw_closure(header, old_line, 26, old_root)}>"
        legacy_dict = {
            "library": "libtbb.so",
            "version": "2021.13.0",
            "schema_version": 25,
            "types": [{"name": owner_old, "kind": "class", "size_bits": 8}],
        }
        legacy_baseline = snapshot_from_dict(legacy_dict)

        owner_new = f"raii_guard<{_raw_closure(header, new_line, 26, new_root)}>"
        fresh = AbiSnapshot(
            library="libtbb.so",
            version="2022.3.0",
            types=[_record(owner_new)],
        )
        normalize_anonymous_type_spellings_on_load(fresh)
        renumber_anonymous_closure_identities(fresh)

        assert legacy_baseline.types[0].name == fresh.types[0].name

        result = compare(legacy_baseline, fresh)
        noisy_kinds = {ChangeKind.TYPE_REMOVED, ChangeKind.TYPE_ADDED}
        assert not ({c.kind for c in result.changes} & noisy_kinds)

    def test_rewrite_is_idempotent_on_an_already_stripped_snapshot(self) -> None:
        """The on-load normalization is applied unconditionally on every
        load, including a snapshot that was already fully normalized (the
        overwhelmingly common case) -- it must be a true no-op there, not
        just harmless-by-luck."""
        already_stripped = AbiSnapshot(
            library="libtbb.so",
            version="2022.3.0",
            types=[_record(f"raii_guard<{_closure('task_group.h', 539, 26)}>")],
        )
        before = already_stripped.types[0].name
        normalize_anonymous_type_spellings_on_load(already_stripped)
        assert already_stripped.types[0].name == before

        already_ordinal = AbiSnapshot(
            library="libtbb.so",
            version="2022.3.0",
            types=[_record("raii_guard<(lambda:task_group.h#1)>")],
        )
        before = already_ordinal.types[0].name
        normalize_anonymous_type_spellings_on_load(already_ordinal)
        assert already_ordinal.types[0].name == before

    def test_multiple_raw_lambdas_in_one_header_still_get_distinct_ordinals(
        self,
    ) -> None:
        """Mirrors the real report's shape (thousands of raw markers in one
        baseline, not just one): several distinct raw closures in the same
        header must each survive stripping with their own identity and
        still be renumbered by RELATIVE SOURCE ORDER, exactly as a snapshot
        that was already stripped at dump time would be."""
        raw_names = [
            f"raii_guard<{_raw_closure('task_group.h', line, 26)}>"
            for line in (522, 520, 539, 528)
        ]
        legacy_dict = {
            "library": "libtbb.so",
            "version": "2021.13.0",
            "schema_version": 25,
            "types": [{"name": n, "kind": "class"} for n in raw_names],
        }
        loaded = snapshot_from_dict(legacy_dict)
        loaded_names = [t.name for t in loaded.types]

        stripped_then_renumbered = [
            f"raii_guard<{_closure('task_group.h', line, 26)}>"
            for line in (522, 520, 539, 528)
        ]
        expected_snapshot = AbiSnapshot(
            library="libtbb.so",
            version="2021.13.0",
            types=[_record(n) for n in stripped_then_renumbered],
        )
        renumber_anonymous_closure_identities(expected_snapshot)
        expected_names = [t.name for t in expected_snapshot.types]

        assert loaded_names == expected_names
        # Every entry actually got an ordinal -- none left in :line:col form.
        assert all("#" in n for n in loaded_names)
