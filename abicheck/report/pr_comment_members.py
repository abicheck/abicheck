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

"""Bounding how much member-report work one aggregate document may ask for.

:mod:`abicheck.report.pr_comment_aggregate` already refuses a *hostile
path* and a *hostile size* per member. Neither bounds how many times a
document may ask: the two are independent, and a document repeating one
permitted 32 MiB member path ten thousand times stays small itself while
asking a privileged publisher for hundreds of gigabytes of parsing. The
artifact extractor cannot see that amplification -- the document is inside
every limit it enforces -- so the bound belongs here, at the point the work
is actually requested.

Kept out of the fold module because it is a different question. The fold
decides what a document *means*; this decides what may be read on its
behalf, and only the second is a trust boundary. It takes the loader as an
argument rather than importing it, so the dependency runs one way.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TypeVar

from ..pr_comment_base import CommentModel, Finding

#: How many targets one document may have folded. Real fan-outs are a build
#: matrix -- tens of targets, not thousands -- so this sits far above any
#: honest document and is still a bound. Targets past it are not silently
#: dropped; the caller states the shortfall as a limitation, like every
#: other refusal in this fan-in.
MAX_FOLDED_TARGETS = 256

#: How many *distinct* member reports may be read. Repeats beyond this are
#: served from the cache, so this bounds real I/O rather than target count.
MAX_DISTINCT_MEMBER_READS = 256

#: What one member load produced: a model, a refusal, or neither.
_Loaded = TypeVar("_Loaded")


def cached_load_member(
    cache: dict[str, _Loaded],
    loader: Callable[[], _Loaded],
    *,
    target: Mapping[str, object],
    base_dir: object,
    on_budget_exhausted: Callable[[], _Loaded],
) -> _Loaded:
    """*loader*'s result for *target*, reading each distinct member once.

    The cache key is the target's ``report_path`` **as written**, qualified
    by the base directory it resolves against -- never the resolved path,
    which an unreadable or refused entry may not have. A refusal is cached
    as itself, so a document naming one bad path a thousand times pays for
    one attempt.

    A target naming no path is not cached at all: there is nothing to key
    on, and such a target is refused by the loader on its own terms.
    """
    raw_path = str(target.get("report_path", "") or "")
    if not raw_path:
        return loader()
    key = f"{base_dir}::{raw_path}"
    if key in cache:
        return cache[key]
    if len(cache) >= MAX_DISTINCT_MEMBER_READS:
        return on_budget_exhausted()
    result = loader()
    cache[key] = result
    return result


#: Ceiling on one member report's on-disk size. A fan-in comment reads as
#: many of these as the document names, from a directory that may have been
#: unpacked from a contributor-produced artifact, so "just read it" is a
#: memory-exhaustion primitive. 32 MiB is far above any real ``compare``
#: JSON (the largest in this repository's own corpora are single-digit MiB)
#: and far below anything that threatens a runner.
MEMBER_REPORT_MAX_BYTES = 32 * 1024 * 1024


class MemberReportRefused(Exception):
    """A member report named by the aggregate document was not loaded.

    Carries the reviewer-facing reason verbatim: every refusal reaches the
    rendered comment as a limitation, so the message is written for the
    person reading the PR, not for a log.
    """


# ---------------------------------------------------------------------------
# Member-report loading — the untrusted-input boundary
# ---------------------------------------------------------------------------


def _reject_absolute(raw: str) -> None:
    """Refuse an absolute member path under *either* platform's rules.

    ``PurePosixPath("C:/x").is_absolute()`` is ``False`` and
    ``PureWindowsPath("/x").is_absolute()`` is ``False`` too, so a check
    written against only the running platform's flavour lets the other
    platform's absolute spelling through. An aggregate document produced on
    a Windows runner and rendered on a Linux one is an ordinary
    configuration, not a contrived one.
    """
    if PurePosixPath(raw).is_absolute() or PureWindowsPath(raw).is_absolute():
        raise MemberReportRefused(
            f"its report_path `{raw}` is absolute; member reports are read only "
            "from the aggregate document's own directory"
        )
    if PureWindowsPath(raw).drive:
        raise MemberReportRefused(
            f"its report_path `{raw}` names a drive; member reports are read "
            "only from the aggregate document's own directory"
        )


def resolve_member_path(base_dir: Path, raw: str) -> Path:
    """The on-disk path of a member report, or refuse with a stated reason.

    Containment is established twice, deliberately, because the two checks
    fail on different inputs. The *component walk* refuses a symlink at any
    level -- including one whose target is inside *base_dir*, since a
    symlink in a contributor-supplied tree is a redirection primitive
    whatever it currently points at, and including the final component,
    which ``Path.is_file()`` would happily follow. The *resolved-prefix*
    check then refuses anything that still lands outside *base_dir* --
    ``..`` segments, and the case where *base_dir* itself is reached through
    a symlink.

    Neither check alone is sufficient: a pure prefix comparison accepts a
    symlink pointing back inside the tree (a file the document did not
    actually name), and a pure symlink walk accepts ``a/../../etc/passwd``.
    """
    if not raw:
        raise MemberReportRefused(
            "the aggregate document records no report_path for it"
        )
    _reject_absolute(raw)
    parts = [p for p in PurePosixPath(raw.replace("\\", "/")).parts if p != "."]
    if any(part == ".." for part in parts):
        raise MemberReportRefused(
            f"its report_path `{raw}` traverses out of the aggregate document's "
            "directory"
        )
    if not parts:
        raise MemberReportRefused(f"its report_path `{raw}` names no file")
    current = base_dir
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise MemberReportRefused(
                f"its report_path `{raw}` passes through the symlink "
                f"`{part}`; member reports must be plain files under the "
                "aggregate document's directory"
            )
    try:
        resolved_base = base_dir.resolve(strict=False)
        resolved = current.resolve(strict=False)
        resolved.relative_to(resolved_base)
    except (OSError, ValueError) as exc:
        raise MemberReportRefused(
            f"its report_path `{raw}` does not resolve inside the aggregate "
            "document's directory"
        ) from exc
    return current


def load_member_report(
    base_dir: Path,
    raw: str,
    *,
    max_bytes: int = MEMBER_REPORT_MAX_BYTES,
) -> dict[str, object]:
    """Load one member report, or raise :class:`MemberReportRefused`.

    Every failure mode -- refused path, missing file, non-regular file,
    oversized file, unreadable bytes, malformed JSON, a JSON value that is
    not an object -- raises with a reviewer-facing reason. None of them
    returns an empty report, because an empty report is indistinguishable
    from a clean one by the time it reaches a bucket.
    """
    path = resolve_member_path(base_dir, raw)
    try:
        stat = path.stat()
    except OSError as exc:
        raise MemberReportRefused(
            f"its report `{raw}` could not be read ({exc.strerror or exc})"
        ) from exc
    if not path.is_file():
        raise MemberReportRefused(f"its report_path `{raw}` is not a regular file")
    if stat.st_size > max_bytes:
        raise MemberReportRefused(
            f"its report `{raw}` is {stat.st_size} bytes, over the "
            f"{max_bytes}-byte member-report limit"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise MemberReportRefused(
            f"its report `{raw}` could not be read ({exc})"
        ) from exc
    except json.JSONDecodeError as exc:
        raise MemberReportRefused(
            f"its report `{raw}` is not valid JSON ({exc})"
        ) from exc
    if not isinstance(data, dict):
        raise MemberReportRefused(f"its report `{raw}` is not a JSON object")
    return data


# ---------------------------------------------------------------------------
# Reading the aggregate document
# ---------------------------------------------------------------------------


def _limitation(symbol: str, detail: str, component: str = "") -> Finding:
    """One analysis-incomplete finding.

    Carries no ``severity``/``category`` on purpose: these are not
    compatibility claims, and stamping one would make them eligible for the
    severity-driven blocking derivations that belong to real findings. The
    aggregate document's own axes decide what blocks (see
    :func:`build_aggregate_model`).
    """
    return Finding(
        kind="aggregate_limitation",
        symbol=symbol,
        detail=detail,
        component=component,
    )


def _load_member(
    target: Mapping[str, object],
    tid: str,
    *,
    build_member_model: Callable[[dict[str, object]], CommentModel],
    base_dir: Path | None,
    max_member_bytes: int,
) -> tuple[CommentModel | None, Finding | None]:
    """One member's model, or ``(None, limitation)`` stating why not.

    Every failure becomes a *limitation*, never an omission and never an
    exception that escapes: one member written in a shape this build cannot
    model (a newer schema, a retired one) must not cost every other target's
    result too.
    """
    if base_dir is None:
        return None, _limitation(
            tid,
            "Per-target detail is unavailable: the aggregate document's own "
            "directory is not known to this renderer, so no member report "
            "was read.",
            tid,
        )
    raw_path = target.get("report_path")
    try:
        data = load_member_report(
            base_dir,
            str(raw_path) if raw_path is not None else "",
            max_bytes=max_member_bytes,
        )
        return build_member_model(data), None
    except MemberReportRefused as exc:
        return None, _limitation(tid, f"Per-target detail is unavailable: {exc}", tid)
    except Exception as exc:  # noqa: BLE001 - see this function's docstring
        return None, _limitation(
            tid,
            f"Per-target detail is unavailable: its report could not be "
            f"rendered ({type(exc).__name__}: {exc})",
            tid,
        )
