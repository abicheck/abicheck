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

"""One release member's comparison request, derived from the parent's.

Design-hardening Phase 2 (F2, ``docs/contribute/plans/design-hardening-
from-defect-families.md``). The release fan-out used to rebuild each
member's ``service.run_compare`` call keyword by keyword from a 34-slot
positional tuple. Every new request field had to be added to the tuple, to
two function signatures and to the forwarding call, and a field missed at
any of those four places was silently dropped for directory/package
comparisons only (#1391, and the ``lang_explicit``/``exclude_headers``/
``env_matrix`` follow-ups each closed one such drop).

Now the release resolves one :class:`ReleaseMemberCompareRequest` -- the
parent -- and each member receives it plus a :class:`MemberDelta`: a frozen
type listing the only fields a member may override (its two operands and
their per-member debug files). :func:`member_request` applies the delta with
:func:`dataclasses.replace`, so every other field is inherited by
construction, and :func:`run_compare_kwargs` forwards every field by
iterating the dataclass rather than naming them. A new field therefore
reaches every member without a second edit, and
``tests/test_release_member_request.py`` proves it with a synthetic field.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..compile_context import CompileContext
    from ..environment_matrix import EnvironmentMatrix

__all__ = [
    "MemberDelta",
    "ReleaseMemberCompareRequest",
    "member_request",
    "run_compare_kwargs",
]


@dataclass(frozen=True)
class ReleaseMemberCompareRequest:
    """The release's resolved per-pair request -- the parent every member
    inherits from.

    Each field name is a ``service.run_compare`` keyword, which
    :func:`run_compare_kwargs` relies on (and a test asserts). The four
    :class:`MemberDelta` slots stay ``None`` on the parent: only a member
    has operands.
    """

    old_input: Path | None = None
    new_input: Path | None = None
    old_pdb_path: Path | None = None
    new_pdb_path: Path | None = None
    old_headers: list[Path] = field(default_factory=list)
    new_headers: list[Path] = field(default_factory=list)
    old_includes: list[Path] = field(default_factory=list)
    new_includes: list[Path] = field(default_factory=list)
    old_version: str = ""
    new_version: str = ""
    lang: str = "c++"
    lang_explicit: bool = False
    suppress: Path | None = None
    policy: str = "strict_abi"
    policy_file_path: Path | None = None
    scope_to_public_surface: bool = True
    pattern_verdicts: bool = True
    include_dependencies: bool = False
    contract_evaluation: bool = False
    contract_mode: str | None = None
    pack_policy_overrides: dict[Any, Any] | None = None
    pack_internal_namespaces: tuple[str, ...] | None = None
    compile_context: CompileContext | None = None
    depth: str | None = None
    public_header_dirs: list[Path] | None = None
    collapse_versioned_symbols: bool = False
    project_policy_overrides: dict[Any, Any] | None = None
    env_matrix: EnvironmentMatrix | None = None
    exclude_headers: tuple[str, ...] = ()
    #: ADR-067 D5/D6, from `.abicheck.yml`'s `acknowledgment:` block (the
    #: records only when the config was named with --config).
    acknowledgments_path: Path | None = None
    acknowledgment_unacknowledged_additions: str | None = None


@dataclass(frozen=True)
class MemberDelta:
    """The only fields a release member may set differently from its parent.

    Adding a field here is a reviewed decision that the member, not the
    release, owns that value; everything absent is inherited.
    """

    old_input: Path
    new_input: Path
    old_pdb_path: Path | None = None
    new_pdb_path: Path | None = None


_REQUEST_FIELDS = frozenset(
    f.name for f in dataclasses.fields(ReleaseMemberCompareRequest)
)
_DELTA_FIELDS = tuple(f.name for f in dataclasses.fields(MemberDelta))
if not set(_DELTA_FIELDS) <= _REQUEST_FIELDS:  # pragma: no cover - import-time contract
    raise TypeError(
        "MemberDelta names a field ReleaseMemberCompareRequest does not have"
    )


def member_request(
    parent: ReleaseMemberCompareRequest, delta: MemberDelta
) -> ReleaseMemberCompareRequest:
    """*parent* with exactly *delta*'s fields replaced."""
    return dataclasses.replace(
        parent, **{name: getattr(delta, name) for name in _DELTA_FIELDS}
    )


def run_compare_kwargs(request: ReleaseMemberCompareRequest) -> dict[str, Any]:
    """Every field of *request*, as ``service.run_compare`` keywords.

    Iterates the dataclass (shallowly -- ``dataclasses.asdict`` would deep-copy
    the compile context and env matrix), so no field can be left out. Raises
    on a request that is still a parent: a comparison needs both operands.
    """
    if request.old_input is None or request.new_input is None:
        raise ValueError(
            "a release parent request has no operands; apply a MemberDelta first"
        )
    return {f.name: getattr(request, f.name) for f in dataclasses.fields(request)}
