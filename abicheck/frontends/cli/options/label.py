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

"""The per-side version label, ``--label`` (ADR-091 D5 R1).

``compare``, ``dump`` and ``project history`` name the label of each side with
``--label``; the retired ``--version`` is a hidden alias (see
:mod:`.renamed`). Each command keeps its own dest and default, so the callback
parameters did not move. Root ``abicheck --version`` is the tool version and is
not this option.
"""

from __future__ import annotations

from .params import SIDED_STR_PARAM
from .renamed import Decorator, Legacy, renamed_option


def compare_label_option(func: Decorator) -> Decorator:
    """``compare``'s side-aware label: ``--label [old=|new=]LABEL``, repeatable."""
    return renamed_option(
        "--label",
        "version",
        legacy=(Legacy("--version", "--label", multiple=True),),
        multiple=True,
        type=SIDED_STR_PARAM,
        help="Version label used when an input is a bare .so file. Scope to one "
        "side with an 'old='/'new=' prefix, repeating the flag per side (e.g. "
        "--label old=1.0 --label new=2.0); a bare value applies to both. "
        "Defaults: old side 'old', new side 'new'.",
    )(func)


def dump_label_option(func: Decorator) -> Decorator:
    """``dump``'s single label, embedded in the snapshot."""
    return renamed_option(
        "--label",
        "version",
        legacy=(Legacy("--version", "--label"),),
        default="unknown",
        show_default=True,
        help="Library version string to embed in snapshot.",
    )(func)


def history_label_option(func: Decorator) -> Decorator:
    """``project history``'s per-snapshot label, one per SNAPSHOT, in order."""
    return renamed_option(
        "--label",
        "versions",
        legacy=(Legacy("--version", "--label", multiple=True),),
        multiple=True,
        metavar="LABEL",
        help=(
            "Explicit release label for one SNAPSHOT, in the same order as the "
            "SNAPSHOTS arguments (repeatable — pass one per snapshot, or omit "
            "entirely). Without this, each snapshot's own recorded "
            "AbiSnapshot.version is used as its release label."
        ),
    )(func)
