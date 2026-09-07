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

"""Changed-path localization: the one owner of the ``--since``/``--changed-path``
seed and of the replay-scope rule it implies (ADR-035 D7, ADR-043 D7).

``docs/contribute/plans/one-comparison-product.md`` §3 #12 moves this input
from ``scan`` onto ``compare``. Both front ends resolve the seed through
:func:`resolve_changed_seed` and both derive the L4/L5 replay scope through
:func:`localized_collect_mode`, so the two commands cannot drift on what a
seed *is* (``--changed-path`` wins; else ``--since`` via ``git diff``; else
none) or on what it *does*.

Deliberately click-free: warnings are reported through an injected *notify*
callable, so this stays an engine-layer leaf both the CLI adapters and the
typed API can call (ADR-061 dependency direction).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

#: The ``--depth source`` collect mode a *seeded* run narrows to, and the
#: unseeded one it narrows *from*. ADR-043 D7: a source-depth run uses
#: changed-path scope when a seed is given, else the current library target --
#: never a zero-TU no-op. ``source-changed`` with an empty seed would be
#: exactly that no-op, which is why the mapping below is keyed on a *non-empty*
#: changed-path set rather than on the flag having been typed.
_TARGET_COLLECT_MODE = "source-target"
_CHANGED_COLLECT_MODE = "source-changed"


@dataclass(frozen=True)
class ChangedPathSeed:
    """A resolved changed-path seed.

    ``seeded`` tracks whether a *valid* seed was produced -- a successful but
    empty ``git diff`` (a no-op PR) is seeded with no paths, which is distinct
    from a missing/failed seed. Only the former lets a risk-driven ``auto``
    depth pick the cheapest rung; the latter must fall back to the broad
    preset (ADR-035 D7).
    """

    paths: tuple[str, ...] = ()
    #: Human-readable provenance of the seed, for report/advisory text.
    source: str = "none (no diff seed; broad scope)"
    seeded: bool = False


def git_changed_paths(
    since: str,
    cwd: Path | None,
    *,
    notify: Callable[[str], None] | None = None,
) -> list[str] | None:
    """Paths changed vs. a git ref via ``git diff --name-only`` (no shell).

    Returns the changed-path list on success (possibly **empty** for a no-op
    diff), or ``None`` when the seed could not be produced (missing git /
    non-repo / bad ref). The caller distinguishes the two -- see
    :class:`ChangedPathSeed`.
    """
    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only", f"{since}...HEAD"],
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if notify is not None:
            notify(f"warning: --since: could not run git diff: {exc}")
        return None
    if proc.returncode != 0:
        if notify is not None:
            notify(
                f"warning: --since {since!r}: git diff failed "
                f"({proc.stderr.strip() or 'non-zero exit'}); scanning broadly."
            )
        return None
    return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]


def resolve_changed_seed(
    changed_paths_opt: tuple[str, ...] | list[str],
    since: str | None,
    cwd: Path | None,
    *,
    notify: Callable[[str], None] | None = None,
) -> ChangedPathSeed:
    """Resolve ``--changed-path``/``--since`` into one :class:`ChangedPathSeed`.

    ``--changed-path`` wins (it is the caller stating the diff outright); else
    ``--since`` is resolved through ``git diff`` from *cwd* (the run's source
    tree, when it has one); else there is no seed.
    """
    if changed_paths_opt:
        return ChangedPathSeed(tuple(changed_paths_opt), "--changed-path", True)
    if since:
        git_changed = git_changed_paths(since, cwd, notify=notify)
        if git_changed is None:
            return ChangedPathSeed(
                (), f"--since {since} (seed failed; broad scope)", False
            )
        return ChangedPathSeed(tuple(git_changed), f"--since {since}", True)
    return ChangedPathSeed()


def localized_collect_mode(
    collect_mode: str, changed_paths: tuple[str, ...] | list[str]
) -> str:
    """Narrow a resolved collect mode to changed-path scope, when there is one.

    ADR-043 D7's rule, stated once: a ``--depth source`` run narrows its L4
    replay (and the L5 call-graph pass that rides on it) to the changed
    translation units **only** when a non-empty changed-path set was actually
    resolved; with no seed it keeps the current library target. Every other
    mode is returned unchanged -- ``graph-full`` is a deliberately full-scope
    request, and ``off``/``build``/``graph-build`` run no replay to narrow.
    """
    if changed_paths and collect_mode == _TARGET_COLLECT_MODE:
        return _CHANGED_COLLECT_MODE
    return collect_mode
