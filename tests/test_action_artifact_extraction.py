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

"""A refused artifact leaves the destination empty.

`docs/reference/verify-source-run.md` claims the whole central directory is
validated **before a byte is written**, so a hostile archive is refused
"without any of it reaching the filesystem". That claim had exactly one
test behind it -- the zip-slip case in
``test_action_run_selection.py``, which checks its own sentinel outside the
destination. Every other refusal asserted the *code* and stopped there, so
an implementation that wrote the safe entries first and refused on reaching
the bad one passed all of them while making the documented sentence false.

It was false. `inspect_archive` validated sizes, entry counts and entry
types up front, but each entry's destination path was resolved only inside
the write loop, so an archive whose second entry escaped had its first
entry written. Nothing ever escaped the destination -- the security
property held -- but a failed step that leaves a partial, attacker-chosen
tree behind is a hazard for whatever reads that directory next, and a
guarantee that is documented but untested is the kind this suite's own
rules say to make executable.

Its own module because `test_action_run_selection.py` reached the
architecture gate's 1200-line test cap, and the seam is real: which run and
pull request may be reported on is that module's subject, what may be
unpacked from the artifact is this one's.
"""

from __future__ import annotations

import pytest

from abicheck.frontends.action.run_selection import (
    ExtractionLimits,
    SourceRunRejected,
    extract_artifact,
)
from tests._action_archives import build_zip


class TestNoRefusalLeavesAnythingBehind:
    """Stated over every refusal kind, by observing the destination.

    Each archive pairs a **benign entry first** with the hostile one, which
    is what makes these capable of failing: with only the hostile entry
    there is nothing an eager implementation could have written.
    """

    BENIGN = ("safe.json", b'{"ok": true}')

    @staticmethod
    def _assert_empty(out) -> None:
        assert not out.exists() or not list(out.rglob("*")), (
            "the benign entry preceding the hostile one was written, so the "
            "archive was not validated before extraction began"
        )

    @pytest.mark.parametrize(
        ("entries", "symlinks"),
        [
            ([("../escape.json", b"{}")], ()),
            ([("/opt/abs.json", b"{}")], ()),
            ([("link.json", b"/etc/passwd")], ("link.json",)),
            ([("a/../../up.json", b"{}")], ()),
        ],
        ids=["traversal", "absolute", "symlink", "nested-traversal"],
    )
    def test_the_destination_is_untouched_after_a_structural_refusal(
        self, tmp_path, entries: list, symlinks: tuple
    ) -> None:
        archive = build_zip(tmp_path, [self.BENIGN, *entries], symlinks=symlinks)
        out = tmp_path / "out"
        with pytest.raises(SourceRunRejected):
            extract_artifact(archive, out)
        self._assert_empty(out)

    def test_the_destination_is_untouched_after_a_size_cap_refusal(
        self, tmp_path
    ) -> None:
        archive = build_zip(tmp_path, [self.BENIGN, ("big.json", b"x" * 4096)])
        out = tmp_path / "out"
        with pytest.raises(SourceRunRejected) as caught:
            extract_artifact(
                archive,
                out,
                ExtractionLimits(
                    max_total_bytes=1024,
                    max_entry_bytes=1024,
                    max_entries=10,
                    max_ratio=200.0,
                ),
            )
        assert caught.value.code in ("artifact-entry-too-large", "artifact-too-large")
        self._assert_empty(out)

    def test_the_destination_is_untouched_after_an_entry_count_refusal(
        self, tmp_path
    ) -> None:
        archive = build_zip(tmp_path, [(f"m{i}.json", b"{}") for i in range(12)])
        out = tmp_path / "out"
        with pytest.raises(SourceRunRejected) as caught:
            extract_artifact(
                archive,
                out,
                ExtractionLimits(
                    max_total_bytes=1 << 20,
                    max_entry_bytes=1 << 20,
                    max_entries=4,
                    max_ratio=200.0,
                ),
            )
        assert caught.value.code == "artifact-too-many-entries"
        self._assert_empty(out)

    def test_positive_control_a_clean_archive_does_write(self, tmp_path) -> None:
        """Without this, an `extract_artifact` that wrote nothing at all
        would pass every assertion above."""
        archive = build_zip(tmp_path, [self.BENIGN, ("more.json", b"{}")])
        out = tmp_path / "out"
        extract_artifact(archive, out)
        assert sorted(p.name for p in out.rglob("*") if p.is_file()) == [
            "more.json",
            "safe.json",
        ]
