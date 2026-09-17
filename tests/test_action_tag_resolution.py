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

"""``tag_resolution``: a publication tag is not the revision that was captured.

**Bug class:** ``identity.two_distinct_identifiers_compared_as_one`` -- a
release tag (``1.5.2``) and the commit a capture recorded (a 40-hex SHA) are
different identifiers for different things, and the pre-captured publication
path compared one against the other. The reported symptom was one rejected
publication; the class is every place a name and the object it names are
folded into a single equality.

So the tests below are written against the *primitive's* contract rather
than against that one repro (root ``AGENTS.md``, "Primitive-level property
tests"). The oracle is an independent restatement of what each API document
means -- a lightweight ref's object *is* the commit, an annotated ref's
object is a tag object whose own target is the commit -- deliberately not a
second call into the implementation. The sibling cases are generated:
``TestResolutionIsDrivenByTheObjectType`` sweeps every object type GitHub can
put in a ref document rather than naming the two that work, so a type added
to the accepted set later cannot slip past by not having been thought of.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.frontends.action.tag_resolution import (
    CAPTURE_REVISION_MODES,
    ResolvedTag,
    TagResolutionError,
    expected_capture_revision,
    resolve_tag_commit,
    tag_object_to_fetch,
)

COMMIT = "a" * 40
OTHER_COMMIT = "b" * 40
TAG_OBJECT = "c" * 40
SHA256_COMMIT = "d" * 64


def lightweight(tag: str, commit: str = COMMIT) -> dict[str, object]:
    """``git/ref/tags/<tag>`` for a tag that points straight at a commit."""
    return {"ref": f"refs/tags/{tag}", "object": {"type": "commit", "sha": commit}}


def annotated(tag: str, obj: str = TAG_OBJECT) -> dict[str, object]:
    """``git/ref/tags/<tag>`` for a tag whose object is a tag object."""
    return {"ref": f"refs/tags/{tag}", "object": {"type": "tag", "sha": obj}}


def tag_object(target: str = COMMIT, obj: str = TAG_OBJECT) -> dict[str, object]:
    """``git/tags/<sha>`` -- the annotated tag object itself."""
    return {"sha": obj, "object": {"type": "commit", "sha": target}}


def _code(excinfo: pytest.ExceptionInfo[TagResolutionError]) -> str:
    return excinfo.value.code


# ---------------------------------------------------------------------------
# The two shapes that must work, and the rule that decides between them
# ---------------------------------------------------------------------------


class TestBothTagShapesResolveToTheirCommit:
    """A numeric release tag works whether it is lightweight or annotated.

    Both are listed in the acceptance table because a project that tags
    releases with ``git tag -a`` and one that uses ``git tag`` produce
    documents of different shapes for the same user-visible tag name, and a
    resolver that handles only the shape its author happened to test
    rejects half of its users with ``tag-object-missing``.
    """

    @pytest.mark.parametrize("tag", ["1.5.2", "v1.5.2", "1.5", "release-2026.01"])
    def test_lightweight(self, tag: str) -> None:
        resolved = resolve_tag_commit(tag, lightweight(tag))
        assert resolved == ResolvedTag(
            tag=tag, ref=f"refs/tags/{tag}", commit_sha=COMMIT, annotated=False
        )

    @pytest.mark.parametrize("tag", ["1.5.2", "v1.5.2", "1.5", "release-2026.01"])
    def test_annotated(self, tag: str) -> None:
        resolved = resolve_tag_commit(
            tag, annotated(tag), tag_object=tag_object(target=COMMIT)
        )
        assert resolved == ResolvedTag(
            tag=tag, ref=f"refs/tags/{tag}", commit_sha=COMMIT, annotated=True
        )

    def test_the_annotated_tags_own_object_is_never_the_answer(self) -> None:
        # The whole reason annotated tags need a second request: the ref's
        # own `object.sha` is a well-formed 40-hex string that a resolver
        # taking the shortcut would happily publish against.
        resolved = resolve_tag_commit(
            "1.5.2", annotated("1.5.2"), tag_object=tag_object(target=COMMIT)
        )
        assert resolved.commit_sha == COMMIT
        assert resolved.commit_sha != TAG_OBJECT

    def test_a_sha256_commit_resolves(self) -> None:
        resolved = resolve_tag_commit("1.5.2", lightweight("1.5.2", SHA256_COMMIT))
        assert resolved.commit_sha == SHA256_COMMIT


class TestTheSecondRequestIsMadeExactlyWhenItIsNeeded:
    """``tag_object_to_fetch`` and ``resolve_tag_commit`` must agree.

    They are two entry points onto one rule -- "is this ref annotated?" --
    and the caller interleaves an API request between them. If they could
    disagree, the caller would either fetch nothing for a tag that needs
    peeling (``tag-object-missing``) or fetch a tag object for a commit ref
    (a 404 mid-publication). Asserting the agreement, rather than each
    answer separately, is what makes that impossible.
    """

    @pytest.mark.parametrize(
        ("document", "expected"),
        [
            (lightweight("1.5.2"), ""),
            (annotated("1.5.2"), TAG_OBJECT),
        ],
    )
    def test_it_names_the_object_to_peel(
        self, document: dict[str, object], expected: str
    ) -> None:
        assert tag_object_to_fetch("1.5.2", document) == expected

    @pytest.mark.parametrize("document", [lightweight("1.5.2"), annotated("1.5.2")])
    def test_resolution_succeeds_exactly_when_the_caller_followed_it(
        self, document: dict[str, object]
    ) -> None:
        to_fetch = tag_object_to_fetch("1.5.2", document)
        resolved = resolve_tag_commit(
            "1.5.2",
            document,
            tag_object=tag_object(obj=to_fetch) if to_fetch else None,
        )
        assert resolved.commit_sha == COMMIT
        assert resolved.annotated is bool(to_fetch)


class TestResolutionIsDrivenByTheObjectType:
    """Every object type a ref can carry, not only the two that work.

    A hand-written pair of tests for ``commit`` and ``tag`` says nothing
    about ``tree``, ``blob``, or a type GitHub adds later -- and "resolve
    whatever is there" is exactly how a baseline gets published against a
    tree. The small domain is enumerated rather than sampled.
    """

    ACCEPTED = {"commit", "tag"}
    ALL_TYPES = ("commit", "tag", "tree", "blob", "", "Commit", "unknown")

    @pytest.mark.parametrize("object_type", ALL_TYPES)
    def test_only_commit_and_tag_are_accepted(self, object_type: str) -> None:
        document = {
            "ref": "refs/tags/1.5.2",
            "object": {"type": object_type, "sha": COMMIT},
        }
        if object_type in self.ACCEPTED:
            resolve_tag_commit("1.5.2", document, tag_object=tag_object(obj=COMMIT))
            return
        with pytest.raises(TagResolutionError) as excinfo:
            resolve_tag_commit("1.5.2", document, tag_object=tag_object(obj=COMMIT))
        assert _code(excinfo) == "tag-not-a-commit"

    @pytest.mark.parametrize("target_type", ALL_TYPES)
    def test_an_annotated_tag_must_peel_to_a_commit(self, target_type: str) -> None:
        obj = {"sha": TAG_OBJECT, "object": {"type": target_type, "sha": COMMIT}}
        if target_type == "commit":
            assert (
                resolve_tag_commit(
                    "1.5.2", annotated("1.5.2"), tag_object=obj
                ).commit_sha
                == COMMIT
            )
            return
        with pytest.raises(TagResolutionError) as excinfo:
            resolve_tag_commit("1.5.2", annotated("1.5.2"), tag_object=obj)
        # A tag-of-a-tag is refused rather than peeled again: an unbounded
        # peel chain is not something a publisher decides silently.
        assert _code(excinfo) == "tag-object-not-a-commit"


# ---------------------------------------------------------------------------
# Selecting the *right* ref out of the answer
# ---------------------------------------------------------------------------


class TestOnlyTheExactRefIsAccepted:
    """The near-miss array is the hazard this selection exists for.

    ``git/ref/tags/1.5`` answers with an array describing ``1.5.2``,
    ``1.5.3`` and so on when no exact ``1.5`` exists. Taking the first entry
    publishes ``1.5``'s baseline against ``1.5.2``'s commit -- a wrong
    answer that looks entirely well-formed.
    """

    NEAR_MISSES = ("1.5.2", "1.5.3", "1.5.10", "1.5-rc1")

    def test_an_exact_entry_is_selected_out_of_a_prefix_match(self) -> None:
        answer = [lightweight("1.5", COMMIT)] + [
            lightweight(name, OTHER_COMMIT) for name in self.NEAR_MISSES
        ]
        assert resolve_tag_commit("1.5", answer).commit_sha == COMMIT

    @pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
    def test_selection_does_not_depend_on_answer_order(
        self, order: tuple[int, ...]
    ) -> None:
        entries = [
            lightweight("1.5.2", OTHER_COMMIT),
            lightweight("1.5", COMMIT),
            lightweight("1.5.10", OTHER_COMMIT),
        ]
        answer = [entries[index] for index in order]
        assert resolve_tag_commit("1.5", answer).commit_sha == COMMIT

    def test_a_prefix_match_with_no_exact_entry_is_refused(self) -> None:
        answer = [lightweight(name, OTHER_COMMIT) for name in self.NEAR_MISSES]
        with pytest.raises(TagResolutionError) as excinfo:
            resolve_tag_commit("1.5", answer)
        assert _code(excinfo) == "tag-not-found"

    @pytest.mark.parametrize(
        "ref",
        [
            "refs/heads/1.5.2",
            "refs/tags/1.5.2x",
            "refs/tags/v1.5.2",
            "refs/remotes/origin/1.5.2",
            "1.5.2",
            "",
        ],
    )
    def test_anything_that_is_not_refs_tags_of_this_tag_is_refused(
        self, ref: str
    ) -> None:
        document = {"ref": ref, "object": {"type": "commit", "sha": COMMIT}}
        with pytest.raises(TagResolutionError) as excinfo:
            resolve_tag_commit("1.5.2", document)
        assert _code(excinfo) == "tag-not-found"

    def test_a_branch_of_the_same_name_is_not_the_tag(self) -> None:
        # The endpoint cannot return one, which is precisely why the
        # assertion is cheap and stays: it survives the endpoint changing.
        answer = [
            {"ref": "refs/heads/1.5.2", "object": {"type": "commit", "sha": COMMIT}}
        ]
        with pytest.raises(TagResolutionError) as excinfo:
            resolve_tag_commit("1.5.2", answer)
        assert _code(excinfo) == "tag-not-found"


class TestMalformedDocumentsAreRefusedNotGuessed:
    @pytest.mark.parametrize("document", ["a string", 17, None, True])
    def test_a_non_object_non_array_answer(self, document: object) -> None:
        with pytest.raises(TagResolutionError) as excinfo:
            resolve_tag_commit("1.5.2", document)
        assert _code(excinfo) == "tag-ref-unreadable"

    @pytest.mark.parametrize(
        "sha", ["", "abc1234", "a" * 39, "a" * 41, "z" * 40, "  " + "a" * 40]
    )
    def test_an_object_sha_that_is_not_a_full_object_name(self, sha: str) -> None:
        document = {"ref": "refs/tags/1.5.2", "object": {"type": "commit", "sha": sha}}
        with pytest.raises(TagResolutionError) as excinfo:
            resolve_tag_commit("1.5.2", document)
        assert _code(excinfo) == "tag-ref-unreadable"

    def test_a_ref_with_no_object(self) -> None:
        with pytest.raises(TagResolutionError) as excinfo:
            resolve_tag_commit("1.5.2", {"ref": "refs/tags/1.5.2"})
        assert _code(excinfo) == "tag-ref-unreadable"

    def test_an_annotated_ref_with_no_tag_object_supplied(self) -> None:
        with pytest.raises(TagResolutionError) as excinfo:
            resolve_tag_commit("1.5.2", annotated("1.5.2"))
        assert _code(excinfo) == "tag-object-missing"

    def test_a_tag_object_describing_a_different_tag(self) -> None:
        # The peel has to be of the object the ref named; otherwise a
        # mis-fetched document resolves to a commit belonging to some other
        # tag, and every check downstream agrees about the wrong revision.
        with pytest.raises(TagResolutionError) as excinfo:
            resolve_tag_commit(
                "1.5.2",
                annotated("1.5.2"),
                tag_object=tag_object(target=OTHER_COMMIT, obj=OTHER_COMMIT),
            )
        assert _code(excinfo) == "tag-object-mismatch"

    def test_an_empty_tag_name(self) -> None:
        with pytest.raises(TagResolutionError) as excinfo:
            resolve_tag_commit("", lightweight(""))
        assert _code(excinfo) == "tag-missing"


# ---------------------------------------------------------------------------
# What a captured set's project_ref is expected to be
# ---------------------------------------------------------------------------


class TestExpectedCaptureRevision:
    """The declared expectation, which is a separate decision from the peel.

    ``commit`` and ``tag`` answer two genuinely different questions -- "what
    revision did somebody else's capture build?" and "what string does this
    workflow's own capture step stamp?" -- and the whole point of keeping
    them apart is that neither is ever reached by falling back from the
    other.
    """

    RESOLVED = ResolvedTag(
        tag="1.5.2", ref="refs/tags/1.5.2", commit_sha=COMMIT, annotated=False
    )

    @pytest.mark.parametrize("declared", ["", "commit", " commit ", "\tcommit\n"])
    def test_the_default_is_the_commit(self, declared: str) -> None:
        assert (
            expected_capture_revision(declared, tag="1.5.2", resolved=self.RESOLVED)
            == COMMIT
        )

    def test_the_legacy_mode_is_the_literal_tag(self) -> None:
        assert (
            expected_capture_revision("tag", tag="1.5.2", resolved=self.RESOLVED)
            == "1.5.2"
        )

    @pytest.mark.parametrize("literal", [OTHER_COMMIT, SHA256_COMMIT, COMMIT.upper()])
    def test_an_explicit_sha_is_taken_verbatim(self, literal: str) -> None:
        assert (
            expected_capture_revision(literal, tag="1.5.2", resolved=self.RESOLVED)
            == literal
        )

    @pytest.mark.parametrize(
        "declared",
        [
            "Commit",
            "COMMIT",
            "tags",
            "Tag",
            "sha",
            "head",
            "refs/tags/1.5.2",
            "abc1234",
            "a" * 41,
            "commit ref",
        ],
    )
    def test_anything_outside_the_closed_set_is_refused(self, declared: str) -> None:
        # A misspelling that silently meant "commit" would turn an intended
        # legacy publication into a rejected one, and a misspelling that
        # silently meant "tag" would accept a set recording no commit at all.
        with pytest.raises(TagResolutionError) as excinfo:
            expected_capture_revision(declared, tag="1.5.2", resolved=self.RESOLVED)
        assert _code(excinfo) == "expected-project-ref-invalid"

    def test_the_commit_mode_refuses_an_unresolved_tag_rather_than_falling_back(
        self,
    ) -> None:
        # The fallback this forecloses: "no commit, so expect the tag".
        # That accepts a set recording the tag string whenever resolution
        # failed, which is the original defect wearing a different hat.
        with pytest.raises(TagResolutionError) as excinfo:
            expected_capture_revision("", tag="1.5.2", resolved=None)
        assert _code(excinfo) == "tag-unresolved"

    def test_the_two_modes_never_produce_the_same_answer_for_a_real_tag(self) -> None:
        # The oracle for the whole module, stated once: a tag name is not a
        # commit name, so the two supported modes are genuinely distinct
        # expectations and a caller must choose.
        for mode in CAPTURE_REVISION_MODES:
            assert expected_capture_revision(
                mode, tag="1.5.2", resolved=self.RESOLVED
            ) in (COMMIT, "1.5.2")
        assert expected_capture_revision(
            "commit", tag="1.5.2", resolved=self.RESOLVED
        ) != expected_capture_revision("tag", tag="1.5.2", resolved=self.RESOLVED)


class TestThereIsOnlyOnePeelingOwner:
    """A second module that peels tags will eventually peel differently.

    This is not hypothetical here: ``baseline_source.resolve_tag`` already
    existed and was the *looser* of the two -- it accepted an object of any
    non-``tag`` type as the commit, so a ref pointing at a tree resolved as a
    perfectly good release baseline. It now delegates, which is what makes
    that a closed bug rather than a second opinion.

    The assertion is over behaviour rather than over an import, because
    "delegates" is satisfiable by importing and then ignoring.
    """

    @pytest.mark.parametrize(
        ("document", "why"),
        [
            (
                {"ref": "refs/tags/1.5.2", "object": {"type": "tree", "sha": COMMIT}},
                "a tree is not a commit",
            ),
            (
                {
                    "ref": "refs/heads/1.5.2",
                    "object": {"type": "commit", "sha": COMMIT},
                },
                "a branch of the same name is not a tag",
            ),
            (
                [
                    {
                        "ref": "refs/tags/1.5.20",
                        "object": {"type": "commit", "sha": COMMIT},
                    }
                ],
                "a prefix match on a longer tag is not the tag asked for",
            ),
        ],
    )
    def test_both_entry_points_refuse_the_same_documents(
        self, document: object, why: str
    ) -> None:
        from abicheck.frontends.action.baseline_source import resolve_tag

        with pytest.raises(TagResolutionError):
            resolve_tag_commit("1.5.2", document)
        assert not resolve_tag("1.5.2", document).ok, why

    @pytest.mark.parametrize("kind", ["lightweight", "annotated"])
    def test_both_entry_points_reach_the_same_commit(self, kind: str) -> None:
        from abicheck.frontends.action.baseline_source import resolve_tag

        if kind == "lightweight":
            document, peel = lightweight("1.5.2"), None
        else:
            document, peel = annotated("1.5.2"), tag_object()
        strict = resolve_tag_commit("1.5.2", document, tag_object=peel)
        legacy = resolve_tag("1.5.2", document, tag_object_document=peel)
        assert legacy.ok
        assert legacy.commit_sha == strict.commit_sha == COMMIT
        assert legacy.annotated == strict.annotated
