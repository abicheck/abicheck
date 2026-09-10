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

"""Stored-`ProjectSnapshot`-package operand resolution for one release side.

One responsibility: turn a release operand that *is* a stored package
directory into the canonical-key -> `Path` map the release fan-out already
builds from a live directory of `.so` files (ADR-062 A1.7), and translate
the engine errors that can come out of it into CLI usage errors.

Split out of ``cli_compare_release_matrix.py`` (Codex review, PR #1184):
that module sits at its `architecture/debt.yaml` ``no_growth`` baseline with
no headroom, and AGENTS.md is explicit that the way to shrink such an entry
is to *move responsibility out to a properly-owned module*, never to raise
the baseline. The responsibility is a real one rather than a slice made to
fit, since ADR-068 plan Phase 7j gave it a second job beyond unpacking:
rendering the side-correct `--variant` remediation the engine deliberately
cannot (see :class:`~abicheck.errors.AmbiguousVariantSelectionError`).

It lives here, in ADR-061's ``frontends`` ring, rather than as a new flat
``cli_*`` root sibling -- ``scripts/check_architecture.py`` rejects the
latter outright (``[frozen-root-family]``/``[root-module]``), and it is
right to: this is a front end translating an engine error into a usage
error, which is exactly what ``frontends/`` owns. It sits beside
``release_dry_run.py``/``release_summary.py``, the release fan-out's other
front-end helpers.

A deliberate **import leaf**: it imports nothing from ``abicheck.cli`` or
any ``cli_*`` sibling, so the module that consumes it gains no edge into
the CLI-registration import cycle (``scripts/check_ai_readiness.py``'s
``IMPORT_CYCLE_ALLOWLIST``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def _resolve_release_package_side(
    side_dir: Path,
    variant_id: str | None,
    make_temp_dir: Callable[[str], Path],
    *,
    side: str,
) -> dict[str, Path] | None:
    """``None`` when *side_dir* is not a stored `ProjectSnapshot` package
    directory -- the caller falls back to its existing live-discovery path
    unchanged. Otherwise, *side_dir* is unpacked via
    `workflows.release_package.resolve_release_package_map` into
    the same canonical-key -> `Path` shape `_build_match_map` builds from a
    live directory of `.so` files (ADR-062 A1.7), so `_match_release_keys`'s
    own ``set(old_map) & set(new_map)`` matches a stored-side library
    against a live-side or another stored-side one by the identical key.

    *side* is ``"old"`` or ``"new"`` -- the `--variant` prefix naming *this*
    operand. It is used only to render an ambiguous-variant error's
    remediation example, which is why it lives here and not in the engine:
    see :class:`~abicheck.errors.AmbiguousVariantSelectionError`.
    """
    if not side_dir.is_dir():
        return None
    from ...workflows.release_package import resolve_release_package_map
    from ...workflows.storage import is_project_snapshot_package_dir

    if not is_project_snapshot_package_dir(side_dir):
        return None
    from ...errors import AmbiguousVariantSelectionError, SnapshotError

    dest_root = make_temp_dir("abicheck_relpkg_")
    try:
        resolved: dict[str, Path] = resolve_release_package_map(
            side_dir, variant_id=variant_id, dest_root=dest_root
        )
        return resolved
    except AmbiguousVariantSelectionError as exc:
        # The engine states the fact and carries the ids but names no flag,
        # because only here is it known *which side* this package is --
        # and a bare `--variant ID` would apply to both sides, so it is not
        # safe advice when only one side is ambiguous (Codex review, PR
        # #1184, second round). Append the side-correct example, and only
        # when there is a real id to name: a package declaring zero
        # variants has nothing to select.
        example = (
            f" (e.g. --variant {side}={exc.variant_ids[0]})" if exc.variant_ids else ""
        )
        raise click.UsageError(f"{exc}{example}") from exc
    except (KeyError, ValueError, OSError, SnapshotError) as exc:
        # Ambiguous variant, a same-key collision (ValueError), a missing/
        # unreadable ref (OSError), an object absent from objects/ entirely
        # (KeyError, DirectoryObjectStore.get's own error on a truncated
        # package; CodeRabbit review), or a corrupt document (SnapshotError)
        # are all usage errors, translated like `_build_match_map`'s own
        # `AmbiguousLibraryMatchError` (Codex review).
        raise click.UsageError(str(exc)) from exc
