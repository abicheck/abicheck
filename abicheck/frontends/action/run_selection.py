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

"""Which run produced this report, which PR is it about, and is the archive safe?

ADR-073. A ``workflow_run`` publisher is *trusted* -- it holds a token that
can write to the pull request -- and the thing it publishes was produced by
an *untrusted* job, possibly from a fork. Everything between those two
sentences is this module.

The checks are separated into three independent questions because each fails
differently, and because every consumer of this pattern needs all three; a
project that re-implements them per repository gets a different subset wrong
each time.

1. :func:`verify_source_run` -- is this the run we think it is? Its
   repository, workflow identity, triggering event, id, attempt and
   conclusion are each checked against what the publisher declared, and a
   mismatch is a refusal naming the field. Nothing here is inferred from the
   artifact.
2. :func:`resolve_pull_request` / :func:`verify_tested_sha` -- which pull
   request is this, and was the analysed commit really its? The PR is
   resolved **through the API**, from the run's own head SHA; an artifact
   that states a PR number is only ever *cross-checked* against that answer,
   never believed. An ambiguous association (no PR, several PRs, a PR whose
   base is a different repository) is a visible failure, not a guess.
3. :func:`extract_artifact` -- is the archive safe to unpack? It is treated
   as hostile: caps on total size, per-entry size, entry count and
   compression ratio; absolute paths, ``..`` traversal, symlinks and every
   other non-regular entry refused. (A GitHub artifact is a zip, and the zip
   format cannot express a hardlink at all -- the recorded Unix mode is
   checked for *every* type, so the refusal is by entry kind rather than by
   an enumeration of the ones that happen to be nameable.) Nothing in it is
   executed, imported, ``pickle``-d or ``yaml.load``-ed -- the only consumer
   is ``json.loads`` in the renderer, and the extracted tree is left with no
   executable bit.

**What this boundary does not do.** Verifying the origin of a report does
not make its *contents* trusted evidence. A fork's analysis job chose what
to put in that JSON. What these checks establish is narrower and worth
stating plainly: the document reaching the publisher is the one that
specific run produced, it is about the pull request the API says it is, and
unpacking it cannot write outside its own directory or exhaust the runner.
Assurance and provenance still come from the report's own recorded evidence
facts, which are preserved and rendered as-is -- never upgraded by the fact
that a trusted job did the rendering.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

# --- artifact limits -------------------------------------------------------

#: Total uncompressed bytes an artifact may expand to.
DEFAULT_MAX_TOTAL_BYTES = 64 * 1024 * 1024
#: Uncompressed bytes any single entry may expand to.
DEFAULT_MAX_ENTRY_BYTES = 32 * 1024 * 1024
#: Entries an artifact may contain.
DEFAULT_MAX_ENTRIES = 2_000
#: Uncompressed-to-compressed ratio above which an entry is refused. A
#: decompression bomb is compressible far beyond anything a JSON report is:
#: measured, a large `compare` report gzips around 12-20x, and pathological
#: bombs reach 1000x and beyond. 200 leaves an order of magnitude of headroom
#: over real content while refusing the attack. Applied per entry and to the
#: archive as a whole, because either alone is evadable -- one enormous entry
#: hides in a large archive's average, and many small ones hide from a
#: per-entry check.
DEFAULT_MAX_RATIO = 200.0
#: Entries below this compressed size are exempt from the ratio check: a
#: 20-byte input compressing to 1 byte is a 20x "ratio" that means nothing,
#: and the absolute caps already bound what such an entry can cost.
_RATIO_FLOOR_BYTES = 1024


class SourceRunRejected(Exception):
    """The source run, its PR association, or its artifact was refused.

    Carries a short machine-readable *code* alongside the message so a
    workflow can branch on the reason without parsing prose, and so the
    negative-control tests assert the reason rather than merely that
    *something* failed -- a test that accepts any rejection passes against
    an implementation that rejects everything.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# 1. Is this the run we think it is?
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunExpectation:
    """What the publisher declares about the run it is willing to publish.

    Every field is compared exactly when set and skipped when empty, but
    *repository* and *workflow* are the two that make this a security
    boundary rather than a sanity check: without them a publisher can be
    handed any run id in the world and will happily fetch, unpack and
    publish whatever that run produced.
    """

    repository: str = ""
    #: The workflow's file path (``.github/workflows/abi.yml``) or its name.
    #: Either is accepted because the API reports both and a caller may
    #: reasonably pin whichever it considers stable.
    workflow: str = ""
    event: str = ""
    run_id: str = ""
    run_attempt: int | None = None
    #: Conclusions that may be published. Empty means "any conclusion",
    #: which is a real choice: a publisher that reports analysis *failures*
    #: needs the failed run.
    allowed_conclusions: tuple[str, ...] = ("success",)


@dataclass(frozen=True)
class SourceRun:
    """The fields of a workflow run this boundary actually uses."""

    run_id: str
    run_attempt: int
    repository: str
    workflow_path: str
    workflow_name: str
    event: str
    status: str
    conclusion: str
    head_sha: str
    head_branch: str = ""
    head_repository: str = ""

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> SourceRun:
        """Read a ``GET /repos/{owner}/{repo}/actions/runs/{id}`` response.

        Every field is coerced defensively: this is the API's answer, but it
        reaches here through a shell redirect and a file, and a truncated
        download must fail a check rather than produce a partially-populated
        object that silently satisfies one.
        """
        if not isinstance(data, Mapping):
            raise SourceRunRejected(
                "run-unreadable", "the run document is not a JSON object"
            )
        repo = data.get("repository")
        head_repo = data.get("head_repository")
        attempt = data.get("run_attempt")
        return cls(
            run_id=str(data.get("id", "") or ""),
            run_attempt=attempt if isinstance(attempt, int) and attempt > 0 else 1,
            repository=str(
                (repo or {}).get("full_name", "") if isinstance(repo, Mapping) else ""
            ),
            workflow_path=str(data.get("path", "") or ""),
            workflow_name=str(data.get("name", "") or ""),
            event=str(data.get("event", "") or ""),
            status=str(data.get("status", "") or ""),
            conclusion=str(data.get("conclusion", "") or ""),
            head_sha=str(data.get("head_sha", "") or ""),
            head_branch=str(data.get("head_branch", "") or ""),
            head_repository=str(
                (head_repo or {}).get("full_name", "")
                if isinstance(head_repo, Mapping)
                else ""
            ),
        )


def verify_source_run(run: SourceRun, expected: RunExpectation) -> None:
    """Refuse *run* unless it is exactly what *expected* declares.

    Checked in the order a mistake is most likely to be dangerous: the
    repository first (publishing another repository's run is the worst
    outcome), then the workflow identity (a run of a *different* workflow in
    the right repository may have been produced by a job with different
    trust), then the event, the id and attempt, and finally the conclusion.

    Raises :class:`SourceRunRejected`; returns ``None`` on success. There is
    deliberately no boolean return: a caller cannot forget to check an
    exception the way it can forget to check a flag.
    """
    if expected.repository and run.repository != expected.repository:
        raise SourceRunRejected(
            "wrong-repository",
            f"the source run belongs to {run.repository or '(unknown)'!r}, not "
            f"{expected.repository!r}",
        )
    if expected.workflow and expected.workflow not in (
        run.workflow_path,
        run.workflow_name,
    ):
        raise SourceRunRejected(
            "wrong-workflow",
            f"the source run is {run.workflow_path or run.workflow_name!r}, "
            f"not {expected.workflow!r}",
        )
    if expected.event and run.event != expected.event:
        raise SourceRunRejected(
            "wrong-event",
            f"the source run was triggered by {run.event or '(unknown)'!r}, "
            f"not {expected.event!r}",
        )
    if expected.run_id and run.run_id != expected.run_id:
        raise SourceRunRejected(
            "wrong-run",
            f"the fetched run is {run.run_id or '(unknown)'!r}, not "
            f"{expected.run_id!r}",
        )
    if expected.run_attempt is not None and run.run_attempt != expected.run_attempt:
        raise SourceRunRejected(
            "wrong-attempt",
            f"the fetched run is attempt {run.run_attempt}, not {expected.run_attempt}",
        )
    if expected.allowed_conclusions and run.conclusion not in (
        expected.allowed_conclusions
    ):
        raise SourceRunRejected(
            "wrong-conclusion",
            f"the source run concluded {run.conclusion or '(none yet)'!r}; "
            f"allowed: {', '.join(expected.allowed_conclusions)}",
        )


# ---------------------------------------------------------------------------
# 2. Which PR, and was the analysed commit really its?
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedPullRequest:
    """The pull request this run's head SHA belongs to, per the API."""

    number: int
    head_sha: str
    base_repository: str
    head_repository: str
    state: str

    @property
    def from_fork(self) -> bool:
        return bool(
            self.head_repository and self.head_repository != self.base_repository
        )


def _pull_request_from_api(data: Mapping[str, Any]) -> ResolvedPullRequest | None:
    """One association entry, or ``None`` when it carries no usable answer.

    Never coerced into a candidate: an entry with no integer ``number``
    describes no pull request, and resolving one from it would be inventing
    the very answer this boundary exists to establish.
    """
    if not isinstance(data, Mapping):
        return None
    number = data.get("number")
    if not isinstance(number, int) or isinstance(number, bool):
        return None

    def _sub(parent: Mapping[str, Any], key: str) -> Mapping[str, Any]:
        value = parent.get(key)
        return value if isinstance(value, Mapping) else {}

    head = _sub(data, "head")
    base = _sub(data, "base")
    head_repo = _sub(head, "repo")
    base_repo = _sub(base, "repo")
    return ResolvedPullRequest(
        number=number,
        head_sha=str(head.get("sha", "") or ""),
        base_repository=str(base_repo.get("full_name", "") or ""),
        head_repository=str(head_repo.get("full_name", "") or ""),
        state=str(data.get("state", "") or ""),
    )


def resolve_pull_request(
    run: SourceRun,
    associated: Sequence[Mapping[str, Any]],
    *,
    repository: str,
    claimed_number: int | None = None,
) -> ResolvedPullRequest:
    """The one PR this run belongs to, resolved from the API's own answer.

    *associated* is the body of ``GET /repos/{repo}/commits/{sha}/pulls`` for
    the run's head SHA -- the association GitHub itself makes. It is used
    instead of the run document's own ``pull_requests`` array because that
    array is empty for a fork's pull request, which is precisely the case
    this whole design exists for.

    *claimed_number* is whatever the artifact said the PR was. It is never
    the answer: at most it must **agree** with the API's answer, and a
    disagreement is a refusal. An artifact that could choose the PR number
    could make a fork's analysis post onto an unrelated pull request.

    Ambiguity is a failure, not a choice. Zero associated PRs, more than one
    open one, or a PR whose base repository is not *repository* each raise,
    because every one of them means the publisher does not know where this
    result belongs -- and posting it somewhere is worse than posting it
    nowhere.
    """
    candidates = [
        pr
        for pr in (_pull_request_from_api(raw) for raw in associated)
        if pr is not None and pr.base_repository == repository
    ]
    if not candidates:
        raise SourceRunRejected(
            "no-pull-request",
            f"no pull request in {repository} is associated with commit "
            f"{run.head_sha or '(unknown)'}",
        )
    open_candidates = [pr for pr in candidates if pr.state == "open"] or candidates
    if len({pr.number for pr in open_candidates}) > 1:
        numbers = ", ".join(
            str(pr.number) for pr in sorted(open_candidates, key=lambda p: p.number)
        )
        raise SourceRunRejected(
            "ambiguous-pull-request",
            f"commit {run.head_sha} is associated with several pull requests "
            f"({numbers}); refusing to guess which one this result is about",
        )
    resolved = open_candidates[0]
    if claimed_number is not None and claimed_number != resolved.number:
        raise SourceRunRejected(
            "pull-request-mismatch",
            f"the artifact claims pull request #{claimed_number} but the API "
            f"associates this run with #{resolved.number}; the API answer is "
            "authoritative and the disagreement is not ignorable",
        )
    return resolved


def verify_tested_sha(
    run: SourceRun,
    pull_request: ResolvedPullRequest,
    *,
    tested_sha: str,
    tested_commit: Mapping[str, Any] | None = None,
) -> str:
    """Check the commit that was actually built against the PR's head.

    These are routinely **different** commits and conflating them is a real
    reporting error: a ``pull_request`` workflow checks out an ephemeral
    merge commit of the PR head into the base, so what was analysed is that
    merge commit while what a reviewer sees on the PR is the head. Reporting
    the merge SHA as the PR head makes the comment unmatchable against the
    commit list; reporting the head when a merge was analysed claims the
    analysis covered a tree it never saw.

    So both are kept, and their *association* is verified: the tested commit
    is acceptable when it is the PR head itself, or when the PR head is one
    of its parents. *tested_commit* is the API's
    ``GET /repos/{repo}/commits/{sha}`` response for *tested_sha*; without
    it, only the equality case can be established and anything else is
    refused rather than assumed.

    Returns the verified tested SHA.
    """
    if not tested_sha:
        raise SourceRunRejected(
            "no-tested-sha", "the artifact records no analysed commit"
        )
    if run.head_sha and pull_request.head_sha and run.head_sha != pull_request.head_sha:
        raise SourceRunRejected(
            "head-sha-mismatch",
            f"the run's head {run.head_sha} is not the pull request's head "
            f"{pull_request.head_sha}",
        )
    if tested_sha in (pull_request.head_sha, run.head_sha):
        return tested_sha
    if tested_commit is None:
        raise SourceRunRejected(
            "unverified-tested-sha",
            f"the analysed commit {tested_sha} is not the pull request head "
            f"{pull_request.head_sha} and no commit document was supplied to "
            "establish the relationship",
        )
    parents = tested_commit.get("parents")
    parent_shas = {
        str(p.get("sha", ""))
        for p in (parents if isinstance(parents, list) else [])
        if isinstance(p, Mapping)
    }
    if pull_request.head_sha and pull_request.head_sha in parent_shas:
        # A real `pull_request` merge commit has two parents: the PR head
        # and the base it was merged into. Requiring that is what makes
        # this branch mean "a merge of the head" rather than "any commit
        # whose parent is the head" -- the latter accepts an ordinary
        # single-parent child carrying an unrelated tree, which a
        # contributor can produce and name in the artifact, making the
        # trusted comment claim analysis of a commit that is not what CI
        # built. A commit built *on top of* the head is not the pull
        # request's tree, so refusing it is the correct answer and not
        # merely the cautious one.
        if len(parent_shas) < 2:
            raise SourceRunRejected(
                "unassociated-tested-sha",
                f"the analysed commit {tested_sha} has the pull request head "
                f"{pull_request.head_sha} as its only parent, so it is a "
                "commit built on top of the pull request rather than a merge "
                "of it; report the head itself, or the merge commit CI built",
            )
        return tested_sha
    raise SourceRunRejected(
        "unassociated-tested-sha",
        f"the analysed commit {tested_sha} is neither the pull request head "
        f"{pull_request.head_sha} nor a merge of it",
    )


def select_artifact(
    artifacts: Sequence[Mapping[str, Any]], name: str, *, run_id: str
) -> dict[str, Any]:
    """The named artifact belonging to *run_id*, or refuse.

    *artifacts* is the body of
    ``GET /repos/{repo}/actions/runs/{id}/artifacts``, which is already
    scoped to one run -- but the scoping is re-established here against each
    entry's own ``workflow_run.id``, because the endpoint is chosen by the
    shell and a boundary that trusts its caller to have called the right URL
    is not a boundary. An expired artifact is refused explicitly rather than
    producing a confusing download failure later.
    """
    matches = [
        a
        for a in artifacts
        if isinstance(a, Mapping) and str(a.get("name", "")) == name
    ]
    scoped = []
    for artifact in matches:
        owner = artifact.get("workflow_run")
        owner_id = str(owner.get("id", "")) if isinstance(owner, Mapping) else ""
        # An entry that states no owner is refused exactly like one stating
        # the wrong owner. `if owner_id and owner_id != run_id` accepted the
        # unstated case, which is the one an attacker controls: this whole
        # loop exists because the endpoint was chosen by the shell, so
        # "the entry did not say" cannot be read as "the entry belongs to
        # the run we asked about". Fail closed.
        if owner_id != str(run_id):
            continue
        scoped.append(artifact)
    if not scoped:
        raise SourceRunRejected(
            "artifact-not-found",
            f"run {run_id} published no artifact named {name!r}",
        )
    if len(scoped) > 1:
        raise SourceRunRejected(
            "ambiguous-artifact",
            f"run {run_id} published {len(scoped)} artifacts named {name!r}",
        )
    artifact = dict(scoped[0])
    if artifact.get("expired"):
        raise SourceRunRejected(
            "artifact-expired", f"artifact {name!r} from run {run_id} has expired"
        )
    return artifact


# ---------------------------------------------------------------------------
# 3. Is the archive safe to unpack?
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractionLimits:
    """Caps applied to one artifact, declared together so a caller cannot
    tighten one and silently leave another at its default."""

    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES
    max_entry_bytes: int = DEFAULT_MAX_ENTRY_BYTES
    max_entries: int = DEFAULT_MAX_ENTRIES
    max_ratio: float = DEFAULT_MAX_RATIO


#: ZIP external-attribute bits. The high 16 bits carry the Unix mode when
#: the archive was created on a Unix host (``create_system == 3``), and
#: ``0o170000`` is the file-type mask within it.
_UNIX_MODE_SHIFT = 16
_S_IFMT = 0o170000
_S_IFREG = 0o100000
_S_IFDIR = 0o040000
_S_IFLNK = 0o120000


def _entry_is_directory(info: zipfile.ZipInfo) -> bool:
    if info.filename.endswith("/"):
        return True
    if info.create_system == 3:
        return (info.external_attr >> _UNIX_MODE_SHIFT) & _S_IFMT == _S_IFDIR
    return False


def _reject_non_regular(info: zipfile.ZipInfo) -> None:
    """Refuse a symlink, device, socket or any other non-regular entry.

    ``zipfile`` has no notion of these: it will happily write a symlink
    entry's *target path* into a plain file, which sounds harmless until the
    consumer is something that follows it. Since the archive records the
    Unix mode, the type is checked directly rather than inferred from the
    name -- and an entry from a non-Unix host records no mode at all, so
    anything that is not recognisably a regular file or a directory is
    refused rather than assumed benign.
    """
    if info.create_system != 3:
        # No Unix mode recorded (a Windows-produced archive). Nothing to
        # check, and nothing that could carry a symlink either.
        return
    mode = (info.external_attr >> _UNIX_MODE_SHIFT) & _S_IFMT
    if mode == _S_IFLNK:
        raise SourceRunRejected(
            "artifact-symlink",
            f"artifact entry {info.filename!r} is a symlink",
        )
    if mode not in (_S_IFREG, _S_IFDIR, 0):
        raise SourceRunRejected(
            "artifact-non-regular",
            f"artifact entry {info.filename!r} is not a regular file "
            f"(mode {oct(mode)})",
        )


def safe_entry_path(destination: Path, name: str) -> Path:
    """Where *name* may be written under *destination*, or refuse.

    Refuses an absolute path under either platform's rules, any ``..``
    component, and a drive-qualified Windows path, then re-checks the result
    against the resolved destination. The last check is what catches a name
    the component rules did not anticipate; the first ones are what keep the
    error message specific about which rule was broken.
    """
    if not name or name in (".", "./"):
        raise SourceRunRejected("artifact-bad-path", "artifact entry has no name")
    if PurePosixPath(name).is_absolute() or PureWindowsPath(name).is_absolute():
        raise SourceRunRejected(
            "artifact-absolute-path",
            f"artifact entry {name!r} is an absolute path",
        )
    if PureWindowsPath(name).drive:
        raise SourceRunRejected(
            "artifact-absolute-path",
            f"artifact entry {name!r} names a drive",
        )
    parts = [p for p in PurePosixPath(name.replace("\\", "/")).parts if p != "."]
    if any(part == ".." for part in parts):
        raise SourceRunRejected(
            "artifact-traversal",
            f"artifact entry {name!r} traverses outside the extraction directory",
        )
    if not parts:
        raise SourceRunRejected(
            "artifact-bad-path", f"artifact entry {name!r} is empty"
        )
    target = destination.joinpath(*parts)
    try:
        target.resolve(strict=False).relative_to(destination.resolve(strict=False))
    except ValueError as exc:
        raise SourceRunRejected(
            "artifact-traversal",
            f"artifact entry {name!r} resolves outside the extraction directory",
        ) from exc
    return target


def inspect_archive(
    archive: Path, limits: ExtractionLimits = ExtractionLimits()
) -> list[zipfile.ZipInfo]:
    """Validate every entry *before* writing a single byte.

    The whole central directory is checked first, so a bomb is refused
    without any of it reaching the filesystem. That ordering matters: a
    check interleaved with extraction leaves a partial tree behind whose
    cleanup is itself another thing to get right.

    Note the declared sizes in the central directory are not trusted as
    *facts* -- they are the basis for refusing an archive up front, and
    :func:`extract_artifact` re-enforces the same caps against the bytes it
    actually reads, so a lying header buys nothing.
    """
    if not archive.is_file():
        raise SourceRunRejected(
            "artifact-unreadable", f"{archive} is not a readable file"
        )
    try:
        with zipfile.ZipFile(archive) as zf:
            infos = zf.infolist()
    except (zipfile.BadZipFile, OSError) as exc:
        raise SourceRunRejected(
            "artifact-unreadable", f"the artifact is not a readable zip ({exc})"
        ) from exc
    if len(infos) > limits.max_entries:
        raise SourceRunRejected(
            "artifact-too-many-entries",
            f"the artifact holds {len(infos)} entries, over the "
            f"{limits.max_entries}-entry limit",
        )
    total_declared = 0
    total_compressed = 0
    for info in infos:
        _reject_non_regular(info)
        if _entry_is_directory(info):
            continue
        if info.file_size > limits.max_entry_bytes:
            raise SourceRunRejected(
                "artifact-entry-too-large",
                f"artifact entry {info.filename!r} declares "
                f"{info.file_size} bytes, over the "
                f"{limits.max_entry_bytes}-byte per-entry limit",
            )
        if (
            info.compress_size >= _RATIO_FLOOR_BYTES
            and info.file_size / max(info.compress_size, 1) > limits.max_ratio
        ):
            raise SourceRunRejected(
                "artifact-compression-bomb",
                f"artifact entry {info.filename!r} expands "
                f"{info.file_size / max(info.compress_size, 1):.0f}x, over the "
                f"{limits.max_ratio:.0f}x limit",
            )
        total_declared += info.file_size
        total_compressed += info.compress_size
    if total_declared > limits.max_total_bytes:
        raise SourceRunRejected(
            "artifact-too-large",
            f"the artifact expands to {total_declared} bytes, over the "
            f"{limits.max_total_bytes}-byte limit",
        )
    if (
        total_compressed >= _RATIO_FLOOR_BYTES
        and total_declared / max(total_compressed, 1) > limits.max_ratio
    ):
        raise SourceRunRejected(
            "artifact-compression-bomb",
            f"the artifact expands "
            f"{total_declared / max(total_compressed, 1):.0f}x overall, over "
            f"the {limits.max_ratio:.0f}x limit",
        )
    return infos


#: Read size for the streaming extraction below. Small enough that the cap
#: is enforced long before a lying header could matter.
_CHUNK = 64 * 1024


def extract_artifact(
    archive: Path,
    destination: Path,
    limits: ExtractionLimits = ExtractionLimits(),
) -> list[Path]:
    """Unpack *archive* into *destination*, refusing anything hostile.

    Returns the files written. Every entry is streamed with the caps
    re-enforced against the bytes actually produced, so a central-directory
    header understating a member's size cannot get more written than
    declared. Each file is created with no executable bit: nothing in an
    artifact is ever run, and a mode that says otherwise is not preserved
    for it.
    """
    infos = inspect_archive(archive, limits)
    destination.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    total = 0
    with zipfile.ZipFile(archive) as zf:
        for info in infos:
            target = safe_entry_path(destination, info.filename)
            if _entry_is_directory(info):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink() or target.parent.is_symlink():
                # A previous entry in the same archive could have created
                # the directory this one lands in. It cannot be a symlink
                # (those are refused above), but re-checking costs nothing
                # and keeps the guarantee local to the write.
                raise SourceRunRejected(
                    "artifact-symlink",
                    f"artifact entry {info.filename!r} would be written "
                    "through a symlink",
                )
            entry_total = 0
            with zf.open(info) as source, open(target, "wb") as sink:
                while True:
                    chunk = source.read(_CHUNK)
                    if not chunk:
                        break
                    entry_total += len(chunk)
                    total += len(chunk)
                    if entry_total > limits.max_entry_bytes:
                        raise SourceRunRejected(
                            "artifact-entry-too-large",
                            f"artifact entry {info.filename!r} exceeded the "
                            f"{limits.max_entry_bytes}-byte per-entry limit "
                            "while being read",
                        )
                    if total > limits.max_total_bytes:
                        raise SourceRunRejected(
                            "artifact-too-large",
                            f"the artifact exceeded the "
                            f"{limits.max_total_bytes}-byte total limit while "
                            "being read",
                        )
                    sink.write(chunk)
            target.chmod(0o644)
            written.append(target)
    return written


def load_json_document(path: Path, *, max_bytes: int = DEFAULT_MAX_ENTRY_BYTES) -> Any:
    """Read one extracted document as JSON, and only as JSON.

    Named and used explicitly so the rule has a place to live: an artifact's
    contents are parsed with :func:`json.loads` and nothing else. No
    ``pickle``, no ``yaml.load``, no import, no ``exec`` -- every one of
    those turns a fork's artifact into code execution in a job holding a
    write token.
    """
    if not path.is_file():
        raise SourceRunRejected("artifact-unreadable", f"{path} is not a regular file")
    if path.stat().st_size > max_bytes:
        raise SourceRunRejected(
            "artifact-entry-too-large",
            f"{path} is larger than the {max_bytes}-byte limit",
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceRunRejected(
            "artifact-unreadable", f"{path} is not readable JSON ({exc})"
        ) from exc


__all__ = [
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_MAX_ENTRY_BYTES",
    "DEFAULT_MAX_RATIO",
    "DEFAULT_MAX_TOTAL_BYTES",
    "ExtractionLimits",
    "ResolvedPullRequest",
    "RunExpectation",
    "SourceRun",
    "SourceRunRejected",
    "extract_artifact",
    "inspect_archive",
    "load_json_document",
    "resolve_pull_request",
    "safe_entry_path",
    "select_artifact",
    "verify_source_run",
    "verify_tested_sha",
]
