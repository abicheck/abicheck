# Copyright 2026 Nikolay Petrov
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

"""The *finished* tier of the dump cache: a live-extracted operand after
``resolve_input``'s finishing passes (ADR-075 ownership, achieved
``--exclude-header`` stamp and the dependency re-scope it triggers).

Those passes are a deterministic function of the raw snapshot -- which the
whole-snapshot key already pins -- plus the inputs a :class:`Finisher`
folds into its own *material*. Without this tier a warm run reloaded the raw
entry and re-ran them every time (measured on a 28-member MKL release:
~4 s of dependency scoping per side per member, plus ownership).

The hand-off is a context variable rather than a parameter, so the eight
exits of ``_resolve_input_impl`` need no new argument: ``resolve_input``
offers a :class:`Finisher` around its body, and the one
:func:`~.api.cached_run_dump` call that body reaches takes it. A finisher
nobody took leaves ``resolve_input`` to finish the snapshot itself, exactly
as before. Entries live in their own sub-directory (own LRU budget), keyed
by ``sha256(whole-snapshot key, material)``.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ....model import AbiSnapshot

#: Sub-directory of the snapshot cache holding finished entries.
FINISHED_SUBDIR = "finished"


@dataclass
class Finisher:
    """The finishing passes for one ``resolve_input`` call.

    *material* must name **every** input *finish* reads beyond the raw
    snapshot itself; *applied* records whether a cache-aware caller already
    ran (or served) them, so the offering caller does not run them twice.
    """

    material: str
    finish: Callable[[AbiSnapshot], AbiSnapshot]
    applied: bool = field(default=False)

    def key_for(self, whole_snapshot_key: str) -> str:
        """The finished entry's key: the raw entry's key plus *material*."""
        h = hashlib.sha256()
        h.update(b"finished-v1\x00")
        h.update(whole_snapshot_key.encode())
        h.update(b"\x00")
        h.update(self.material.encode("utf-8", "surrogatepass"))
        return h.hexdigest()


_PENDING: ContextVar[Finisher | None] = ContextVar(
    "abicheck_pending_snapshot_finisher", default=None
)


@contextmanager
def offering(finisher: Finisher | None) -> Iterator[None]:
    """Offer *finisher* to the cache for the duration of the block."""
    token = _PENDING.set(finisher)
    try:
        yield
    finally:
        _PENDING.reset(token)


def take_pending() -> Finisher | None:
    """Claim the offered finisher, if any. One claim per offer: a nested
    lookup in the same block (none today) must not finish twice."""
    finisher = _PENDING.get()
    if finisher is not None:
        _PENDING.set(None)
    return finisher


def _path_facts(paths: Iterable[object] | None) -> list[tuple[str, str, bool, bool]]:
    """Each path as spelled, resolved, and whether it exists / is a
    directory -- everything root matching and ownership read off a root."""
    out = []
    for p in paths or ():
        path = Path(str(p))
        out.append((str(p), str(path.resolve()), path.exists(), path.is_dir()))
    return out


def finish_material(
    ownership: object | None,
    headers: Sequence[Path] | None,
    exclude_headers: Sequence[str],
    scope_inputs: Mapping[str, Any],
) -> str:
    """Every input ``resolve_input``'s finishing reads beyond the raw snapshot.

    * ownership (``classify_extracted``): the explicit request, the enclosing
      run's project request, the ``-H`` and ``public_header_dirs`` roots as
      resolved on disk, and the working directory a root-less request
      anchors at;
    * the achieved ``--exclude-header`` stamp: the requested patterns and the
      ones that matched the *unfiltered* ``-H`` walk (a directory listing the
      raw key does not see -- an excluded header's content is never hashed);
      the declaration-matched half is a function of the raw snapshot;
    * the re-scope roots: ``-H``, ``public_headers``, ``public_header_dirs``
      (a dump manifest never reaches here: no finisher is offered for one).
    """
    from ....extract.header_exclusions import matched_exclusion_patterns
    from ...ownership_request import project_ownership_key

    return repr(
        (
            repr(ownership),
            project_ownership_key(),
            os.getcwd(),
            tuple(exclude_headers),
            matched_exclusion_patterns(list(headers or []), exclude_headers),
            _path_facts(headers),
            _path_facts(scope_inputs.get("public_headers")),
            _path_facts(scope_inputs.get("public_header_dirs")),
        )
    )
