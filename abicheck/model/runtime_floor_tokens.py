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

"""Validation of the ``runtime_floors`` values that are tokens, not versions.

Most ``runtime_floors`` keys (``GLIBC``, ``GLIBCXX``, ...) carry a dotted
numeric floor, which ``environment_matrix._parse_runtime_floors`` checks
itself. Two carry something else, and each has a vocabulary a detector reads,
so a value outside it would load fine and then silently disable the check
the config believes it enabled:

* ``WHEEL_ARCH`` -- an architecture token, validated against
  :data:`~abicheck.model.wheel_arch_claims.WHEEL_ARCH_CLAIMS`, the one list
  ``diff_wheel_deployment.check_wheel_tag_architecture_mismatch`` also reads
  (Codex review, PR #1221, Finding 1: a typo such as ``"x86-64"`` used to
  load and then report nothing).
* ``NUMPY_REQUIREMENT`` -- the wheel's declared ``numpy`` requirement (G26),
  a PEP 440 specifier set such as ``">=1.23.5,<3"``, or ``""`` when the
  wheel declares no numpy floor. Present only when the requirement is
  *known*: an absent key means "not declared to abicheck", which the NumPy
  metadata check must not read as "the wheel declares no numpy"
  (``checker._numpy_metadata_contract_findings``).
"""

from __future__ import annotations

from .wheel_arch_claims import WHEEL_ARCH_CLAIMS

__all__ = ["runtime_floor_token_error"]


def runtime_floor_token_error(key: str, value: object) -> str | None:
    """Why *value* is not a valid token for upper-cased *key*, or ``None``.

    ``None`` too for a key with no token vocabulary: the caller validates
    those itself.
    """
    floor = str(value)
    if key == "WHEEL_ARCH" and floor.lower() not in WHEEL_ARCH_CLAIMS:
        return (
            f"'runtime_floors.WHEEL_ARCH' {value!r} is not a recognized "
            f"architecture token; expected one of {sorted(WHEEL_ARCH_CLAIMS)}"
        )
    if key == "NUMPY_REQUIREMENT":
        from packaging.specifiers import InvalidSpecifier, SpecifierSet

        try:
            SpecifierSet(floor)
        except InvalidSpecifier:
            return (
                "'runtime_floors.NUMPY_REQUIREMENT' must be a PEP 440 "
                f"specifier set (e.g. '>=1.23.5,<3', or '' for no floor), got {value!r}"
            )
    return None
