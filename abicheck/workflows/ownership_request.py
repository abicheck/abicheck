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

"""The ownership request a run classifies its declarations under (ADR-075).

Two halves, joined in one place:

* :func:`ownership_request_from_config` -- the project's ``.abicheck.yml``
  ``scope:`` keys (dependencies, ``private_*``, ``dependency_evidence``),
  each root made absolute against the project root. What a front end puts
  on ``InputSpec.ownership``.
* :func:`with_target_roots` -- the target roots, which are always this
  run's ``public_header_dirs`` plus its ``-H`` *directories* (the plan's
  definition; a ``-H`` file and a ``-I`` never are). Folded by
  ``workflows.input_resolution.resolve_input`` itself, so every caller --
  CLI, typed API, a release member -- gets the same roots from the same
  inputs instead of each front end re-deriving them.
"""

from __future__ import annotations

import contextvars
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..model.ownership_rules import DependencyRoots, OwnershipRequest, OwnershipRules

if TYPE_CHECKING:
    from ..model.snapshot import AbiSnapshot

__all__ = [
    "classify_extracted",
    "operand_anchor",
    "ownership_request_from_config",
    "project_ownership_key",
    "project_ownership_scope",
    "with_target_roots",
]

#: The project's ownership request for the enclosing run, when a front end
#: declared one for a whole fan-out (see :func:`project_ownership_scope`).
_PROJECT_OWNERSHIP: contextvars.ContextVar[OwnershipRequest | None] = (
    contextvars.ContextVar("abicheck_project_ownership", default=None)
)


@contextmanager
def project_ownership_scope(request: OwnershipRequest | None) -> Iterator[None]:
    """Classify every snapshot extracted inside this block under *request*.

    For the directory/package release fan-out: one project config states the
    rules for every member, and the fan-out already propagates a copy of the
    calling thread's context into each parallel worker (the mechanism
    ``workflows.crosscheck_ownership`` documents), so each member dump sees
    the same rules without a parameter threaded through five layers. An
    explicit ``InputSpec.ownership`` still wins. Restored on exit.
    """
    token = _PROJECT_OWNERSHIP.set(request)
    try:
        yield
    finally:
        _PROJECT_OWNERSHIP.reset(token)


def ownership_request_from_config(
    cfg: Any, project_root: Path | None
) -> OwnershipRequest | None:
    """The request *cfg*'s ``scope:`` block states, or ``None`` with no config.

    *cfg* is a ``BuildConfig`` (read by attribute, so ``frontends`` callers
    need not import ``buildsource``). Relative roots are the project's, so
    they resolve against the project root the config belongs to
    (``config_paths.project_root_for_config``, applied by the caller), not
    the working directory.
    """
    if cfg is None or project_root is None:
        return None
    root = project_root.resolve()
    rules: OwnershipRules = getattr(cfg, "ownership", None) or OwnershipRules()

    def absolute(spelling: str) -> str:
        return os.path.normpath(os.path.join(str(root), spelling))

    config_roots = tuple(
        absolute(str(d)) for d in getattr(cfg, "public_header_dirs", ()) or ()
    )
    return OwnershipRequest(
        rules=replace(
            rules,
            # ``scope.public_header_dirs`` is a target root (the plan's
            # definition), resolved like every other config root.
            target_roots=(*rules.target_roots, *config_roots),
            dependencies=tuple(
                DependencyRoots(d.name, tuple(absolute(r) for r in d.header_roots))
                for d in rules.dependencies
            ),
        ),
        project_root=str(root),
    )


def operand_anchor(roots: Sequence[str]) -> str | None:
    """The deepest directory every entry of *roots* lies under, or ``None``.

    The anchor one side's roots are recorded relative to when no project
    config supplies one. It is derived from *that side's own* header roots,
    never from where the side sits on disk: ``-H old=rel-1/include -H
    new=rel-2/include`` must record "the public header root" as one rule on
    both sides, and two byte-identical checkouts under different directory
    names must too. ``None`` for no roots, or roots on two drives.
    """
    if not roots:
        return None
    try:
        return os.path.commonpath([os.path.abspath(r) for r in roots])
    except ValueError:  # different drives on Windows
        return None


def with_target_roots(
    request: OwnershipRequest | None,
    headers: Sequence[Path],
    public_header_dirs: Sequence[Path],
) -> OwnershipRequest:
    """*request* (or no configured rules) with this run's target roots.

    ``public_header_dirs`` and each ``-H`` directory, absolute against the
    working directory they were spelled relative to -- the same resolution
    provenance already applies to them. Order-preserving and de-duplicated;
    the recorded form is sorted anyway.
    """
    roots = [str(Path(h).resolve()) for h in headers if Path(h).is_dir()]
    roots += [str(Path(d).resolve()) for d in public_header_dirs]
    base = request or _PROJECT_OWNERSHIP.get() or OwnershipRequest()
    project_root = base.project_root
    if project_root is None:
        # With no project config the roots were recorded absolute, so the two
        # sides of a release comparison (`-H old=... -H new=...`) -- or two
        # byte-identical checkouts under different names -- always
        # fingerprinted as "different ownership rules". Each side is anchored
        # at its own header roots instead, so the same layout is one rule.
        project_root = operand_anchor([*base.rules.target_roots, *roots])
    return replace(
        base,
        project_root=project_root,
        rules=replace(
            base.rules,
            target_roots=tuple(dict.fromkeys([*base.rules.target_roots, *roots])),
        ),
    )


def classify_extracted(
    snapshot: AbiSnapshot,
    request: OwnershipRequest | None,
    headers: Sequence[Path] | None,
    public_header_dirs: Sequence[Path] | None,
) -> None:
    """Stamp a snapshot *this run extracted* (ADR-075 D1/D2).

    Only a snapshot with header-derived declarations is classified; a
    binary- or debug-only one has nothing to own and records no scope. A
    contradictory rule set (one root claimed twice) is a usage error, never
    a partial stamp.
    """
    if not snapshot.from_headers:
        return
    from ..errors import ValidationError
    from ..extract.ownership import OwnershipRuleError
    from ..extract.ownership_stamp import stamp_ownership

    try:
        stamp_ownership(
            snapshot,
            with_target_roots(request, headers or (), public_header_dirs or ()),
        )
    except OwnershipRuleError as exc:
        raise ValidationError(f"invalid ownership rules: {exc}") from exc


def project_ownership_key() -> str:
    """A stable key for the enclosing run's project ownership request, ``""``
    with none -- what ``SurfaceAcquisitionIdentity.ownership`` folds in."""
    request = _PROJECT_OWNERSHIP.get()
    if request is None:
        return ""
    import hashlib

    return hashlib.sha256(repr(request).encode("utf-8")).hexdigest()
