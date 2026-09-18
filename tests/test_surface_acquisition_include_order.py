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

"""An acquisition identity may not normalize away an *ordered* input.

**Bug class.** A cache/reuse key is built by canonicalizing its inputs, and
one of those inputs is order-*sensitive*. Sorting it makes two genuinely
different requests key identically, so one request's result is served for
the other -- a wrong answer, not a slow one. Here
``build_side_identity`` sorted ``includes``, which becomes the compiler's
``-I`` search path: with ``a/choice.h`` declaring ``int api(void)`` and
``b/choice.h`` declaring ``long api(void)``, ``-I a -I b`` and
``-I b -I a`` yield different ASTs but produced one key.

**General invariant**: for *any* two include sequences that are not equal
as sequences, the acquisition key differs. Stated over generated
permutations and duplicate-bearing sequences rather than the one reported
``a``/``b`` pair, because the defect is a property of the normalization,
not of that example. A companion test keeps the *membership*-only inputs
sorted, so the fix cannot be over-applied into "never normalize anything":
those name a set and must still share a key across orderings.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from abicheck.workflows.release_public_surface import build_side_identity


def _identity(includes, *, headers=(), public_header_dirs=None):
    return build_side_identity(
        [Path(h) for h in headers],
        [Path(i) for i in includes],
        lang="c++",
        exclude_headers=(),
        public_header_dirs=(
            None
            if public_header_dirs is None
            else [Path(p) for p in public_header_dirs]
        ),
        compile_context=None,
        depth=None,
        include_dependencies=False,
    )


class TestIncludeSearchOrderIsPartOfTheKey:
    @pytest.mark.parametrize(
        "paths",
        [
            ("/a", "/b"),
            ("/b", "/a"),
            ("/x/inc", "/y/inc", "/z/inc"),
            ("/one", "/two", "/three", "/four"),
        ],
    )
    def test_every_distinct_permutation_keys_distinctly(self, paths) -> None:
        """Permutations, not "change one path" -- the defect only shows here.

        A test that swapped one path for a different path would have passed
        against the sorted implementation, since the *set* changed too.
        """
        keys = {}
        for perm in itertools.permutations(paths):
            key = _identity(perm).key()
            keys.setdefault(key, []).append(perm)
        collisions = {k: v for k, v in keys.items() if len(v) > 1}
        assert not collisions, f"distinct include orders shared a key: {collisions}"
        # Vacuity guard: permutations were actually generated.
        assert len(keys) == len(set(itertools.permutations(paths)))

    def test_the_tuple_itself_preserves_the_caller_order(self) -> None:
        given = ["/z/inc", "/a/inc", "/m/inc"]
        identity = _identity(given)
        assert list(identity.includes) == [str(Path(p).resolve()) for p in given]

    def test_a_repeated_path_is_kept_because_it_shifts_precedence(self) -> None:
        """De-duplicating is the same defect in another spelling.

        ``-I a -I b -I a`` and ``-I a -I b`` are the same *set*; collapsing
        the first onto the second would be safe here, but collapsing
        ``-I b -I a -I b`` onto ``-I b -I a`` is not the point -- the rule
        is that the key records the sequence the parse will be given.
        """
        with_dup = _identity(["/a", "/b", "/a"])
        without = _identity(["/a", "/b"])
        assert with_dup.key() != without.key()
        assert len(with_dup.includes) == 3

    def test_equal_sequences_still_share_one_acquisition(self) -> None:
        """The reuse the key exists for is not lost by the fix."""
        assert _identity(["/a", "/b"]).key() == _identity(["/a", "/b"]).key()


class TestMembershipOnlyInputsStayNormalized:
    """The fix is scoped: a set-valued input must still key order-independently."""

    def test_public_header_dirs_order_does_not_split_the_key(self, tmp_path) -> None:
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        one = _identity([], public_header_dirs=[a, b])
        two = _identity([], public_header_dirs=[b, a])
        assert one.key() == two.key()

    def test_header_roots_order_does_not_split_the_key(self, tmp_path) -> None:
        a, b = tmp_path / "h1.h", tmp_path / "h2.h"
        a.write_text("int a(void);\n")
        b.write_text("int b(void);\n")
        assert (
            _identity([], headers=[a, b]).key() == _identity([], headers=[b, a]).key()
        )
