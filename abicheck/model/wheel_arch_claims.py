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

"""Canonical vocabulary of wheel-tag architecture claim tokens (G27).

A ``WHEEL_ARCH`` value -- whether produced by
``extract.wheel_tags.parse_wheel_architecture_claim`` or hand-written into an
``.abicheck.yml``'s ``deployment.runtime_floors.WHEEL_ARCH`` key -- is only
meaningful if :func:`abicheck.diff_wheel_deployment.
check_wheel_tag_architecture_mismatch` actually recognizes it: an unrecognized
claim isn't rejected there, it's silently treated as "nothing to check" (see
that function's own docstring), which would otherwise let a misspelled or
invalid claim (``x86-64`` for ``x86_64``) disable the hard
architecture-mismatch gate a strict config believes it enabled (Codex review,
PR #1221, Finding 1).

This module exists so that detector (which architecture tokens it can check)
and config parser (which tokens it accepts) cannot independently drift on
what's "supported": both `diff_wheel_deployment.py` (``compare`` layer) and
`environment_matrix.py` (``model`` layer, which may not import ``compare`` --
see ``AGENTS.md``'s "Task routing and dependency direction" table) read the
same frozenset from here, and `diff_wheel_deployment.py` asserts at import
time that its own per-claim dicts' keys union to exactly this set, so a new
architecture token added to only one of those dicts fails immediately rather
than silently narrowing what a strict config's validation accepts.
"""

from __future__ import annotations

#: Every wheel-tag architecture claim `diff_wheel_deployment.py` can check
#: (the union of `_ARCH_CLAIM_TO_ELF_MACHINE`'s and
#: `_ARCH_CLAIM_TO_MACHO_CPU_TYPE`'s keys there) -- and therefore every
#: ``WHEEL_ARCH`` value `environment_matrix.py`'s config parser accepts.
WHEEL_ARCH_CLAIMS: frozenset[str] = frozenset(
    {
        "x86_64",
        "aarch64",
        "i686",
        "armv7l",
        "ppc64le",
        "ppc64",
        "s390x",
        "riscv64",
        "loongarch64",
        "arm64",
    }
)
