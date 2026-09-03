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
from abicheck.provenance import _GENERATED_BASENAME, build_public_set, classify_origin

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
).filter(
    lambda s: (
        s.lower() not in _RESERVED_TOKENS
        and s not in (".", "..")
        # A segment used as a filename (`f"{s}.h"`) must not itself look
        # generated (a `moc_`/`ui_`/`_generated` shape under this alphabet) --
        # several properties below assert PRIVATE_HEADER unconditionally for a
        # header built from this strategy, and classify_origin checks the
        # generated heuristic before falling through to PRIVATE_HEADER, so an
        # unfiltered Hypothesis-generated "moc_1"/"ui_x"/"x_generated" would
        # make that assertion seed-dependent instead of a real invariant
        # (Codex review, PR #894).
        and not _GENERATED_BASENAME.match(f"{s}.h")
    )
)
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
    """The #843 path-taint bug generalized one layer up, and stated the way
    ``provenance.py``'s own module docstring actually motivates it: "Source
    locations recorded by the DWARF/castxml parsers are frequently absolute
    *build* paths... that bear no resemblance to the paths the user passes
    on the command line." So the public directory is described WITHOUT
    ``root`` at all (a bare relative path, as a real ``-H``/
    ``--public-header-dir`` argument commonly is) while only the header's
    own recorded absolute path relocates between ``root_a``/``root_b`` --
    the header's build-machine root and the user's own public-header
    description are independent inputs, not the same string relocated in
    lockstep.

    An earlier revision built both the public directory AND the header
    under the identical ``root`` for a given call, which a checkout-tainted
    implementation requiring an exact rooted-prefix match could still pass
    (both sides always moved together), missing exactly the decoupled-root
    scenario the docstring describes (Codex review, PR #894, fresh
    evidence: verified empirically that `classify_origin` already handles
    a bare relative public directory matching a header recorded under a
    wholly different absolute root, which the coupled-root construction
    could never exercise).

    Both an inside-public-dir and an OUTSIDE-public-dir header are checked,
    each against its own expected ``ScopeOrigin`` (not just cross-root
    equality). The outside-public-dir header lives under a UUID-rooted
    sibling disjoint from ``root``/``pub_rel``/``header_rel``, so it can
    never accidentally land under the public directory via
    `_contiguous_subsequence` containment."""
    assume(root_a != root_b)
    # pub_rel must be disjoint from every root/header_rel segment: since
    # public_dir no longer includes root at all, a coincidentally-shared
    # segment could make `_contiguous_subsequence` match pub_rel inside the
    # PRIVATE header's own tail purely by chance, unrelated to the actual
    # public/private structure under test.
    assume(all(p not in root_a and p not in root_b for p in pub_rel))
    assume(all(p not in header_rel for p in pub_rel))
    public_dir = _abspath(pub_rel)  # root-independent: a bare relative path
    public_tail = (*pub_rel, *header_rel, f"{filename}.h")
    private_sibling = (str(uuid.uuid4()), str(uuid.uuid4()))
    private_tail = (*private_sibling, f"{filename}.h")

    def classify_under(root: list[str], header_tail: tuple[str, ...]) -> ScopeOrigin:
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
    actually reach the classification layer, not just its own unit.

    The detour is spliced INSIDE `pub_rel` itself (before its last segment),
    not merely appended after an already-complete match -- otherwise a
    broken/no-op `..`-collapse would leave `root + pub_rel` intact as a
    contiguous prefix regardless, since a bare `.` segment is already
    dropped by plain path-component splitting, and the noise trailing
    afterward wouldn't break the containment match either way. Splicing it
    inside `pub_rel` means an incorrect collapse genuinely breaks the
    directory-containment match (a different `ScopeOrigin`, not just a
    coincidentally-equal one), which is what this metamorphic property is
    actually meant to catch. Both a public and a private header are
    checked, matching the sibling additive-directory property's fix for
    the identical reason -- a spliced-detour private header (a UUID-rooted
    sibling) confirms the noise doesn't itself spuriously introduce a
    match."""
    public_dir = _abspath((*root, *pub_rel))
    plain_public_header = _abspath((*root, *pub_rel, *header_rel, f"{filename}.h"))
    noisy_public_header = "/" + "/".join(
        (
            *root,
            ".",
            *pub_rel[:-1],
            "detour",
            "..",
            pub_rel[-1],
            *header_rel,
            f"{filename}.h",
        )
    )
    assert _classify(plain_public_header, [public_dir]) is ScopeOrigin.PUBLIC_HEADER
    assert _classify(noisy_public_header, [public_dir]) is ScopeOrigin.PUBLIC_HEADER

    private_sibling = (str(uuid.uuid4()), str(uuid.uuid4()))
    plain_private_header = _abspath((*root, *private_sibling, f"{filename}.h"))
    noisy_private_header = "/" + "/".join(
        (
            *root,
            ".",
            private_sibling[0],
            "detour",
            "..",
            private_sibling[1],
            f"{filename}.h",
        )
    )
    assert _classify(plain_private_header, [public_dir]) is ScopeOrigin.PRIVATE_HEADER
    assert _classify(noisy_private_header, [public_dir]) is ScopeOrigin.PRIVATE_HEADER


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
    generated header path, so no spurious match is possible either.

    Both a public and a PRIVATE header are checked, each against its own
    expected ``ScopeOrigin`` before and after the addition -- the public
    header alone can't distinguish this property from an implementation
    that spuriously matches unrelated directories, since it already
    classifies PUBLIC_HEADER either way and `_matches_public` is additive
    (a regression could only ever flip a PRIVATE_HEADER classification to a
    spurious PUBLIC_HEADER one, never the reverse)."""
    public_dir = _abspath((*root, *pub_rel))
    public_header = _abspath((*root, *pub_rel, *header_rel, f"{filename}.h"))
    private_sibling = (str(uuid.uuid4()), str(uuid.uuid4()))
    private_header = _abspath((*root, *private_sibling, f"{filename}.h"))
    unrelated_dir = _abspath((str(uuid.uuid4()), str(uuid.uuid4())))

    assert _classify(public_header, [public_dir]) is ScopeOrigin.PUBLIC_HEADER
    assert _classify(private_header, [public_dir]) is ScopeOrigin.PRIVATE_HEADER

    after_public = _classify(public_header, [public_dir, unrelated_dir])
    after_private = _classify(private_header, [public_dir, unrelated_dir])
    assert after_public is ScopeOrigin.PUBLIC_HEADER
    assert after_private is ScopeOrigin.PRIVATE_HEADER


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
