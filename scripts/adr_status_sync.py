#!/usr/bin/env python3
"""ADR status-sync gate: an ADR's Status claim vs. the index, and vs. the code.

A leaf module imported by ``check_ai_readiness.py``, which registers
:func:`check_adr_status_sync` as the ``adr-status-sync`` check and reuses this
module's ADR-status *parsing* helpers (:data:`_ADR_FILE_RE`,
:func:`_adr_status_text`) for its own ``adr-index-nav-sync`` check. Split out
rather than added inline because ``check_ai_readiness.py`` is already past the
2000-line hard cap and only stays green through ``LARGE_FILE_ALLOWLIST`` --
which AGENTS.md is explicit is "not a way to silently exempt a new file"
(Codex review on PR #667).

Pure-stdlib, like its caller, so it can run as the first CI step before
``pip install``.
"""

from __future__ import annotations

import datetime as _dt
import re
import subprocess
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "abicheck"
DOCS = ROOT / "docs"

#: Repo-relative first-party Python roots, mirroring check_ai_readiness.py's
#: own ``FIRST_PARTY_PY_ROOTS``. A Status paragraph naming
#: ``tests/test_cli_contract.py`` (ADR-037 already does) means that test file,
#: and the tripwire below has to resolve it as such rather than looking for it
#: inside the package (Codex review on PR #667). Kept as a local literal to
#: keep this module a leaf; ``tests/test_ai_readiness.py`` asserts the two
#: lists agree, so a new root added there can't silently go unseen here.
FIRST_PARTY_ROOT_NAMES: tuple[str, ...] = (
    "abicheck",
    "scripts",
    "tests",
    "eval",
    "validation",
    "action",
    "contrib",
    "agent-evals",
)


class Findings(Protocol):
    """The error/warning sink check_ai_readiness.py passes in."""

    def err(self, check: str, msg: str) -> None:
        """Record a blocking finding under `check`."""
        ...

    def warn(self, check: str, msg: str) -> None:
        """Record a non-blocking finding under `check`."""
        ...


def _read(p: Path) -> str:
    """File contents, or `""` when unreadable.

    A file this gate can't read is not a finding of its own: the ADR checks
    below all degrade to "nothing to compare", which the surrounding checks
    (`adr-index-nav-sync`'s missing-Status error) already report properly.
    """
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


#: Matches an ADR filename's leading number, e.g. "020" from
#: "020-build-context-capture.md". Two files may legitimately share a number
#: (020a/020b, 021a/021b are sibling sub-ADRs) — this check is per-file, not
#: per-number, so that's not flagged here.
_ADR_FILE_RE = re.compile(r"^\d{3}-.+\.md$")

#: An ADR's status, either as an inline bold label ("**Status:** Accepted —
#: ...") or as its own heading ("## Status\n\nAccepted — ..."). Both
#: conventions are in active use across the existing 51 ADRs.
_ADR_STATUS_INLINE_START_RE = re.compile(r"^\*\*Status:\*\*[ \t]*(.*)$")
_ADR_STATUS_HEADING_START_RE = re.compile(r"^## Status[ \t]*$")
#: A line that ends the Status paragraph's continuation: a new bolded
#: field label (`**Decision maker:** ...`), a heading, or a `---` rule.
_ADR_STATUS_CONTINUATION_STOP_RE = re.compile(
    r"^\s*\*\*[^*\n]+:\*\*|^\s*#|^\s*-{3,}\s*$"
)


def _adr_status_text(text: str) -> str | None:
    """Return the full Status paragraph -- not just its first physical line
    -- for either the inline (`**Status:** ...`) or heading (`## Status`)
    convention. Several real ADRs already wrap their status across multiple
    lines; capturing only the first line would hide a genuine replacement
    link that happens to fall on a continuation line from
    _links_to_another_adr()."""
    lines = text.split("\n")
    start: int | None = None
    first_content: str | None = None
    for i, line in enumerate(lines):
        m = _ADR_STATUS_INLINE_START_RE.match(line)
        if m:
            start, first_content = i, m.group(1)
            break
        if _ADR_STATUS_HEADING_START_RE.match(line):
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            # An empty "## Status" section immediately followed by the next
            # heading/field (no actual status content) must not treat that
            # structural line as the status text -- leave start unset so
            # this reports as a missing Status, not a bogus one.
            if j < len(lines) and not _ADR_STATUS_CONTINUATION_STOP_RE.match(lines[j]):
                start, first_content = j, lines[j]
            break
    if start is None:
        return None
    parts = [first_content] if first_content is not None else []
    j = start + 1
    while j < len(lines):
        nxt = lines[j]
        if not nxt.strip() or _ADR_STATUS_CONTINUATION_STOP_RE.match(nxt):
            break
        parts.append(nxt)
        j += 1
    result = " ".join(p.strip() for p in parts).strip()
    return result or None


#: An ADR's optional, opt-in verification receipt: the commit a maintainer
#: last checked this ADR's Status claims against, plus the date they did it.
#: Absent means "nobody has recorded a check" -- which is the honest default
#: for the ADRs written before this convention existed, and why the freshness
#: check below is opt-in rather than retrofitted onto every file.
_ADR_VERIFIED_START_RE = re.compile(r"^\*\*Verified:\*\*[ \t]*(.*)$", re.MULTILINE)
_ADR_VERIFIED_VALUE_RE = re.compile(
    r"^(?P<ref>[A-Za-z0-9._/-]+)@(?P<sha>[0-9a-f]{7,40})[ \t]+on[ \t]+"
    r"(?P<date>\d{4}-\d{2}-\d{2})\s*$"
)

#: The end of an ADR's leading metadata block: the first section heading or
#: horizontal rule. Everything before it is the `**Date:**`/`**Status:**`/
#: `**Verified:**`/`**Decision maker:**` preamble every ADR here opens with.
#: `## Status` is deliberately *not* a terminator: a handful of ADRs use the
#: heading style for their status (ADR-036, ADR-042), and stopping there would
#: put their status section -- and any receipt following it -- outside the
#: block, so a receipt added to one would be silently ignored. That is exactly
#: the class of quiet no-op this gate exists to prevent.
_ADR_METADATA_END_RE = re.compile(r"^\s*#{2,}\s+(?!Status\b)|^\s*-{3,}\s*$")
_ADR_FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")


def _adr_metadata_block(text: str) -> str:
    """The ADR's leading metadata preamble, with fenced code removed.

    The receipt is searched here rather than across the whole document so an
    ADR *documenting* the convention -- a fenced `**Verified:** <ref>@<sha>`
    example, deliberately illustrative and possibly incomplete -- can't be
    read as that ADR's own live receipt and fail a required CI job (Codex
    review on PR #667). Same precaution `adr-index-nav-sync` already takes
    for links via `_strip_adr_link_noise` (PR #619 review); a fence is
    stripped even inside the preamble, since the convention places the real
    receipt on a bare metadata line, never inside a code block.
    """
    kept: list[str] = []
    fence: str | None = None
    for line in text.split("\n"):
        m = _ADR_FENCE_RE.match(line)
        if m:
            token = m.group(1)
            if fence is None:
                fence = token[0]
            elif token[0] == fence:
                fence = None
            continue
        if fence is not None:
            continue
        if _ADR_METADATA_END_RE.match(line):
            break
        kept.append(line)
    return "\n".join(kept)


#: A first-party Python file named inside a Status paragraph: a repo-relative
#: path under any first-party root ("`abicheck/contract_evaluation.py`",
#: "`tests/test_cli_contract.py`") or a bare module name
#: ("`contract_replay.py`"). Real status lines mix both freely, often naming
#: the first module of a family in full and the rest of the family bare. These
#: are what a Status claim is *about* ("the shadow evaluator is implemented in
#: X"), so a commit touching one after the recorded verification is exactly the
#: signal that the claim needs re-reading.
_ADR_MODULE_PATH_RE = re.compile(
    r"\b(?P<path>[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*\.py)\b"
)


def _is_first_party_path(path: str) -> bool:
    """True for a repo-relative path rooted in a first-party tree."""
    head, sep, _rest = path.partition("/")
    return bool(sep) and head in FIRST_PARTY_ROOT_NAMES


def _adr_named_modules(status: str) -> list[str]:
    """Repo-relative paths of the first-party files a Status claim names.

    A path written out under a first-party root -- `abicheck/x.py`, but also
    `tests/test_cli_contract.py`, which ADR-037's Status already names -- is
    always a tripwire, **including when no such file exists at HEAD**. A file
    named by a verified claim and since deleted or renamed is the case most
    likely to have invalidated that claim, so dropping it for failing an
    existence test would suppress the warning exactly where it matters most
    (both points from Codex review on PR #667).

    A *bare* `x.py` has to resolve inside the package, since that's the only
    thing distinguishing a real module reference from an unrelated `verify.py`
    or `conftest.py` mention. That existence gate means a bare name whose
    module was deleted degrades to no tripwire -- the conservative default,
    since after deletion nothing distinguishes the two cases any more.

    **Family shorthand (`_resolver.py` after `compatibility_evaluation_config.py`)
    is deliberately not resolved.** An earlier revision guessed it, and three
    successive review rounds each found a different shape the guess got wrong:
    a nested anchor lost its directory, `source_graph.py` + `_findings.py`
    wants the family *appended* where `graph_facts.py` + `_impact.py` wants it
    *replaced*, and an expanded path could not be kept as a tripwire after
    deletion without also fabricating one from a wrong guess. Prose-shape
    guessing is the wrong thing for a gate to do: a Status that wants a module
    watched names it in full, which is exact, auditable, and survives
    deletion. See `adr/index.md`'s convention section.
    """
    named = set()
    for m in _ADR_MODULE_PATH_RE.finditer(status):
        path = m.group("path")
        if _is_first_party_path(path):
            named.add(path)
        elif "/" not in path and (PKG / path).is_file():
            named.add(f"abicheck/{path}")
    return sorted(named)


#: One row of the ADR index's status table: `| [NNN](file.md) | Title | Status |`.
_ADR_INDEX_ROW_RE = re.compile(
    r"^\|\s*\[[^\]]+\]\((?P<file>\d{3}-[^)]+\.md)\)\s*\|[^|]*\|(?P<status>.*)\|\s*$",
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Check: an ADR's Status claim agrees with the index, and its recorded
# verification hasn't been outrun by the code it names
# ---------------------------------------------------------------------------


#: The decision half of a Status line -- the only vocabulary that is genuinely
#: closed. The implementation half is deliberately left as prose (see the
#: "Status field convention" section of docs/contribute/adr/index.md for why a
#: structured frontmatter schema was considered and rejected).
_ADR_DECISION_WORDS: frozenset[str] = frozenset(
    {"accepted", "proposed", "superseded", "deprecated", "rejected"}
)

#: "not implemented" / "not started" / "not yet implemented" -- a claim that
#: *nothing* has shipped. Matched ahead of the bare "implemented" below, since
#: the negative phrase contains the positive one as a substring.
_ADR_IMPL_NONE_RE = re.compile(r"\bnot\s+(?:yet\s+)?(?:implemented|started)\b", re.I)
_ADR_IMPL_SOME_RE = re.compile(r"\bimplemented\b", re.I)


def _adr_decision_word(status: str) -> str | None:
    """The leading decision word of a Status claim, or None when the claim
    doesn't open with one from the closed vocabulary."""
    lead = re.split(r"[\s—:.,;()-]", status.strip(), maxsplit=1)[0].strip("*").lower()
    return lead if lead in _ADR_DECISION_WORDS else None


def _adr_impl_claim(status: str) -> str | None:
    """Coarsely classify a Status claim's *implementation* half as "none"
    (nothing shipped) or "some" (anything shipped, partially or fully), or
    None when it makes no implementation claim at all.

    Deliberately only two buckets. An earlier prototype tried to distinguish
    "fully implemented" from "partially implemented" by keyword and flagged 15
    of 56 ADRs, nearly all of them false positives: the index cell is an
    *abridgement* of a status paragraph that often runs a dozen lines, so it
    routinely omits a follow-up the full paragraph mentions. Paraphrase is not
    drift. A flat contradiction -- one side says nothing shipped, the other
    says something did -- is, and that is what this reports.
    """
    none_m = _ADR_IMPL_NONE_RE.search(status)
    some_m = _ADR_IMPL_SOME_RE.search(status)
    if none_m and (some_m is None or none_m.start() <= some_m.start()):
        return "none"
    if some_m:
        return "some"
    return None


def _git_output(*args: str) -> str | None:
    """Run a read-only git command in the repo, or None if git is unusable
    (not installed, not a repository, command failed)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(ROOT), *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _repo_is_shallow() -> bool:
    """True when this checkout has truncated history (or git can't say).

    Used for exactly one decision: whether a `**Verified:**` sha that doesn't
    resolve is a typo (ERROR) or simply outside the fetch depth (skip). It is
    deliberately *not* used to gate the freshness query itself — a shallow
    checkout still answers "did anything touch this path since commit X"
    correctly for the history it does have. An earlier revision skipped the
    whole check whenever the repo was shallow, which disabled the tripwire
    wherever history was truncated.

    The `ai-readiness` CI job checks out with `fetch-depth: 0` precisely so
    this branch is *not* taken there: on a depth-1 checkout no receipt sha
    resolves, so the tripwire would stand down on every run and be live only
    on developer machines (Codex review on PR #667). Shallow tolerance
    remains for any other environment that runs this script.
    """
    shallow = _git_output("rev-parse", "--is-shallow-repository")
    return shallow is None or shallow.strip() != "false"


#: Refs to try, in order, when asking "is this receipt anchored to durable
#: history". The remote ref is preferred: a PR checkout has `origin/main` but
#: usually no local `main`.
_DEFAULT_BRANCH_REFS: tuple[str, ...] = ("origin/main", "main")


def _durable_history_ref() -> str | None:
    """A ref for the default branch, or None if none is resolvable here."""
    for ref in _DEFAULT_BRANCH_REFS:
        if _git_output("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"):
            return ref
    return None


def _check_receipt_is_durably_anchored(f: Findings, name: str, sha: str) -> None:
    """A receipt must name a commit that will still exist after this branch is
    gone -- i.e. one reachable from the default branch.

    Anchoring to a PR-branch commit is a delayed-action failure, not a
    cosmetic one: the branch commit is unreachable once the PR squash-merges,
    and because the `ai-readiness` job checks out full history, the
    unresolvable sha is reported as an *error* rather than tolerated the way a
    shallow checkout's is. The result is a required job that passes on the PR
    and then fails on main forever, for a documentation edit. Caught here, at
    the moment the receipt is written, instead (Codex review on PR #667).

    Skipped when no default-branch ref is resolvable -- a detached clone with
    no `main` can't answer the question, and guessing would fail honest work.
    """
    ref = _durable_history_ref()
    if ref is None:
        return
    if _git_output("merge-base", "--is-ancestor", sha, ref) is None:
        f.err(
            "adr-status-sync",
            f"docs/contribute/adr/{name}: '**Verified:**' names commit {sha}, "
            f"which is not reachable from {ref} — record the default-branch "
            "commit whose tree you actually verified, not a commit on the "
            "branch adding the receipt (that one disappears on merge and "
            "turns this into a permanent failure on main)",
        )


def _receipt_date_is_real(f: Findings, name: str, raw_date: str) -> bool:
    """True when the receipt's date is a real calendar date, not in the future.

    The value regex only pins the *shape* `\\d{4}-\\d{2}-\\d{2}`, so
    `2026-99-99` — or a date after the verification could have happened —
    would otherwise pass a required gate while recording a check that cannot
    have taken place (Codex review on PR #667).

    One day of slack on the upper bound: the author's local date can be ahead
    of the runner's UTC date, and failing an honest receipt over a timezone
    boundary would be worse than accepting a receipt dated one day early.
    """
    try:
        parsed = _dt.date.fromisoformat(raw_date)
    except ValueError:
        f.err(
            "adr-status-sync",
            f"docs/contribute/adr/{name}: '**Verified:**' date {raw_date!r} "
            "is not a real calendar date",
        )
        return False
    if parsed > _dt.date.today() + _dt.timedelta(days=1):
        f.err(
            "adr-status-sync",
            f"docs/contribute/adr/{name}: '**Verified:**' date {raw_date} is "
            "in the future; it records when the Status was checked, which "
            "cannot be later than today",
        )
        return False
    return True


def _warn_if_status_edited_after_verification(
    f: Findings, name: str, raw_date: str, status: str
) -> None:
    """Warn when the Status text was edited after it was last verified.

    A receipt anchors the commit whose **code** was checked, not the text that
    was there at the time: writing corrected prose about the code as of that
    commit is the whole point, so comparing the Status at the receipt commit
    with the current one would flag every honest receipt (the commit that
    introduced this convention included exactly that shape -- ADR-049's text
    at `2e43d53` is the wrong text the receipt was written to replace).

    The real hole is the other direction, and the discriminator is the
    receipt's *date*: in the honest flow the Status edit and the receipt land
    together, so the Status as of the receipt date already reads as it does
    now. A *later* Status edit that doesn't move the receipt forward leaves
    the receipt vouching for claims nobody checked -- which is what this
    reports (Codex review on PR #667, mechanism corrected).
    """
    cutoff = f"{raw_date}T23:59:59"
    rel = f"docs/contribute/adr/{name}"
    at_receipt = _git_output("rev-list", "-1", f"--until={cutoff}", "HEAD", "--", rel)
    if not at_receipt or not at_receipt.strip():
        # No commit touching this ADR at or before the receipt date -- a fresh
        # file, or history this checkout doesn't carry. Nothing to compare.
        return
    then = _git_output("show", f"{at_receipt.strip()}:{rel}")
    if then is None:
        return
    if (_adr_status_text(then) or "") != status:
        f.warn(
            "adr-status-sync",
            f"docs/contribute/adr/{name}: the Status was edited after "
            f"{raw_date}, the date its '**Verified:**' receipt records — so "
            "the receipt now vouches for claims that were not part of what "
            "was checked; re-read the Status against the code and move the "
            "receipt forward",
        )


def _check_adr_verification_receipt(
    f: Findings, name: str, text: str, status: str
) -> None:
    """Validate an ADR's opt-in `**Verified:**` receipt and warn when the code
    it points at has moved on.

    The receipt records the commit at which a maintainer last checked this
    ADR's Status claims against the tree. Every first-party module path the
    Status paragraph *names* is then a tripwire: a commit touching one after
    that point means the prose describing it may no longer hold. That is a
    WARN, not an ERROR -- a change to a named module doesn't prove the claim
    went stale, it proves nobody has re-read it since the code moved.

    Exactly one receipt is allowed. Two of them -- what a merge resolution or
    a careless edit produces -- leave "which commit was this verified at?"
    ambiguous, and validating only the first would let a malformed or
    conflicting second one sit in the file with the gate still green (Codex
    review on PR #667). That is the same silent no-op this whole check exists
    to prevent, so it is reported rather than resolved by picking one.
    """
    receipts = _ADR_VERIFIED_START_RE.findall(_adr_metadata_block(text))
    if not receipts:
        return
    if len(receipts) > 1:
        f.err(
            "adr-status-sync",
            f"docs/contribute/adr/{name}: carries {len(receipts)} "
            "'**Verified:**' lines; exactly one is allowed, since which "
            "commit the Status was checked at would otherwise be ambiguous "
            f"({', '.join(repr(r.strip()) for r in receipts)})",
        )
        return
    raw = receipts[0].strip()
    value = _ADR_VERIFIED_VALUE_RE.match(raw)
    if value is None:
        f.err(
            "adr-status-sync",
            f"docs/contribute/adr/{name}: malformed '**Verified:**' line "
            f"({raw!r}); expected '<ref>@<sha> on YYYY-MM-DD', "
            "e.g. 'main@2e43d53 on 2026-08-04'",
        )
        return
    if not _receipt_date_is_real(f, name, value.group("date")):
        return
    sha = value.group("sha")
    if _git_output("cat-file", "-e", f"{sha}^{{commit}}") is None:
        if not _repo_is_shallow():
            f.err(
                "adr-status-sync",
                f"docs/contribute/adr/{name}: '**Verified:**' names commit "
                f"{sha}, which doesn't exist in this repository",
            )
        # On a shallow checkout the commit may simply predate the fetch
        # depth, which says nothing about the receipt's validity.
        return
    _check_receipt_is_durably_anchored(f, name, sha)
    _warn_if_status_edited_after_verification(f, name, value.group("date"), status)
    modules = _adr_named_modules(status)
    if not modules:
        return
    log = _git_output("log", "--format=%h", f"{sha}..HEAD", "--", *modules)
    if not log or not log.strip():
        return
    commits = log.split()
    f.warn(
        "adr-status-sync",
        f"docs/contribute/adr/{name}: Status was verified at {sha} but "
        f"{len(commits)} commit(s) have touched the module(s) it names since "
        f"({', '.join(modules)}) — re-read the claim against the code and "
        "move the '**Verified:**' line forward, or correct the claim",
    )


def check_adr_status_sync(f: Findings) -> None:
    """Keep an ADR's own Status line and its row in the ADR index from
    contradicting each other, and keep an opt-in verification receipt honest.

    Two separate failure modes, found by a 2026-08 status review:

    1. *Document-vs-document drift.* ADR-056's own file said "partially
       implemented" while the index table said "not implemented" — a reader
       gets a different answer depending on which page they open. Only a flat
       contradiction is reported (see _adr_impl_claim); the index cell is
       allowed to abridge, because that's what an index is for.
    2. *Document-vs-code drift.* ADR-049's Status said its shadow evaluator
       "is not called from any pipeline stage" and that nothing was "wired
       into the CLI ... or reports" — five merged PRs after that had stopped
       being true. No comparison between two prose documents can catch this;
       only re-reading the code can. What the `**Verified:**` receipt adds is
       the *tripwire*: record which commit you checked at, and this check
       tells the next reader when the modules that claim names have changed
       underneath it.
    """
    adr_dir = DOCS / "contribute" / "adr"
    if not adr_dir.is_dir():
        return
    index_text = _read(adr_dir / "index.md")
    rows = {
        m.group("file"): m.group("status").strip()
        for m in _ADR_INDEX_ROW_RE.finditer(index_text)
    }
    for md in sorted(adr_dir.glob("*.md")):
        if md.name == "index.md" or not _ADR_FILE_RE.match(md.name):
            continue
        text = _read(md)
        # Raw text, not _strip_adr_link_noise'd: the module paths the
        # freshness tripwire keys on are written as inline code, which that
        # stripper (correctly, for its own link-scanning purpose) removes.
        status = _adr_status_text(text)
        row = rows.get(md.name)
        if status is None or row is None:
            # A missing Status line is adr-index-nav-sync's error to report,
            # and a missing index row is its "not linked from index.md" error.
            # Reporting either twice would just be noise.
            continue
        file_decision, row_decision = (
            _adr_decision_word(status),
            _adr_decision_word(row),
        )
        # An unparseable decision word is reported rather than skipped: the
        # vocabulary is closed, so `Acceptd` is a typo, and treating it as
        # "nothing to compare" would silently switch this gate off for the
        # one ADR whose metadata is already known-broken (Codex review).
        unrecognized = [
            side
            for side, parsed in (
                ("its own Status line", file_decision),
                ("its docs/contribute/adr/index.md row", row_decision),
            )
            if parsed is None
        ]
        if unrecognized:
            f.err(
                "adr-status-sync",
                f"docs/contribute/adr/{md.name}: {' and '.join(unrecognized)} "
                "doesn't open with a recognized decision word (one of: "
                f"{', '.join(sorted(_ADR_DECISION_WORDS))})",
            )
        elif file_decision != row_decision:
            f.err(
                "adr-status-sync",
                f"docs/contribute/adr/{md.name}: decision is "
                f"'{file_decision}' in the ADR but '{row_decision}' in "
                "docs/contribute/adr/index.md",
            )
        file_impl, row_impl = _adr_impl_claim(status), _adr_impl_claim(row)
        if file_impl and row_impl and file_impl != row_impl:
            said = {"none": "nothing implemented", "some": "something implemented"}
            f.err(
                "adr-status-sync",
                f"docs/contribute/adr/{md.name}: the ADR claims "
                f"{said[file_impl]} but docs/contribute/adr/index.md claims "
                f"{said[row_impl]}",
            )
        _check_adr_verification_receipt(f, md.name, text, status)
