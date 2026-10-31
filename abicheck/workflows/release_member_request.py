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
member's comparison keyword by keyword from a 34-slot positional tuple, and
a field missed at any of the forwarding places was silently dropped for
directory/package comparisons only (#1391, and the ``lang_explicit``/
``exclude_headers``/``env_matrix`` follow-ups each closed one such drop).

Now the release resolves one :class:`ReleaseMemberCompareRequest` -- the
parent -- and each member receives it plus a :class:`MemberDelta`: a frozen
type listing the only fields a member may override (its two operands and
their per-member debug files). :func:`member_request` applies the delta with
:func:`dataclasses.replace`, so every other field is inherited by
construction.

:func:`member_compare_request` then builds the member's typed
:class:`~abicheck.workflows.service_requests.CompareRequest` directly -- the same
request the keyword API builds for the same operands -- with no keyword
round-trip. It consumes every field explicitly, and refuses a field it does
not map rather than dropping it, so a new request field cannot silently
disagree with the identical single-pair comparison.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from ..errors import ValidationError
from .request_inputs import InputSpec
from .service_requests import CompareRequest, ContractDomain

if TYPE_CHECKING:
    from ..compile_context import CompileContext
    from ..environment_matrix import EnvironmentMatrix

__all__ = [
    "MemberDelta",
    "ReleaseMemberCompareRequest",
    "ReleaseMemberRunInputs",
    "member_compare_request",
    "member_request",
]


@dataclass(frozen=True)
class ReleaseMemberCompareRequest:
    """The release's resolved per-pair request -- the parent every member
    inherits from.

    Field names mirror the single-pair comparison's keywords (the public
    ``service.run_compare`` signature), which a test asserts. The four
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
    suppress: Path | tuple[Path, ...] | None = None
    policy: str = "strict_abi"
    policy_file_path: Path | None = None
    scope_to_public_surface: bool = True
    pattern_verdicts: bool = True
    include_dependencies: bool = False
    contract_evaluation: bool = True
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
    #: ADR-067 D5/D6, from `.abicheck.yml`'s `acknowledgments:` block (the
    #: records only when the config was named with --config).
    acknowledgments_path: Path | None = None
    acknowledgment_unacknowledged_additions: str | None = None
    acknowledgment_breaks: str | None = None
    #: :class:`ReleaseMemberRunInputs`' fields: the dependency walk and the
    #: per-side debug roots, applied to every member as the scalar compare
    #: applies them to its one pair.
    follow_deps: bool = False
    dependency_search_paths: tuple[Path, ...] = ()
    ld_library_path: str = ""
    old_debug_roots: tuple[Path, ...] = ()
    new_debug_roots: tuple[Path, ...] = ()


@dataclass(frozen=True)
class ReleaseMemberRunInputs:
    """The run inputs a release hands every member unchanged.

    ``--follow-deps``/``--search-path``/``--ld-library-path`` (or their
    ``deps.*`` config homes) and the directory/detached-file ``--debug-info``
    roots (or ``debug.info``), resolved once by the front end exactly as for a
    single-pair compare. A debug root is a ``.build-id`` tree or a detached
    file looked up per binary, so one root serves every member of its side.
    A debug *package* is not here: the plan unpacks it and resolves each
    member's file (:class:`MemberDelta`'s ``*_pdb_path``).
    """

    follow_deps: bool = False
    dependency_search_paths: tuple[Path, ...] = ()
    ld_library_path: str = ""
    old_debug_roots: tuple[Path, ...] = ()
    new_debug_roots: tuple[Path, ...] = ()

    def request_fields(self) -> dict[str, Any]:
        """These inputs as :class:`ReleaseMemberCompareRequest` keywords."""
        return {f.name: getattr(self, f.name) for f in dataclasses.fields(self)}


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

#: Every request field :func:`member_compare_request` consumes. Checked
#: against the dataclass on each call, so a field added without a mapping is
#: refused loudly rather than dropped from the member's comparison.
_MAPPED_FIELDS = frozenset(
    {
        "old_input",
        "new_input",
        "old_pdb_path",
        "new_pdb_path",
        "old_headers",
        "new_headers",
        "old_includes",
        "new_includes",
        "old_version",
        "new_version",
        "lang",
        "lang_explicit",
        "suppress",
        "policy",
        "policy_file_path",
        "scope_to_public_surface",
        "pattern_verdicts",
        "include_dependencies",
        "contract_evaluation",
        "contract_mode",
        "pack_policy_overrides",
        "pack_internal_namespaces",
        "compile_context",
        "depth",
        "public_header_dirs",
        "collapse_versioned_symbols",
        "project_policy_overrides",
        "env_matrix",
        "exclude_headers",
        "acknowledgments_path",
        "acknowledgment_unacknowledged_additions",
        "acknowledgment_breaks",
        "follow_deps",
        "dependency_search_paths",
        "ld_library_path",
        "old_debug_roots",
        "new_debug_roots",
    }
)


def member_request(
    parent: ReleaseMemberCompareRequest, delta: MemberDelta
) -> ReleaseMemberCompareRequest:
    """*parent* with exactly *delta*'s fields replaced."""
    return dataclasses.replace(
        parent, **{name: getattr(delta, name) for name in _DELTA_FIELDS}
    )


def member_compare_request(request: ReleaseMemberCompareRequest) -> CompareRequest:
    """*request* as the typed :class:`CompareRequest` for one member's pair.

    The mapping, field by field (a both-sides keyword fans out to each
    :class:`InputSpec`; ``None``/empty collections normalise exactly as the
    keyword API's own shim does, so the two routes build equal requests):

    - ``old_input``/``new_input``, ``*_pdb_path``, ``*_headers``,
      ``*_includes``, ``*_version`` -> that side's ``InputSpec``
    - ``include_dependencies``, ``compile_context``, ``public_header_dirs``,
      ``exclude_headers`` -> both sides' ``InputSpec``
    - ``pack_policy_overrides``, ``project_policy_overrides`` -> a tuple of
      pairs, or ``None`` when empty
    - ``scope_to_public_surface`` -> ``CompareRequest.scope_public``
    - ``old_debug_roots``/``new_debug_roots`` -> that side's
      ``InputSpec.debug_roots``
    - the remaining fields carry over under their own names

    ``frontend``, ``debuginfod``,
    ``diagnostic_comparison``, ``severity_preset`` and the other keyword-only
    defaults are not release fields: both routes leave them at the
    ``CompareRequest`` default.
    """
    if request.old_input is None or request.new_input is None:
        raise ValueError(
            "a release parent request has no operands; apply a MemberDelta first"
        )
    unmapped = {f.name for f in dataclasses.fields(request)} - _MAPPED_FIELDS
    if unmapped:
        raise TypeError(
            f"no CompareRequest mapping for {sorted(unmapped)}; add it to "
            "member_compare_request rather than dropping it"
        )
    # Both sides carry the release's shared settings; each side keeps its own
    # operands, debug file, headers, includes and version. Written out per
    # side (as the keyword shim does) so the two routes read the same way.
    return CompareRequest(
        old=InputSpec(
            path=request.old_input,
            headers=tuple(request.old_headers or ()),
            includes=tuple(request.old_includes or ()),
            version=request.old_version,
            pdb=request.old_pdb_path,
            debug_roots=tuple(request.old_debug_roots),
            exclude_headers=tuple(request.exclude_headers or ()),
            include_system_declarations=request.include_dependencies,
            compile=request.compile_context,
            public_header_dirs=tuple(request.public_header_dirs or ()),
        ),
        new=InputSpec(
            path=request.new_input,
            headers=tuple(request.new_headers or ()),
            includes=tuple(request.new_includes or ()),
            version=request.new_version,
            pdb=request.new_pdb_path,
            debug_roots=tuple(request.new_debug_roots),
            exclude_headers=tuple(request.exclude_headers or ()),
            include_system_declarations=request.include_dependencies,
            compile=request.compile_context,
            public_header_dirs=tuple(request.public_header_dirs or ()),
        ),
        lang=request.lang,
        lang_explicit=request.lang_explicit,
        policy=(
            request.policy_file_path
            if request.policy_file_path is not None
            else request.policy
        ),
        suppress=request.suppress,
        scope_public=request.scope_to_public_surface,
        pattern_verdicts=request.pattern_verdicts,
        contract=release_contract_domain(
            request.contract_evaluation, request.contract_mode
        ),
        pack_policy_overrides=(
            tuple(request.pack_policy_overrides.items())
            if request.pack_policy_overrides
            else None
        ),
        pack_internal_namespaces=request.pack_internal_namespaces,
        project_policy_overrides=(
            tuple(request.project_policy_overrides.items())
            if request.project_policy_overrides
            else None
        ),
        depth=request.depth,
        collapse_versioned_symbols=request.collapse_versioned_symbols,
        env_matrix=request.env_matrix,
        acknowledgments=request.acknowledgments_path,
        acknowledgment_unacknowledged_additions=(
            request.acknowledgment_unacknowledged_additions
        ),
        acknowledgment_breaks=request.acknowledgment_breaks,
        follow_deps=request.follow_deps,
        dependency_search_paths=tuple(request.dependency_search_paths),
        ld_library_path=request.ld_library_path,
    )


def release_contract_domain(
    contract_evaluation: bool, contract_mode: str | None
) -> ContractDomain | None:
    """Map the release request's contract pair onto ``CompareRequest.contract``.

    ADR-091 R10: ``contract_evaluation`` was an opt-out the CLI never exposed
    (``--contract`` always evaluates), and the typed ``contract`` value set has
    no way to say "do not evaluate". Refusing ``False`` keeps the removal loud
    rather than silently ignoring the caller's request.
    """
    if not contract_evaluation:
        raise ValidationError(
            "contract_evaluation=False is no longer supported: contract relevance "
            "is always evaluated, as the CLI does; pass contract='all' for the "
            "rollback to judging every entity"
        )
    # A value outside the set is refused by CompareRequest.validation_errors.
    return cast("ContractDomain | None", contract_mode)
