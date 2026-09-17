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

"""The release tag a baseline is published *as* is not the revision it captured.

``publish-baseline.yml``'s pre-captured path compared a baseline-set's own
``project_ref`` against the *release tag* it was being published under. That
is only correct for a set this workflow captured itself, because its own
capture step stamps the tag into the manifest. A set captured by somebody
else records what it actually built -- a commit SHA -- so the comparison
rejected every genuine cross-run capture, and a downstream project closed
the gap by reimplementing tag resolution in shell next to its own publisher.

This module is the one owner of that resolution. It decides, from GitHub's
own ``git/ref`` and ``git/tags`` documents, which commit a publication tag
names, and separately what a caller has declared the captured revision
should be. It performs no I/O and holds no credentials: the caller fetches
the documents, this decides what they mean, which is what makes every rule
below testable against a fixture instead of only against a live tag.

Three things it deliberately refuses rather than works around:

* **A near-miss ref.** ``GET /repos/{repo}/git/ref/tags/{tag}`` answers with
  an *array* when the name it was given is a prefix of several real tags, so
  asking for ``1.5`` can come back describing ``1.5.2``. The exact
  ``refs/tags/{tag}`` entry is selected, and its absence is
  ``tag-not-found`` -- never "the first one".
* **A branch of the same name.** A branch lives at ``refs/heads/``, so the
  tags endpoint cannot return one; the returned ``ref`` is nevertheless
  asserted to be exactly ``refs/tags/{tag}``, because the check that costs
  nothing is the one that survives an endpoint changing shape.
* **A tag that does not peel to a commit.** An annotated tag is a real
  object whose own SHA is *not* the commit; it is peeled through
  ``git/tags``. A tag object pointing at a tree, a blob, or another tag is
  refused instead of being followed, because publishing an immutable
  baseline against an unbounded peel chain is not a decision a workflow
  gets to make silently.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "CAPTURE_REVISION_MODES",
    "ResolvedTag",
    "TagResolutionError",
    "expected_capture_revision",
    "resolve_tag_commit",
    "tag_object_to_fetch",
]

#: A full git object name, in either hash algorithm GitHub serves. Nothing
#: here ever accepts an abbreviation: every value this module produces is
#: compared for equality against a manifest's recorded revision, and a
#: prefix is ambiguous by construction.
_FULL_SHA = re.compile(r"^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$")

#: What ``expected-project-ref`` may say, beyond a literal SHA. Exported so
#: a front end can state the supported set in an error rather than keeping
#: its own copy of it.
CAPTURE_REVISION_MODES = ("commit", "tag")


class TagResolutionError(Exception):
    """A publication tag could not be resolved to a revision to expect.

    Carries a machine-readable *code* for the same reason
    :class:`~abicheck.frontends.action.run_selection.SourceRunRejected`
    does: a negative-control test that accepts any failure passes against an
    implementation that fails at everything.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ResolvedTag:
    """A publication tag and the commit it actually names.

    ``tag`` and ``commit_sha`` are kept as separate fields on purpose: they
    are the two halves whose conflation is the defect this module exists to
    close, and a caller that wants one must say which.
    """

    tag: str
    ref: str
    commit_sha: str
    annotated: bool


def _entries(document: Any) -> Sequence[Mapping[str, Any]]:
    """The ref documents in an API answer, whichever shape it came in.

    A single ref is an object; a prefix match is an array of them. Both are
    normalised here so the selection rule below is stated once.
    """
    if isinstance(document, Mapping):
        return [document]
    if isinstance(document, list):
        return [item for item in document if isinstance(item, Mapping)]
    raise TagResolutionError(
        "tag-ref-unreadable",
        f"the git ref document is neither an object nor an array "
        f"(got {type(document).__name__})",
    )


def _object_of(entry: Mapping[str, Any], *, what: str) -> tuple[str, str]:
    obj = entry.get("object")
    if not isinstance(obj, Mapping):
        raise TagResolutionError(
            "tag-ref-unreadable", f"the {what} document records no 'object'"
        )
    sha = str(obj.get("sha", "") or "")
    obj_type = str(obj.get("type", "") or "")
    if not _FULL_SHA.match(sha):
        raise TagResolutionError(
            "tag-ref-unreadable",
            f"the {what} document records {sha!r} as its object sha, which is "
            "not a full object name",
        )
    return obj_type, sha


def tag_object_to_fetch(tag: str, ref_document: Any) -> str:
    """The annotated-tag object to peel, or ``""`` for a lightweight tag.

    Split out from :func:`resolve_tag_commit` so the caller can make the
    *second* API request only when there is one to make, while the decision
    about whether there is one stays here rather than becoming a ``jq``
    expression in a workflow.
    """
    obj_type, sha = _object_of(_select_ref(tag, ref_document), what="git ref")
    return sha if obj_type == "tag" else ""


def _select_ref(tag: str, ref_document: Any) -> Mapping[str, Any]:
    if not tag:
        raise TagResolutionError("tag-missing", "no publication tag was given")
    wanted = f"refs/tags/{tag}"
    entries = _entries(ref_document)
    exact = [entry for entry in entries if str(entry.get("ref", "")) == wanted]
    if not exact:
        seen = sorted(str(entry.get("ref", "")) for entry in entries)
        raise TagResolutionError(
            "tag-not-found",
            f"no ref named {wanted} exists; the API answered with "
            + (f"{', '.join(seen)}" if seen else "nothing")
            + " -- a prefix match on a longer tag name is not the tag that "
            "was asked for, and a branch of the same name is not a tag at all",
        )
    if len(exact) > 1:
        raise TagResolutionError(
            "tag-ambiguous",
            f"the API returned {len(exact)} entries all named {wanted}",
        )
    return exact[0]


def resolve_tag_commit(
    tag: str,
    ref_document: Any,
    *,
    tag_object: Any = None,
) -> ResolvedTag:
    """The commit *tag* names, peeling an annotated tag through *tag_object*.

    *ref_document* is ``GET /repos/{repo}/git/ref/tags/{tag}``'s body and
    *tag_object*, when the ref describes an annotated tag, is
    ``GET /repos/{repo}/git/tags/{sha}``'s. Use :func:`tag_object_to_fetch`
    to learn whether that second request is needed; omitting it for an
    annotated tag is refused rather than treated as a lightweight tag, since
    an annotated tag's own object SHA is a perfectly well-formed hex string
    that simply is not the commit anyone captured.
    """
    entry = _select_ref(tag, ref_document)
    ref = str(entry.get("ref", ""))
    obj_type, sha = _object_of(entry, what="git ref")

    if obj_type == "commit":
        return ResolvedTag(tag=tag, ref=ref, commit_sha=sha, annotated=False)
    if obj_type != "tag":
        raise TagResolutionError(
            "tag-not-a-commit",
            f"{ref} points at a {obj_type or 'unknown'} object, not a commit "
            "or an annotated tag; a baseline cannot be published against it",
        )

    if tag_object is None:
        raise TagResolutionError(
            "tag-object-missing",
            f"{ref} is an annotated tag whose own object is {sha}; that is "
            "the tag object, not the commit it points at, so the "
            "git/tags document is required to peel it",
        )
    if not isinstance(tag_object, Mapping):
        raise TagResolutionError(
            "tag-ref-unreadable",
            f"the git tag document is not an object (got {type(tag_object).__name__})",
        )
    # The peel has to be of the object the ref named. Without this, a caller
    # that fetched the wrong tag object -- or was answered with one -- peels
    # to a commit that has nothing to do with `tag`, and every check
    # downstream then agrees with itself about the wrong revision.
    peeled_from = str(tag_object.get("sha", "") or "")
    if peeled_from and peeled_from != sha:
        raise TagResolutionError(
            "tag-object-mismatch",
            f"{ref} names tag object {sha}, but the supplied git/tags "
            f"document describes {peeled_from}",
        )
    target_type, target_sha = _object_of(tag_object, what="git tag")
    if target_type != "commit":
        raise TagResolutionError(
            "tag-object-not-a-commit",
            f"the annotated tag {ref} points at a {target_type or 'unknown'} "
            "object rather than a commit; chained tag objects are refused "
            "rather than peeled further, because an unbounded peel is not a "
            "decision a publisher makes silently",
        )
    return ResolvedTag(tag=tag, ref=ref, commit_sha=target_sha, annotated=True)


def expected_capture_revision(
    declared: str,
    *,
    tag: str,
    resolved: ResolvedTag | None,
) -> str:
    """The revision a pre-captured set's ``project_ref`` must equal.

    *declared* is the caller's ``expected-project-ref`` input:

    ``""`` or ``"commit"``
        the commit *tag* resolves to -- the default, and the only answer
        that is correct for a set somebody else captured, because a capture
        records the revision it built.
    ``"tag"``
        the literal tag string. This is what ``publish-baseline.yml``'s own
        capture path stamps into the manifests it writes, so re-publishing
        one of those through the pre-captured path needs it. Supported
        explicitly and named in the input, never reached by falling back
        from a failed commit comparison: a fallback would accept a set whose
        recorded revision matched neither, whenever the tag happened to be
        the string it recorded.
    a full commit SHA
        exactly that, for a caller that already knows the revision and does
        not want the answer to depend on a tag that could be moved between
        the capture and the publication.

    Anything else is refused. The set of supported values is small and
    closed on purpose -- a misspelling that silently means "commit" would
    turn an intended legacy publication into a rejected one, or worse.
    """
    value = declared.strip()
    if value in ("", "commit"):
        if resolved is None:
            raise TagResolutionError(
                "tag-unresolved",
                f"the captured revision is expected to be the commit {tag!r} "
                "names, but the tag was not resolved",
            )
        return resolved.commit_sha
    if value == "tag":
        if not tag:
            raise TagResolutionError(
                "tag-missing",
                "expected-project-ref is 'tag', but no publication tag was given",
            )
        return tag
    if _FULL_SHA.match(value):
        return value
    raise TagResolutionError(
        "expected-project-ref-invalid",
        f"expected-project-ref must be one of {', '.join(CAPTURE_REVISION_MODES)} "
        f"or a full 40/64-character commit SHA (got {value!r})",
    )
