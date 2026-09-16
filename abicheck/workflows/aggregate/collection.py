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

"""Assemble ``aggregate``'s two inputs from **one** declaration of the run.

:func:`abicheck.workflows.aggregate.execute` needs two things that must agree:
a directory holding this run's per-check reports, and an expected-target
manifest saying which checks the run was *supposed* to produce. Building them
separately is how a matrix job ends up reporting "no changes found" for a check
that never ran -- the report is simply absent from the directory, and nothing
declared that it should have been there.

This module builds both from a single list of declared checks, and then
validates the document ``aggregate`` produced from them. Three rules here exist
because each has a real failure behind it:

* **The manifest is never written into the reports directory.** ``aggregate``
  discovers reports by globbing ``*.json``, so a manifest (or a previous run's
  ``aggregate.json``) sitting in that directory is picked up as an extra,
  unexpected target. :func:`collect_reports` refuses a destination holding any
  ``.json`` it did not itself place.
* **A check's identity is never re-derived from its filename.** A report that
  records its own ``target_id`` is authoritative; the declaration must agree
  with it, and a disagreement is a refusal rather than a silent re-label. Only
  a report carrying no ``target_id`` at all falls back to the declared id --
  which is then written into the filename verbatim, because
  :func:`~abicheck.workflows.aggregate.load.target_id_from_path` reads the stem
  back and any "sanitization" of the ``@``/``#``/``~`` separators would make
  every report aggregate as an unavailable target.
* **A check that produced no report is declared, not dropped.** It goes into
  the manifest and stays absent from the directory, which is exactly how
  ``aggregate`` reports an unavailable required target. Silently shortening the
  expected set turns a failed analysis into a clean one.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .contracts import DEFAULT_REPORT_PREFIX, CoverageStatus, parse_check_id

__all__ = [
    "AggregateValidationError",
    "CollectionError",
    "CollectionResult",
    "DeclaredCheck",
    "collect_reports",
    "parse_check_declaration",
    "validate_aggregate_document",
]

#: A declared check id becomes a filename (``abi-report-<id>.json``), so it may
#: not carry a path separator or a traversal segment. Everything the
#: ``check_id`` grammar itself uses (``@``, ``#``, ``~``, ``!``, ``.``, ``-``,
#: ``_``) is deliberately allowed: those are the separators that carry the
#: identity, and stripping them is the bug this module exists to prevent.
_ALLOWED_ID = re.compile(r"^[A-Za-z0-9@#~!._+-]+$")


class CollectionError(Exception):
    """A declaration could not be turned into a coherent aggregate input."""


class AggregateValidationError(Exception):
    """A document does not describe a real aggregate outcome."""


@dataclass(frozen=True)
class DeclaredCheck:
    """One check this run was supposed to produce."""

    id: str
    #: Where this check's report was written, or ``None`` when it produced
    #: none. ``None`` is a first-class state, not an error: it is how an
    #: expected-but-unavailable check reaches ``aggregate``.
    report: Path | None = None
    required: bool = True


@dataclass
class CollectionResult:
    """What :func:`collect_reports` placed, and what it could not."""

    reports_dir: Path
    manifest_path: Path
    present: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    #: ``check id -> why its report could not be collected`` for a declared
    #: report that exists but is unusable (unreadable, not JSON, not an
    #: object). Distinct from :attr:`missing`: the check ran and produced
    #: something, and losing that distinction is how a corrupted report reads
    #: as "never ran". The check is still declared expected, so the aggregate
    #: reports it as unavailable either way.
    unusable: dict[str, str] = field(default_factory=dict)

    @property
    def expected(self) -> int:
        return len(self.present) + len(self.missing) + len(self.unusable)


def parse_check_declaration(raw: Any) -> list[DeclaredCheck]:
    """Parse the declared check list.

    Accepts a JSON array of objects (``{"id", "report", "required"}``), which
    is the only shape a caller should write. A ``report`` of ``""``/``null``
    means "expected, produced nothing".
    """
    if not isinstance(raw, list) or not raw:
        raise CollectionError(
            "the check declaration must be a non-empty JSON array of "
            '{"id", "report", "required"} objects -- an empty declaration '
            "would aggregate zero targets, which reads as a clean run rather "
            "than as an analysis that never happened."
        )
    checks: list[DeclaredCheck] = []
    seen: dict[str, int] = {}
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise CollectionError(f"check {index} must be an object, got {entry!r}")
        unknown = set(entry) - {"id", "report", "required"}
        if unknown:
            raise CollectionError(
                f"check {index} has unrecognized field(s) "
                f"{', '.join(sorted(unknown))} -- a misspelled 'required' would "
                "silently downgrade a mandatory check to an optional one."
            )
        check_id = entry.get("id")
        if not isinstance(check_id, str) or not check_id:
            raise CollectionError(f"check {index} has no usable 'id'")
        if not _ALLOWED_ID.match(check_id) or ".." in check_id:
            raise CollectionError(
                f"check id {check_id!r} contains a character that cannot appear in "
                "a report filename (a path separator, a traversal segment, or a "
                "control character). The check_id separators @ # ~ ! are allowed "
                "and are never stripped."
            )
        if check_id in seen:
            raise CollectionError(
                f"check id {check_id!r} is declared twice (checks {seen[check_id]} "
                f"and {index}). Two declarations of one check cannot both be "
                "reported, and the second would overwrite the first's report."
            )
        seen[check_id] = index
        required = entry.get("required", True)
        if not isinstance(required, bool):
            raise CollectionError(
                f"check {check_id!r}: 'required' must be a boolean, got {required!r}"
            )
        raw_report = entry.get("report")
        if raw_report is not None and not isinstance(raw_report, str):
            raise CollectionError(
                f"check {check_id!r}: 'report' must be a string path or null, "
                f"got {raw_report!r}"
            )
        report = Path(raw_report) if raw_report else None
        checks.append(DeclaredCheck(id=check_id, report=report, required=required))
    return checks


def _authoritative_id(path: Path) -> tuple[str | None, str | None]:
    """``(target_id, problem)`` for a declared report file.

    ``target_id`` is ``None`` when the report simply records none -- the
    ordinary case for a plain ``compare`` report that never went through the
    ``check-target`` pipeline, and not a problem.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return None, f"cannot be read: {exc}"
    except json.JSONDecodeError as exc:
        return None, f"is not valid JSON: {exc}"
    if not isinstance(document, dict):
        return None, f"is not a JSON object (got {type(document).__name__})"
    recorded = document.get("target_id")
    if recorded is None:
        return None, None
    if not isinstance(recorded, str) or not recorded:
        return None, f"records an unusable target_id {recorded!r}"
    return recorded, None


def _write_json_atomic(path: Path, payload: Any) -> None:
    """Write *payload* to *path* via a same-directory temp file and rename.

    A partially-written expected-target manifest is worse than none: it is
    still a file, ``aggregate`` still reads it, and it declares a *shorter*
    expected set than the run actually had.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    )
    try:
        with handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def collect_reports(
    checks: list[DeclaredCheck],
    *,
    reports_dir: Path,
    manifest_path: Path,
    gate: dict[str, Any] | None = None,
    prefix: str = DEFAULT_REPORT_PREFIX,
) -> CollectionResult:
    """Place *checks*' reports in *reports_dir* and declare them in *manifest_path*.

    Never raises for a check that produced nothing or produced something
    unreadable: those are recorded on the result and still declared expected,
    so one component's failure never costs the others their diagnostics.
    Raises :class:`CollectionError` only for a declaration that cannot be
    honoured at all -- a manifest inside the reports directory, a foreign
    document already sitting in it, or two checks resolving to one identity.
    """
    reports_dir = Path(reports_dir)
    manifest_path = Path(manifest_path)
    reports_dir.mkdir(parents=True, exist_ok=True)

    resolved_dir = reports_dir.resolve()
    if manifest_path.resolve().parent == resolved_dir:
        raise CollectionError(
            f"the expected-target manifest {manifest_path} would be written inside "
            f"the reports directory {reports_dir}. aggregate discovers reports by "
            "globbing *.json there, so the manifest would be picked up as an extra, "
            "unexpected target. Put it anywhere else."
        )

    # A foreign document is refused before anything is touched -- a stale
    # aggregate.json is a target this run never declared.
    foreign = [
        existing.name
        for existing in sorted(resolved_dir.glob("*.json"))
        if not existing.name.startswith(prefix)
    ]
    if foreign:
        raise CollectionError(
            f"the reports directory {reports_dir} already holds "
            f"{', '.join(foreign)}, which aggregate would read as additional "
            "targets. Collect into a directory this run owns."
        )

    result = CollectionResult(reports_dir=reports_dir, manifest_path=manifest_path)
    placed: dict[str, str] = {}

    # Stage first, clear second, move third. Clearing the destination up front
    # is the obvious order and is wrong: a caller may legitimately declare a
    # report that already lives in the reports directory under this prefix --
    # a re-run of collection over its own output, or a producer that wrote
    # straight into it -- and deleting it before the copy loop destroys the
    # input, after which `is_file()` is False and the check records as
    # *missing*. A real result would then aggregate as an unavailable target
    # with no error raised anywhere, which is the exact failure this module
    # exists to prevent. Staging also means a report the copy loop rejects
    # never leaves a half-cleared directory behind.
    staging = Path(
        tempfile.mkdtemp(dir=resolved_dir.parent, prefix=".abicheck-collect-")
    )
    try:
        for check in checks:
            if check.report is None or not check.report.is_file():
                result.missing.append(check.id)
                continue
            recorded, problem = _authoritative_id(check.report)
            if problem is not None:
                result.unusable[check.id] = f"{check.report} {problem}"
                continue
            if recorded is not None and recorded != check.id:
                raise CollectionError(
                    f"check {check.id!r} was declared for {check.report}, but that "
                    f"report records its own target_id {recorded!r}. The report's "
                    "recorded identity is authoritative; collecting it under a "
                    "different id would report one check's result as another's. Fix "
                    "the declaration to match."
                )
            identity = recorded or check.id
            if identity in placed:
                raise CollectionError(
                    f"checks {placed[identity]!r} and {check.id!r} both resolve to the "
                    f"identity {identity!r}."
                )
            placed[identity] = check.id
            shutil.copyfile(check.report, staging / f"{prefix}{identity}.json")
            result.present.append(check.id)

        # Only now: drop reports a previous run of this same collection left,
        # so a target that is no longer declared does not linger as an extra.
        for existing in sorted(resolved_dir.glob("*.json")):
            if existing.name.startswith(prefix):
                existing.unlink()
        for staged in sorted(staging.iterdir()):
            os.replace(staged, resolved_dir / staged.name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    manifest: dict[str, Any] = {
        "targets": [
            {"id": check.id, "required": check.required}
            for check in sorted(checks, key=lambda c: c.id)
        ]
    }
    if gate:
        manifest["gate"] = gate
    _write_json_atomic(manifest_path, manifest)
    return result


# ── outcome validation ──────────────────────────────────────────────────────

#: ``aggregate``'s own target states (``fold``'s ``targets[].state``).
_TARGET_STATES = frozenset({"analyzed", "unavailable"})
_STATUSES = frozenset({"pass", "fail"})
_COVERAGE_STATUSES = frozenset(status.value for status in CoverageStatus)


def validate_aggregate_document(document: Any, *, expected: int | None = None) -> None:
    """Raise :class:`AggregateValidationError` unless *document* is a real outcome.

    Checking that one key is present is not validation: a file carrying only an
    ``aggregate_schema_version`` string would pass while describing nothing, and
    the whole point of validating here is that **operational report loss must
    not reach a publisher disguised as an empty finding set**.

    Every enumerated value is checked against the vocabulary
    :mod:`~abicheck.workflows.aggregate.contracts` itself defines, so this
    cannot drift from what ``aggregate`` emits the way a hand-copied list of
    status strings does. *expected*, when given, additionally requires the
    document to describe that many targets -- the number the caller *declared*,
    which is the one thing the document itself cannot know it is short of.
    """
    if not isinstance(document, dict):
        raise AggregateValidationError(
            f"not a JSON object (got {type(document).__name__})"
        )
    version = document.get("aggregate_schema_version")
    if not isinstance(version, str) or not version.strip():
        raise AggregateValidationError("declares no aggregate_schema_version")
    status = document.get("status")
    if status not in _STATUSES:
        raise AggregateValidationError(f"status is not pass/fail: {status!r}")
    for block in ("compatibility", "coverage", "gate"):
        if not isinstance(document.get(block), dict):
            raise AggregateValidationError(f"has no {block} block")
    coverage_status = document["coverage"].get("status")
    if coverage_status not in _COVERAGE_STATUSES:
        raise AggregateValidationError(
            f"coverage status {coverage_status!r} is not one of "
            f"{sorted(_COVERAGE_STATUSES)}"
        )
    targets = document.get("targets")
    if not isinstance(targets, list):
        raise AggregateValidationError("has no targets list")
    if not targets:
        # Zero targets means the expected set never reached aggregation --
        # an operational failure of the producing job, not a clean comparison.
        raise AggregateValidationError("declares no targets at all")
    seen: set[str] = set()
    for target in targets:
        if not isinstance(target, dict):
            raise AggregateValidationError("has a target that is not an object")
        target_id = target.get("target_id")
        if not isinstance(target_id, str) or not target_id:
            raise AggregateValidationError("has a target with no target_id")
        if target_id in seen:
            raise AggregateValidationError(f"declares target {target_id!r} twice")
        seen.add(target_id)
        if target.get("state") not in _TARGET_STATES:
            raise AggregateValidationError(
                f"target {target_id!r} has an unrecognised state "
                f"{target.get('state')!r}"
            )
    if expected is not None and len(targets) != expected:
        raise AggregateValidationError(
            f"describes {len(targets)} target(s) but {expected} were declared -- "
            "the aggregate is not reporting on the run that was actually asked for."
        )


def summarize(document: dict[str, Any]) -> dict[str, Any]:
    """The few fields a CI step branches on, read once from *document*.

    Includes the per-target roll-up by channel, because "both components"
    and "both baseline channels" are different questions and a caller that
    only sees a total cannot tell one gap from the other.
    """
    targets = document.get("targets", [])
    analyzed = [t for t in targets if t.get("state") == "analyzed"]
    channels: dict[str, dict[str, int]] = {}
    for target in targets:
        parts = parse_check_id(str(target.get("target_id", "")))
        key = parts.baseline_channel if parts else ""
        bucket = channels.setdefault(key, {"analyzed": 0, "unavailable": 0})
        bucket["analyzed" if target.get("state") == "analyzed" else "unavailable"] += 1
    return {
        "status": document.get("status", ""),
        "coverage": document.get("coverage", {}).get("status", ""),
        "targets": len(targets),
        "analyzed": len(analyzed),
        "unavailable": len(targets) - len(analyzed),
        "channels": channels,
    }
