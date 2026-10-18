#!/usr/bin/env python3
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

"""Rank ``test_*`` functions by how often refactor commits had to edit them.

A mirror test restates the implementation's behaviour as expected values, so
a refactor that changes no behaviour can still force an edit to it. For every
test this report asks two questions of the history:

* which commits that claim to be behaviour-preserving (``refactor:``,
  ``refactor(...)``, ``perf:``, ``style:``) had to touch it; and
* of those, which changed an **assertion** (or an expected value) rather than
  setup. An assertion edit in a refactor commit is the strong signal: the
  test's stated expectation moved while the commit said nothing observable
  changed.

``feat:``/``fix:`` edits to the same test are counted as context only.

Method:

* ``git log -p -M -U0 --no-merges`` over ``tests/`` in the window (``--since``,
  default one year), oldest first. Renames are followed, so a test keeps its
  identity across a file move; a pure rename has no hunks and counts nothing.
* Each changed hunk is attributed to the innermost ``test_*`` function that
  encloses its changed lines. Removed lines are looked up in the parent
  revision and added lines in the commit, each parsed with ``ast``.
* A changed line is labelled ``assertion`` when it sits in an ``assert``
  statement, in the header of a ``pytest.raises``/``warns`` (or
  ``assertRaises``/``assertWarns``) ``with``, in a call to an ``assert*``
  method, or in an assignment to an ``expected*``/``want*`` name. It is
  ``neutral`` and attributes nothing when it is an import, blank or a comment.
  Anything else is ``setup``.
* A test that did not exist at the parent revision was *created* by the
  commit, not edited, and is not counted. A test whose body is unchanged but
  which the commit moved (to another place in its file, or to another file
  under the same name) is not counted either: a reorder or a split is not an
  edit. A moved test whose body also changed is counted against its old
  location, so its record reads as gone. Import-only and blank-only hunks are
  not counted.
* Tests with a refactor edit that no longer exist at HEAD are reported
  separately as ``gone``.

Limits: the labels are syntactic, so a changed expected value that is passed
through a helper reads as setup. Edits to helpers outside ``test_*`` functions
are not attributed to the tests that call them, so this under-counts churn
caused by a shared fixture. Treat the ranking as a review queue: confirm a
candidate by reading its commits before changing the test.

Run: ``python scripts/test_refactor_churn.py --since "1 year ago"``. Use
``--json PATH`` for the full listing. The script reports and never gates: it
exits 0 whenever the analysis runs, and 1 when git cannot produce it.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import re
import sys

from refactor_churn_source import ChurnError, _Blobs, _git, _run, _Source

TESTS_PATH = "tests"
REFACTOR_RE = re.compile(r"^(refactor|perf|style)(\([^)]*\))?!?:")
FEATFIX_RE = re.compile(r"^(feat|fix)(\([^)]*\))?!?:")
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
#: ASCII record and field separators: they cannot appear in a commit subject.
SEP = "\x1e"
FIELD = "\x1f"


# ---------------------------------------------------------------------------
# The log: commits and their hunks
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _Section:
    """One file's diff inside one commit."""

    old: str | None = None
    new: str | None = None
    #: (changed lines at the parent, changed lines at the commit), per hunk.
    hunks: list[tuple[range, range]] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class _Commit:
    sha: str
    parent: str | None
    subject: str
    sections: list[_Section]


def _diff_path(raw: str, prefix: str) -> str | None:
    if raw == "/dev/null":
        return None
    return raw.removeprefix(prefix).rstrip("\t")


def _hunk_range(start: str, count: str | None) -> range:
    first = int(start)
    return range(first, first + (int(count) if count is not None else 1))


def _parse_log(text: str) -> list[_Commit]:
    commits: list[_Commit] = []
    for chunk in text.split(SEP)[1:]:
        header, _, patch = chunk.partition("\n")
        ids, _, subject = header.partition(FIELD)
        sha, *parents = ids.split()
        commit = _Commit(sha, parents[0] if parents else None, subject, [])
        section: _Section | None = None
        for line in patch.split("\n"):
            if line.startswith("diff --git "):
                section = _Section()
                commit.sections.append(section)
            elif section is None:
                continue
            elif line.startswith("@@"):
                match = HUNK_RE.match(line)
                if match is None:
                    raise ChurnError(f"unparseable hunk header: {line!r}")
                section.hunks.append(
                    (
                        _hunk_range(match[1], match[2]),
                        _hunk_range(match[3], match[4]),
                    )
                )
            elif section.hunks:
                # Body lines of a hunk; a removed "-- x" must not read as a header.
                continue
            elif line.startswith("rename from "):
                section.old = line[len("rename from ") :]
            elif line.startswith("rename to "):
                section.new = line[len("rename to ") :]
            elif line.startswith("--- "):
                section.old = _diff_path(line[4:], "a/")
            elif line.startswith("+++ "):
                section.new = _diff_path(line[4:], "b/")
        commits.append(commit)
    return commits


def _kind(subject: str) -> str:
    if REFACTOR_RE.match(subject):
        return "refactor"
    if FEATFIX_RE.match(subject):
        return "featfix"
    return "other"


# ---------------------------------------------------------------------------
# Walking the history
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _Edit:
    sha: str
    subject: str
    kind: str
    assertion: bool
    setup: bool


@dataclasses.dataclass
class _Record:
    key: str
    """Canonical path (stable across renames) of the file holding the test."""
    name: str
    edits: list[_Edit] = dataclasses.field(default_factory=list)
    test_id: str = ""
    gone: bool = False

    def _count(self, kind: str, *, assertion: bool = False) -> int:
        return sum(
            1 for e in self.edits if e.kind == kind and (e.assertion or not assertion)
        )

    @property
    def refactor_edits(self) -> int:
        return self._count("refactor")

    @property
    def refactor_assertion_edits(self) -> int:
        return self._count("refactor", assertion=True)

    @property
    def featfix_edits(self) -> int:
        return self._count("featfix")

    @property
    def other_edits(self) -> int:
        return self._count("other")

    @property
    def total_edits(self) -> int:
        return len(self.edits)


def _is_py(path: str | None) -> bool:
    """``tests/`` also holds fixtures, goldens and docs; only Python is parsed."""
    return path is not None and path.endswith(".py")


def _key(canon: dict[str, str], path: str | None) -> str | None:
    if path is None:
        return None
    return canon.get(path, path)


def _mark(
    touched: dict[tuple[str, str], set[str]],
    key: str,
    src: _Source,
    lines: range,
    existing: set[str] | None,
) -> None:
    """Attribute each changed line's label to its innermost enclosing test.

    ``existing`` restricts attribution to tests that were already present on
    that side, so a test the commit created is never counted as edited.
    """
    if src.broken:
        return
    for line in lines:
        label = src.label(line)
        if label is None:
            continue
        name = src.test_at(line)
        if name is None or (existing is not None and name not in existing):
            continue
        touched.setdefault((key, name), set()).add(label)


def _walk(
    blobs: _Blobs, commits: list[_Commit]
) -> tuple[dict[tuple[str, str], _Record], dict[str, str], int]:
    """Attribute every commit's hunks. Returns records, path map and unparsed count."""
    canon: dict[str, str] = {}
    records: dict[tuple[str, str], _Record] = {}
    unparsed = 0
    for commit in commits:
        kind = _kind(commit.subject)
        touched: dict[tuple[str, str], set[str]] = {}
        #: Body digests before the commit, by (key, name); after it, by name.
        old_bodies: dict[tuple[str, str], str] = {}
        new_bodies: dict[str, set[str]] = {}
        for sec in commit.sections:
            old_key = _key(canon, sec.old)
            if sec.old and sec.new and sec.old != sec.new:
                canon[sec.new] = canon.pop(sec.old, sec.old)
            elif sec.old and not sec.new:
                canon.pop(sec.old, None)
            elif sec.new and not sec.old and sec.new in canon.values():
                # A new file reusing the name of a live file's canonical key.
                canon[sec.new] = f"{sec.new}@{commit.sha[:10]}"
            new_key = _key(canon, sec.new)
            if not sec.hunks:
                continue
            old_src = (
                blobs.source(commit.parent, sec.old)
                if commit.parent and _is_py(sec.old)
                else None
            )
            new_src = blobs.source(commit.sha, sec.new) if _is_py(sec.new) else None
            unparsed += sum(1 for s in (old_src, new_src) if s and s.broken)
            existing = set(old_src.tests) if old_src else set()
            if new_src is not None and new_key is not None:
                for name, digest in new_src.bodies.items():
                    new_bodies.setdefault(name, set()).add(digest)
            if old_src is not None and old_key is not None:
                for name, digest in old_src.bodies.items():
                    old_bodies[(old_key, name)] = digest
            for old_range, new_range in sec.hunks:
                if old_src is not None and old_key is not None:
                    _mark(touched, old_key, old_src, old_range, None)
                if new_src is not None and new_key is not None:
                    _mark(touched, new_key, new_src, new_range, existing)
        # A test whose body survives the commit under the same name is a move, not
        # an edit: its removed lines would otherwise read as assertion edits to a
        # test that did not change. A body that changed matches nothing, so it stays.
        for entry in [
            e for e in touched if old_bodies.get(e) in new_bodies.get(e[1], ())
        ]:
            del touched[entry]
        for (key, name), labels in touched.items():
            record = records.setdefault((key, name), _Record(key, name))
            record.edits.append(
                _Edit(
                    commit.sha,
                    commit.subject,
                    kind,
                    assertion="assertion" in labels,
                    setup="setup" in labels,
                )
            )
    return records, canon, unparsed


def _resolve(
    blobs: _Blobs,
    head: str,
    records: dict[tuple[str, str], _Record],
    canon: dict[str, str],
) -> None:
    """Give each record its HEAD identity, or mark it gone."""
    head_files = [
        p
        for p in _git(
            blobs.repo, "ls-tree", "-r", "--name-only", head, "--", TESTS_PATH
        ).split("\n")
        if p.endswith(".py")
    ]
    live_by_key = {_key(canon, p): p for p in head_files}
    for record in records.values():
        path = live_by_key.get(record.key)
        src = blobs.source(head, path) if path else None
        if path and src and not src.broken and record.name in src.tests:
            record.test_id = f"{path}::{record.name}"
        else:
            record.test_id = f"{path or record.key}::{record.name}"
            record.gone = True


def _rank(records: list[_Record]) -> list[_Record]:
    return sorted(
        records,
        key=lambda r: (
            -r.refactor_assertion_edits,
            -r.refactor_edits,
            -r.total_edits,
            r.test_id,
        ),
    )


def _prepare_history(repo: pathlib.Path, unshallow: bool) -> dict[str, object]:
    """Make the history complete, or say that it is not."""
    if _git(repo, "rev-parse", "--is-shallow-repository").strip() != "true":
        return {"shallow": False, "unshallowed": False, "note": None}
    if not unshallow:
        return {
            "shallow": True,
            "unshallowed": False,
            "note": "shallow history, not fetched (--no-unshallow): results cover "
            "only the commits present",
        }
    proc = _run(repo, "fetch", "--unshallow")
    if proc.returncode == 0:
        return {
            "shallow": True,
            "unshallowed": True,
            "note": "history was shallow; fetched it with `git fetch --unshallow` "
            "before analysing",
        }
    return {
        "shallow": True,
        "unshallowed": False,
        "note": "shallow history and `git fetch --unshallow` failed "
        f"({proc.stderr.strip()}): results cover only the commits present",
    }


@dataclasses.dataclass
class Report:
    since: str
    head: str
    history: dict[str, object]
    commits_scanned: int
    commits_touching_tests: int
    refactor_commits: int
    unparsed_files: int
    #: Tests at HEAD, ranked. Includes tests with feat/fix edits only.
    tests: list[_Record]
    #: Tests that no longer exist at HEAD, ranked.
    gone: list[_Record]


def analyse(repo: pathlib.Path, since: str, *, unshallow: bool = True) -> Report:
    repo = repo.resolve()
    history = _prepare_history(repo, unshallow)
    head = _git(repo, "rev-parse", "HEAD").strip()
    commits = _parse_log(
        _git(
            repo,
            "log",
            "--reverse",
            "--no-merges",
            "-M",
            "-U0",
            "-p",
            "--no-color",
            "--no-ext-diff",
            f"--format={SEP}%H %P{FIELD}%s",
            f"--since={since}",
            head,
            "--",
            TESTS_PATH,
        )
    )
    subjects = _git(
        repo, "log", "--no-merges", f"--since={since}", "--format=%s", head
    ).split("\n")
    commits_scanned = int(
        _git(repo, "rev-list", "--count", "--no-merges", f"--since={since}", head)
    )
    with _Blobs(repo) as blobs:
        records, canon, unparsed = _walk(blobs, commits)
        _resolve(blobs, head, records, canon)
    all_records = list(records.values())
    return Report(
        since=since,
        head=head,
        history=history,
        commits_scanned=commits_scanned,
        commits_touching_tests=len(commits),
        refactor_commits=sum(1 for s in subjects if REFACTOR_RE.match(s)),
        unparsed_files=unparsed,
        tests=_rank([r for r in all_records if not r.gone]),
        gone=_rank([r for r in all_records if r.gone]),
    )


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _tag(edit: _Edit) -> str:
    if edit.kind == "refactor":
        return "R/assert" if edit.assertion else "R/setup"
    return {"featfix": "F", "other": "O"}[edit.kind]


def _commit_cell(edits: list[_Edit], limit: int = 6) -> str:
    cells = [f"`{e.sha[:9]}` {_tag(e)}" for e in edits[:limit]]
    if len(edits) > limit:
        cells.append(f"+{len(edits) - limit} more")
    return ", ".join(cells)


def _table(records: list[_Record]) -> list[str]:
    rows = [
        "| # | Test | Refactor edits | Assertion edits (refactor) | feat/fix edits | Commits |",
        "|--:|---|--:|--:|--:|---|",
    ]
    for rank, r in enumerate(records, 1):
        rows.append(
            f"| {rank} | `{r.test_id}` | {r.refactor_edits} | "
            f"{r.refactor_assertion_edits} | {r.featfix_edits} | {_commit_cell(r.edits)} |"
        )
    return rows


def render_markdown(report: Report, top: int) -> str:
    live = [r for r in report.tests if r.refactor_edits]
    gone = [r for r in report.gone if r.refactor_edits]
    note = report.history["note"] or "full history (not shallow)"
    out = [
        "# Test refactor churn",
        "",
        f"Window: commits since `{report.since}` up to `{report.head[:10]}`: "
        f"{report.commits_scanned} non-merge commits, "
        f"{report.commits_touching_tests} touching `{TESTS_PATH}/`, "
        f"{report.refactor_commits} refactor/perf/style commits (all paths).",
        "",
        f"History: {note}.",
    ]
    if report.unparsed_files:
        out.append(
            f"Unparsed file revisions skipped: {report.unparsed_files} (their "
            "tests are not counted)."
        )
    out += [
        "",
        "Ranked by assertion edits made by refactor/perf/style commits, then by "
        f"refactor edits. Showing top {min(top, len(live))} of {len(live)} tests "
        "with a refactor edit.",
        "",
        *_table(live[:top]),
        "",
        "Commit tags: `R/assert` refactor commit that changed an assertion or "
        "expected value; `R/setup` refactor commit that changed setup only; "
        "`F` feat/fix; `O` other.",
        "",
        "## Tests no longer at HEAD",
        "",
        f"{len(gone)} such tests had a refactor edit. Reported separately: they "
        "cannot be fixed by editing them.",
        "",
        *(_table(gone[:top]) if gone else ["_None._"]),
        "",
    ]
    return "\n".join(out)


def _entry(record: _Record) -> dict[str, object]:
    return {
        "test": record.test_id,
        "gone": record.gone,
        "refactor_edits": record.refactor_edits,
        "refactor_assertion_edits": record.refactor_assertion_edits,
        "featfix_edits": record.featfix_edits,
        "other_edits": record.other_edits,
        "total_edits": record.total_edits,
        "commits": [dataclasses.asdict(e) for e in record.edits],
    }


def to_json(report: Report) -> dict[str, object]:
    return {
        "since": report.since,
        "head": report.head,
        "history": report.history,
        "commits_scanned": report.commits_scanned,
        "commits_touching_tests": report.commits_touching_tests,
        "refactor_commits": report.refactor_commits,
        "unparsed_files": report.unparsed_files,
        "tests": [_entry(r) for r in report.tests],
        "gone": [_entry(r) for r in report.gone],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since",
        default="1 year ago",
        help='Window start, any git --since value (default: "1 year ago").',
    )
    parser.add_argument(
        "--repo",
        type=pathlib.Path,
        default=pathlib.Path("."),
        help="Repository to analyse (default: the current directory).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Rows per markdown table (default: 20). JSON always holds every test.",
    )
    parser.add_argument(
        "--json",
        type=pathlib.Path,
        help="Also write the full listing to this path as JSON.",
    )
    parser.add_argument(
        "--no-unshallow",
        action="store_true",
        help="Do not run `git fetch --unshallow` on a shallow clone; report it instead.",
    )
    args = parser.parse_args(argv)

    try:
        report = analyse(args.repo, args.since, unshallow=not args.no_unshallow)
    except ChurnError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(render_markdown(report, args.top))
    if args.json:
        args.json.write_text(
            json.dumps(to_json(report), indent=1) + "\n", encoding="utf-8"
        )
    # Always 0 once the analysis runs: this reports for a human, it does not gate.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
