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

"""Phase 3 of ``docs/contribute/plans/bug-class-regression-testing.md``:
the ``policy.public_surface_reachability`` bug class, generalized to
``abicheck.provenance``'s path/segment-based declaration-origin
classification (``classify_origin``/``_matches_public``/
``is_dependency_header``) -- the layer that decides PUBLIC_HEADER vs.
PRIVATE_HEADER vs. SYSTEM_HEADER vs. GENERATED for one declaration, which
``surface.py``'s reachability closure (already covered by
``test_surface_property.py``/``test_surface_seed_predicate_properties.py``,
both added alongside PR #843) builds its public/private *type* surface on
top of.

``provenance.py``'s own module docstring already commits to the invariant
this suite states as executable properties: "Matching is therefore done on
path *segments*... rather than by resolving real paths, which would be
brittle when a snapshot is produced on a different machine than the
public-header set is described on." PR #843's own first fix
(a checkout-path-dependent CastXML ctor/dtor identity: two byte-identical
header trees checked out under different absolute paths synthesized two
different ``Function.mangled`` keys) is the concrete historical instance of
exactly the failure mode this suite's relocation-invariance property targets
one layer up, in the classifier that decides whether a declaration counts as
public in the first place.

Oracle: every expected answer here is known BY CONSTRUCTION (a generator
builds two path spellings whose real, structural containment relationship
is fixed by how they were assembled, not derived from ``classify_origin``
itself) or is a metamorphic relation (the classifier's own output before and
after a semantics-preserving transformation of its input -- relocating the
checkout root, adding an unrelated directory, restating an existing path).

Deliberately scoped: this does NOT attempt the full generated include-DAG
model (``-I``/``-isystem``/``-idirafter`` resolution order, ``#include``
cycles, symlinked roots) the plan document sketches for this phase --
that model was flagged there as needing its own design review before
implementation, and building it inside this same PR risked exactly the
under-designed, maintenance-burden outcome the plan warns about. See this
file's own registry entry in ``tests/regressions/manifest.py`` for what
remains open.
"""

from __future__ import annotations

import uuid

import pytest
from hypothesis import assume, given, settings, strategies as st

from abicheck.model import ScopeOrigin
from abicheck.provenance import build_public_set, classify_origin

pytestmark = pytest.mark.slow

# Path-segment alphabet excluding every literal token `provenance.py`'s own
# heuristics key on (system/toolchain directory names, the "generated"
# family, "c++"/"include"/"lib"/"gcc"/"clang") -- mirrors
# `test_provenance_toolchain_properties.py`'s own `_NON_ANCHOR_SEGMENT`
# convention, so a generated segment can never accidentally assemble a real
# anchor and confound a property that's supposed to be about relocation/
# containment, not incidental toolchain-dir recognition.
_RESERVED_TOKENS = frozenset(
    {
        "usr",
        "include",
        "include-fixed",
        "local",
        "lib",
        "lib64",
        "gcc",
        "clang",
        "c++",
        "generated",
        "_generated",
        ".generated",
        "gen",
        "autogen",
        "library",
        "developer",
        "applications",
        "xcode.app",
        "program files",
        "vc",
        "tools",
        "windows kits",
    }
)
_SEGMENT = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="_-"
    ),
    min_size=1,
    max_size=8,
).filter(lambda s: s.lower() not in _RESERVED_TOKENS and s not in (".", ".."))
_SEGMENT_LIST = st.lists(_SEGMENT, min_size=1, max_size=3, unique=True)


def _abspath(segs: tuple[str, ...] | list[str]) -> str:
    return "/" + "/".join(segs)


def _classify(header: str, public_dirs: list[str]) -> ScopeOrigin:
    header_segs, dir_segs, have_set = build_public_set([], public_dirs)
    return classify_origin(header, header_segs, dir_segs, have_public_set=have_set)


# --------------------------------------------------------------------------
# Metamorphic: relocating the checkout must not change classification.
# --------------------------------------------------------------------------


@given(
    root_a=_SEGMENT_LIST,
    root_b=_SEGMENT_LIST,
    pub_rel=_SEGMENT_LIST,
    header_rel=st.lists(_SEGMENT, min_size=0, max_size=2),
    filename=_SEGMENT,
)
@settings(max_examples=200)
def test_classification_is_invariant_to_checkout_relocation(
    root_a: list[str],
    root_b: list[str],
    pub_rel: list[str],
    header_rel: list[str],
    filename: str,
) -> None:
    """The #843 path-taint bug generalized one layer up: the SAME relative
    structure (a public directory, and a header somewhere under or outside
    it) must classify identically regardless of which absolute checkout
    root it's rooted at.

    Both an inside-public-dir and an OUTSIDE-public-dir header are checked,
    each against its own expected ``ScopeOrigin`` (not just cross-root
    equality) -- a header nested under ``pub_rel`` for every root classifies
    trivially the same way regardless of relocation sensitivity, so that
    case alone can't distinguish this property from a checkout-tainted
    implementation (e.g. one keying off the raw absolute root string) that
    happens to move both sides consistently. The outside-public-dir header
    lives under a UUID-rooted sibling disjoint from ``root``/``pub_rel``/
    ``header_rel``, so it can never accidentally land under the public
    directory via `_contiguous_subsequence` containment."""
    assume(root_a != root_b)
    public_tail = (*pub_rel, *header_rel, f"{filename}.h")
    private_sibling = (str(uuid.uuid4()), str(uuid.uuid4()))
    private_tail = (*private_sibling, f"{filename}.h")

    def classify_under(root: list[str], header_tail: tuple[str, ...]) -> ScopeOrigin:
        public_dir = _abspath((*root, *pub_rel))
        header = _abspath((*root, *header_tail))
        return _classify(header, [public_dir])

    for root in (root_a, root_b):
        assert classify_under(root, public_tail) is ScopeOrigin.PUBLIC_HEADER
        assert classify_under(root, private_tail) is ScopeOrigin.PRIVATE_HEADER

    assert classify_under(root_a, public_tail) == classify_under(root_b, public_tail)
    assert classify_under(root_a, private_tail) == classify_under(root_b, private_tail)


@given(
    root=_SEGMENT_LIST,
    pub_rel=_SEGMENT_LIST,
    header_rel=st.lists(_SEGMENT, min_size=0, max_size=2),
    filename=_SEGMENT,
)
@settings(max_examples=150)
def test_classification_is_invariant_to_dot_and_double_dot_spellings(
    root: list[str], pub_rel: list[str], header_rel: list[str], filename: str
) -> None:
    """An equivalent path spelling with an inert `./` segment, or a
    `foo/../foo/` detour, must classify identically to the plain form --
    `_segments()`'s own `..`-collapsing (already property-tested against
    `posixpath.normpath` in `test_provenance_toolchain_properties.py`) must
    actually reach the classification layer, not just its own unit."""
    public_dir = _abspath((*root, *pub_rel))
    plain_header = _abspath((*root, *pub_rel, *header_rel, f"{filename}.h"))
    noisy_header = "/" + "/".join(
        (*root, ".", *pub_rel, "detour", "..", *header_rel, f"{filename}.h")
    )
    assert _classify(plain_header, [public_dir]) == _classify(
        noisy_header, [public_dir]
    )


@given(
    root=_SEGMENT_LIST,
    pub_rel=_SEGMENT_LIST,
    header_rel=st.lists(_SEGMENT, min_size=0, max_size=2),
    filename=_SEGMENT,
)
@settings(max_examples=150)
def test_adding_an_unrelated_public_directory_does_not_change_classification(
    root: list[str], pub_rel: list[str], header_rel: list[str], filename: str
) -> None:
    """A second, wholly unrelated public directory added to the set must not
    change how an EXISTING header classifies -- `_matches_public` is a pure
    `any(...)` over the public-dir list, so an irrelevant addition can only
    ever add a spurious match, never remove one; the unrelated directory
    here is built from UUID segments that cannot appear anywhere in the
    generated header path, so no spurious match is possible either."""
    public_dir = _abspath((*root, *pub_rel))
    header = _abspath((*root, *pub_rel, *header_rel, f"{filename}.h"))
    unrelated_dir = _abspath((str(uuid.uuid4()), str(uuid.uuid4())))

    before = _classify(header, [public_dir])
    after = _classify(header, [public_dir, unrelated_dir])
    assert before == after


# --------------------------------------------------------------------------
# Independent oracle: real segment-boundary containment, not a coincidental
# string prefix -- the "duplicate basenames"/"name-shape alone" failure
# class the plan's own invariant names explicitly.
# --------------------------------------------------------------------------


@given(
    root=_SEGMENT_LIST,
    base=_SEGMENT,
    rel=st.lists(_SEGMENT, min_size=0, max_size=2),
)
@settings(max_examples=200)
def test_directory_containment_is_a_real_path_boundary_not_a_string_prefix(
    root: list[str], base: str, rel: list[str]
) -> None:
    """A header genuinely nested under the public directory classifies
    public; a SIBLING directory whose name merely starts with the same
    string (`base` vs. `base + "_sibling"`) must not -- ground truth is
    known by construction (the two are built to be structurally distinct
    directories), not derived from `classify_origin` itself.

    Containment here is `_contiguous_subsequence`, not a strict path
    prefix, so `(*root, base)` must not be reconstructible anywhere ELSE
    in the sibling's parent path either (e.g. a `rel` segment coincidentally
    replaying `root`'s own tail) -- `base`/`rel` are kept disjoint from
    `root` (and from each other) so the only place the pattern can appear
    is the deliberately-broken one (`base + "_sibling"` where `base` would
    need to be)."""
    assume(base not in root)
    assume(all(r not in root and r != base for r in rel))
    public_dir = _abspath((*root, base))
    nested_header = _abspath((*root, base, *rel, "api.h"))
    sibling_header = _abspath((*root, f"{base}_sibling", *rel, "api.h"))

    assert _classify(nested_header, [public_dir]) is ScopeOrigin.PUBLIC_HEADER
    assert _classify(sibling_header, [public_dir]) is not ScopeOrigin.PUBLIC_HEADER


def _naive_string_prefix_is_public(header: str, public_dir: str) -> bool:
    """The known-bad mutant this property is designed to kill: matching by
    raw string prefix instead of a real path-segment boundary. Wrongly
    accepts a sibling directory sharing a prefix substring."""
    return header.startswith(public_dir)


@given(root=_SEGMENT_LIST, base=_SEGMENT)
@settings(max_examples=100)
def test_suite_kills_the_naive_string_prefix_mutant(root: list[str], base: str) -> None:
    assume(base not in root)
    public_dir = _abspath((*root, base))
    sibling_header = _abspath((*root, f"{base}_sibling", "api.h"))

    assert _naive_string_prefix_is_public(sibling_header, public_dir) is True
    assert _classify(sibling_header, [public_dir]) is not ScopeOrigin.PUBLIC_HEADER


# --------------------------------------------------------------------------
# Priority order: public > generated > system > private (classify_origin's
# own documented check sequence), for a header constructed to match more
# than one category at once.
# --------------------------------------------------------------------------


@given(root=_SEGMENT_LIST, pub_rel=_SEGMENT_LIST)
@settings(max_examples=100)
def test_public_match_outranks_a_generated_looking_basename(
    root: list[str], pub_rel: list[str]
) -> None:
    """A header both under a public directory AND named like a generated
    file (`moc_widget.h`, Qt's meta-object compiler convention) still
    classifies PUBLIC_HEADER -- the public-header-set match is checked
    first."""
    public_dir = _abspath((*root, *pub_rel))
    header = _abspath((*root, *pub_rel, "moc_widget.h"))
    assert _classify(header, [public_dir]) is ScopeOrigin.PUBLIC_HEADER


@given(root=_SEGMENT_LIST, pub_rel=_SEGMENT_LIST)
@settings(max_examples=100)
def test_public_match_outranks_a_system_looking_directory(
    root: list[str], pub_rel: list[str]
) -> None:
    """A header both under a public directory AND under a structurally
    system-shaped path (contains a literal ``usr/include`` segment pair)
    still classifies PUBLIC_HEADER."""
    public_dir = _abspath((*root, *pub_rel))
    header = _abspath((*root, *pub_rel, "usr", "include", "api.h"))
    assert _classify(header, [public_dir]) is ScopeOrigin.PUBLIC_HEADER


@given(root=_SEGMENT_LIST)
@settings(max_examples=100)
def test_generated_basename_outranks_system_directory_when_not_public(
    root: list[str],
) -> None:
    """A header that is NOT under any public directory, but is both
    generated-shaped and system-directory-shaped, classifies GENERATED --
    `classify_origin` checks the generated heuristic before the system
    one.

    `have_public_set` must still be True here -- `classify_origin` returns
    UNKNOWN unconditionally when no public-header set was ever supplied at
    all (D4, opt-in classification), which would make this priority-order
    property vacuously true rather than actually exercising the
    generated-vs-system ordering. A UUID-rooted dummy public directory
    opts classification in without ever matching this header."""
    unrelated_dir = _abspath((str(uuid.uuid4()), str(uuid.uuid4())))
    header = _abspath((*root, "usr", "include", "moc_widget.h"))
    assert _classify(header, [unrelated_dir]) is ScopeOrigin.GENERATED


# --------------------------------------------------------------------------
# Duplicate basenames across trees: a DOCUMENTED, accepted trade-off
# (`_matches_public`'s own docstring, decision D3) -- pinned explicitly so
# it is neither silently widened nor silently removed.
# --------------------------------------------------------------------------


@given(root=_SEGMENT_LIST, sub_a=_SEGMENT, sub_b=_SEGMENT, filename=_SEGMENT)
@settings(max_examples=100)
def test_duplicate_basename_across_unrelated_trees_is_a_documented_accepted_match(
    root: list[str], sub_a: str, sub_b: str, filename: str
) -> None:
    assume(sub_a != sub_b)
    public_header_file = _abspath((*root, sub_a, f"{filename}.h"))
    unrelated_header_same_basename = _abspath((*root, sub_b, f"{filename}.h"))

    header_segs, dir_segs, have_set = build_public_set([public_header_file], [])
    origin = classify_origin(
        unrelated_header_same_basename, header_segs, dir_segs, have_public_set=have_set
    )
    # This is the accepted false-positive risk `_matches_public`'s own
    # docstring names, not a bug -- pinned so a future change cannot
    # silently narrow OR widen it without this test noticing.
    assert origin is ScopeOrigin.PUBLIC_HEADER
