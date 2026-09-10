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


"""Is a ``--sources``/``--build-info`` path *raw evidence* or a prebuilt pack?

One owner for a question three call sites were answering separately, and one
of them was answering it only for the two-sided path. The distinction
matters twice over:

* **Routing.** A raw checkout/build dir is collected from inline at
  ``--depth``; a prebuilt ``collect`` pack (either kind) is loaded
  out-of-band instead.
* **Liveness.** A run given raw evidence performs a live extraction even
  when its *artifact* operand is an already-serialized snapshot -- so the
  ``--depth build``/``--depth source`` evidence-contract floor applies to
  it. ``compare``'s two-sided path had encoded that as
  ``old_fmt is not None or old_had_raw_evidence``; the one-sided audit
  tested the operand's path alone and so exempted itself, reporting a clean
  exit 0 where the equivalent two-sided invocation exited 7 (Codex review,
  P1).

Deliberately a sink module -- it imports the two pack predicates and nothing
imports it back, which is what lets it depend on both ``pack_shape`` and
``inputs_pack`` without closing the ``inline -> pack_shape -> inputs_pack ->
inline`` cycle ``pack_shape``'s own docstring warns about.
"""

from __future__ import annotations

from pathlib import Path

from .inputs_pack import is_inputs_pack_dir
from .pack_shape import is_pack_dir

__all__ = ["is_raw_evidence_input", "any_raw_evidence_input"]


def is_raw_evidence_input(path: Path | None) -> bool:
    """True when *path* names a raw source checkout or build dir/file.

    ``False`` for ``None`` (nothing given) and for either kind of prebuilt
    pack (a ``BuildSourcePack`` directory or a build-emitted Flow-2
    ``abicheck_inputs/`` pack), which are loaded rather than collected from.
    Both predicates validate the manifest's *content*, so a raw checkout that
    happens to contain a ``manifest.json`` still reads as raw evidence.
    """
    if path is None:
        return False
    return not (is_pack_dir(path) or is_inputs_pack_dir(path))


def any_raw_evidence_input(*paths: Path | None) -> bool:
    """True when any of *paths* is raw evidence by :func:`is_raw_evidence_input`."""
    return any(is_raw_evidence_input(p) for p in paths)
