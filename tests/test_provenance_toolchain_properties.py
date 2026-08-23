# Copyright 2026 Nikolay Petrov
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

"""Property-based tests for provenance.py's structural toolchain/system
include-directory recognition (``_is_toolchain_compiler_include_dir``,
``_is_bare_system_dir``, ``_segments``).

This module exists because the example-based tests in test_provenance.py
were, across two rounds of review on the same PR, each individually shown to
miss a real case: the first cut unconditionally matched a bare
``include/c++`` anywhere (a false-positive risk on an unrelated project
path); the fix for that then under-protected a nested nested subdirectory
(``.../include/c++/bits``) and the traditional, non-conda-forge split layout
(``/usr/include/c++/<version>``) from being promoted to a public directory.
Each individual round's own examples passed the fix that introduced the next
round's gap — which is exactly the failure mode this file's own convention
(AGENTS.md's "Primitive-level property tests") exists to catch: a property
stated as an invariant over the whole input space, not a fixed list of
examples chosen after the fact.

The two invariants this suite pins, independent of any specific path string:

1. **No false positive**: a path with no recognized toolchain/system anchor
   anywhere in it is never classified as a toolchain include directory,
   regardless of what generic-sounding segments (``include``, ``c++``,
   ``lib``) it happens to contain elsewhere.
2. **No false negative at any depth**: a path that DOES contain a
   recognized anchor is classified as one, regardless of how many extra
   segments come before or after the anchor -- closing the exact
   "the anchor plus one more subdirectory slipped through" shape both
   review-round gaps had.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.provenance import (
    _TARGET_TRIPLE_RE,
    _TOOLCHAIN_VERSION_RE,
    _is_bare_system_dir,
    _is_toolchain_compiler_include_dir,
    _segments,
)

pytestmark = pytest.mark.slow

# Path-segment alphabet: identifier-ish tokens only (no "/", no "..", no
# empty string) so generated segments always round-trip through _segments()
# unchanged -- the property is about the *classification* logic, not
# _segments()'s own parsing (covered separately below).
_SEGMENT = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="_-."
    ),
    min_size=1,
    max_size=10,
).filter(lambda s: s not in ("", ".", "..") and "/" not in s and "\\" not in s)

_PREFIX_SUFFIX = st.lists(_SEGMENT, max_size=4)
_TRIPLE_OR_VERSION = _SEGMENT

# Compiler-shaped generators (round-3 review, Codex: the earlier revision
# wildcarded the triple/version components unconditionally, matching a
# project path like lib/gcc/backend/v1/include too). Built from small
# integer/word components joined the way a real triple/version actually is,
# not by importing the module's own regex -- an independent notion of "looks
# like a real triple/version" for the "recognized" side of the property, the
# "rejected" side below cross-checks these generators against the module's
# actual regexes instead of re-deriving a second definition of "shaped".
_TRIPLE_COMPONENT = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=8
)
_REAL_TRIPLE = st.lists(_TRIPLE_COMPONENT, min_size=2, max_size=4).map(
    "-".join
)
_REAL_VERSION = st.lists(
    st.integers(min_value=0, max_value=99), min_size=1, max_size=3
).map(lambda parts: ".".join(str(p) for p in parts))


@given(
    before=_PREFIX_SUFFIX,
    triple=_REAL_TRIPLE,
    version=_REAL_VERSION,
    include_kind=st.sampled_from(("include", "include-fixed")),
    after=_PREFIX_SUFFIX,
)
@settings(max_examples=200)
def test_gcc_private_include_recognized_at_any_depth(
    before, triple, version, include_kind, after
) -> None:
    """lib/gcc/<triple>/<version>/include[-fixed] is recognized regardless
    of what surrounds it -- an arbitrary environment prefix before, and an
    arbitrary subdirectory (bits/, ext/, a header filename, ...) after --
    as long as the triple/version components are actually compiler-shaped."""
    segs = (*before, "lib", "gcc", triple, version, include_kind, *after)
    assert _is_toolchain_compiler_include_dir(segs) is True
    assert _is_bare_system_dir(segs) is True


@given(
    before=_PREFIX_SUFFIX,
    version=_REAL_VERSION,
    after=_PREFIX_SUFFIX,
)
@settings(max_examples=200)
def test_clang_builtin_include_recognized_at_any_depth(before, version, after) -> None:
    segs = (*before, "lib", "clang", version, "include", *after)
    assert _is_toolchain_compiler_include_dir(segs) is True
    assert _is_bare_system_dir(segs) is True


# A segment that is NOT compiler-shaped by either rule (no "-", not
# all-digits-and-dots) -- generated from letters only, filtered against the
# module's own regexes so the property below is a genuine cross-check, not
# a tautology restating the generator's own construction.
_NON_COMPILER_SHAPED_SEGMENT = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=10
).filter(
    lambda s: not _TARGET_TRIPLE_RE.match(s) and not _TOOLCHAIN_VERSION_RE.match(s)
)


@given(
    before=_PREFIX_SUFFIX,
    triple=_NON_COMPILER_SHAPED_SEGMENT,
    version=_NON_COMPILER_SHAPED_SEGMENT,
    include_kind=st.sampled_from(("include", "include-fixed")),
    after=_PREFIX_SUFFIX,
)
@settings(max_examples=200)
def test_gcc_private_include_rejected_when_triple_or_version_not_compiler_shaped(
    before, triple, version, include_kind, after
) -> None:
    """The round-3 review finding, as a property: lib/gcc/<X>/<Y>/include
    must NOT be recognized when neither X nor Y is a real triple/version
    shape -- regardless of what the two non-shaped components actually
    spell (a project directory named "backend"/"v1" is one example among
    arbitrarily many, not a special case)."""
    segs = (*before, "lib", "gcc", triple, version, include_kind, *after)
    assert _is_toolchain_compiler_include_dir(segs) is False


@given(
    prefix=st.sampled_from((("usr", "include"), ("usr", "local", "include"))),
    version=_TRIPLE_OR_VERSION,
    after=_PREFIX_SUFFIX,
)
@settings(max_examples=200)
def test_system_prefixed_libstdcxx_recognized_at_any_depth(
    prefix, version, after
) -> None:
    """The traditional (non-conda-forge) split layout -- libstdc++'s c++
    tree living separately from lib/gcc/, but anchored under a real system
    prefix (/usr/include, /usr/local/include) -- is recognized at any depth
    under the c++/<version> boundary too, not only the exact directory."""
    segs = (*prefix, "c++", version, *after)
    assert _is_toolchain_compiler_include_dir(segs) is True
    assert _is_bare_system_dir(segs) is True


# Segment alphabet deliberately EXCLUDING the literal tokens the recognizer
# keys on ("lib", "gcc", "clang", "include", "include-fixed", "c++", "usr",
# "local") so a generated path can never accidentally assemble a real anchor
# by chance -- this is what makes "no anchor anywhere" a checkable
# precondition rather than a fluke of what Hypothesis happened to draw.
_ANCHOR_TOKENS = frozenset(
    {"lib", "gcc", "clang", "include", "include-fixed", "c++", "usr", "local"}
)
_NON_ANCHOR_SEGMENT = _SEGMENT.filter(lambda s: s not in _ANCHOR_TOKENS)
_NON_ANCHOR_PATH = st.lists(_NON_ANCHOR_SEGMENT, min_size=0, max_size=8)


@given(segs=_NON_ANCHOR_PATH)
@settings(max_examples=300)
def test_no_false_positive_without_any_recognized_anchor(segs) -> None:
    """A path built entirely from non-anchor tokens must never classify as
    a toolchain include directory -- the false-positive risk both P2
    findings (the unconditional include/c++ match, and its own
    reintroduction risk) were about, stated as an invariant over the whole
    non-anchor input space rather than one hand-picked example."""
    assert _is_toolchain_compiler_include_dir(tuple(segs)) is False


class TestSegmentsDotDotCollapsing:
    """_segments() must collapse a `..` against its immediately preceding
    segment -- the fix for the real-world conda-forge repro path's own
    literal `bin/..` component (a compiler path resolved via
    `$(dirname "$CC")/..`). Checked as a property against Python's own
    posixpath.normpath, an independent reference implementation of the
    identical POSIX lexical-collapse rule, rather than hand-picked examples.
    """

    @given(segs=st.lists(_SEGMENT, min_size=1, max_size=8))
    @settings(max_examples=200)
    def test_no_dot_dot_is_a_no_op(self, segs) -> None:
        # Sanity precondition: a path with no ".." at all must segment
        # exactly as given (the collapsing logic must never fire when
        # there's nothing to collapse).
        assert _segments("/" + "/".join(segs)) == tuple(segs)

    @given(
        before=st.lists(_SEGMENT, min_size=1, max_size=4),
        cancelled=_SEGMENT,
        after=st.lists(_SEGMENT, min_size=0, max_size=4),
    )
    @settings(max_examples=300)
    def test_segment_followed_by_dot_dot_cancels_out(
        self, before, cancelled, after
    ) -> None:
        """`.../before/CANCELLED/../after` must segment identically to
        `.../before/after` -- matches posixpath.normpath's own lexical
        collapse rule (an independent reference), and directly encodes the
        real bug: a build-recorded path like `.../bin/../lib/gcc/...` must
        match its already-collapsed `.../lib/gcc/...` form."""
        import posixpath

        raw = "/" + "/".join((*before, cancelled, "..", *after))
        collapsed = "/" + "/".join((*before, *after))
        assert _segments(raw) == _segments(collapsed)
        # Cross-check against the independent stdlib reference.
        assert _segments(raw) == _segments(posixpath.normpath(raw))

    def test_leading_dot_dot_with_nothing_to_collapse_is_kept(self) -> None:
        assert _segments("../a/b") == ("..", "a", "b")
